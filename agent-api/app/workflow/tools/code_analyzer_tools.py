"""Code Analyzer tools exposed as LangChain StructuredTools for the ReAct agent.

When a ``codeAnalyzer`` node is connected to an ``agent`` node in the
workflow graph, the executor calls :func:`build_code_analyzer_tools` to
create 5 LangChain-compatible tool instances covering the full investigation
lifecycle:

1. ``code_investigate``          — primary entry: search + trace + history
2. ``code_explain_flow``         — structural: trace + callers + imports + owners
3. ``code_analyze_change``       — temporal: diff + deploys + blast radius
4. ``code_get_runtime_evidence`` — runtime: stack trace parsing + anomaly correlation
5. ``code_finalize_incident``    — closure: RCA record + remediation + async learning

All 18 original capabilities are preserved as private ``_methods`` on three
internal classes:

- :class:`CodeInvestigationRuntime` — session cache, orchestration, public tools
- :class:`CodeMemoryStore`          — investigation_memory, rca_history
- :class:`CodeGraphStore`           — code_chunks, code_calls, code_imports, owners

Evidence grading uses a categorical scale (never numeric) with an immutable
priority hierarchy that adaptive learning cannot override.

Phase 1 MVP (this file): stable retrieval + memory.
Phase 2 (later): reinforcement, incident clustering, investigation scoring.
Phase 3 (later): time decay, era weighting, topology divergence guards, runtime traces.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Immutable evidence priority hierarchy
# ---------------------------------------------------------------------------

EVIDENCE_PRIORITY: List[str] = [
    "speculative",            # 0 — semantic similarity only
    "inferred",               # 1 — static call graph present
    "correlated",             # 2 — semantic + graph aligned
    "historical_rca",         # 3 — matches 2+ prior incidents
    "deployment_correlated",  # 4 — error onset aligned with a deploy
    "runtime-confirmed",      # 5 — runtime trace or parsed stack frame
    "historically-confirmed", # 6 — runtime + historical agreement (highest composite)
]

EVIDENCE_GRADES: Dict[str, str] = {
    "speculative":            "Semantic similarity only — no structural or runtime corroboration.",
    "inferred":               "Supported by static call graph analysis.",
    "correlated":             "Semantic + structural evidence aligned.",
    "historical_rca":         "Matches 2+ prior confirmed RCAs.",
    "deployment_correlated":  "Error onset correlates with a specific deployment.",
    "runtime-confirmed":      "Corroborated by runtime trace or parsed stack frame.",
    "historically-confirmed": "Runtime-confirmed + historical RCA agreement.",
}

PYTHON_FRAME_RE = re.compile(
    r'File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+(\w+)'
)
NODE_FRAME_RE = re.compile(
    r'at\s+(\w+)\s+\(([^:)]+):(\d+):\d+\)'
)

# ---------------------------------------------------------------------------
# Investigation strategy classification (multi-strategy, weighted)
# ---------------------------------------------------------------------------

STRATEGY_MAP: Dict[str, List[str]] = {
    "incident":      ["_search", "_trace_flow", "_get_recent_changes", "_diff_summary"],
    "temporal":      ["_diff_summary", "_get_deployment_timeline", "_get_recent_changes"],
    "architectural": ["_trace_flow", "_search", "_get_function"],
    "ownership":     ["_get_owners", "_get_imports", "_search"],
    "dependency":    ["_get_imports", "_trace_flow", "_get_owners"],
    "general":       ["_search", "_get_function"],
}


def _classify_investigation(query: str) -> List[Dict[str, float]]:
    """Return weighted list of investigation strategy types for the query."""
    q = query.lower()
    strategies: List[Dict[str, float]] = []

    if any(w in q for w in ["error", "exception", "failing", "broken", "crash", "500", "null",
                              "traceback", "stacktrace", "stack trace", "caused by"]):
        strategies.append({"type": "incident", "weight": 0.9})
    if any(w in q for w in ["what changed", "after", "since", "deploy", "regression",
                              "recent", "yesterday", "this morning", "release", "rollback"]):
        strategies.append({"type": "temporal", "weight": 0.8})
    if any(w in q for w in ["how does", "explain", "trace", "flow", "walkthrough",
                              "understand", "architecture", "design"]):
        strategies.append({"type": "architectural", "weight": 0.7})
    if any(w in q for w in ["import", "depends", "breaks", "blast radius", "downstream",
                              "impact", "who uses", "consumers of"]):
        strategies.append({"type": "dependency", "weight": 0.7})
    if any(w in q for w in ["who owns", "team", "escalate", "responsible", "contact",
                              "owner", "maintainer"]):
        strategies.append({"type": "ownership", "weight": 0.7})

    return strategies or [{"type": "general", "weight": 0.5}]


def _evidence_rank(grade: str) -> int:
    try:
        return EVIDENCE_PRIORITY.index(grade)
    except ValueError:
        return 0


def _grade_evidence(
    has_semantic: bool,
    has_call_graph: bool,
    has_runtime_trace: bool,
    historical_confirmation_count: int = 0,
    has_deployment_correlation: bool = False,
) -> str:
    """Return the highest achievable evidence grade from available signals.

    Immutable hierarchy: runtime > deployment > structural > historical > semantic.
    No adaptive weighting can override this ordering.
    """
    if has_runtime_trace:
        if historical_confirmation_count >= 2:
            return "historically-confirmed"
        return "runtime-confirmed"
    if has_deployment_correlation:
        return "deployment_correlated"
    if has_semantic and has_call_graph:
        return "correlated"
    if has_call_graph:
        return "inferred"
    if historical_confirmation_count >= 2:
        return "historical_rca"
    return "speculative"


def _merge_grades(grades: List[str]) -> str:
    """Return the highest-priority grade from a list. Never downgrades."""
    if not grades:
        return "speculative"
    return max(grades, key=_evidence_rank)


# ---------------------------------------------------------------------------
# Pydantic input schemas (5 public tools)
# ---------------------------------------------------------------------------

class InvestigateInput(BaseModel):
    query: str = PydanticField(description="What you want to understand or debug.")
    repo: str = PydanticField(description="Repository name to investigate.")
    depth: Literal["quick", "standard", "deep"] = PydanticField(
        default="standard",
        description=(
            "Investigation depth: 'quick'=2 sub-tools (simple lookups), "
            "'standard'=4-5 sub-tools (default), 'deep'=full strategy sequence."
        ),
    )
    investigation_type: Optional[str] = PydanticField(
        default=None,
        description="Override auto-classification: incident|temporal|architectural|dependency|ownership|general",
    )


class ExplainFlowInput(BaseModel):
    entry_function: str = PydanticField(description="Function name to trace from.")
    repo: str = PydanticField(description="Repository name.")
    max_depth: int = PydanticField(default=2, ge=1, le=3, description="Call graph depth (1-3).")
    include_owners: bool = PydanticField(default=True, description="Include CODEOWNERS lookup.")
    include_imports: bool = PydanticField(default=True, description="Include import dependency analysis.")


class AnalyzeChangeInput(BaseModel):
    repo: str = PydanticField(description="Repository name.")
    since_hours: int = PydanticField(default=24, ge=1, le=168, description="How many hours back to scan.")
    path_filter: Optional[str] = PydanticField(default=None, description="Optional subdirectory filter (e.g. 'auth/').")
    include_blast_radius: bool = PydanticField(default=True, description="Include imported-by analysis for changed files.")


class RuntimeEvidenceInput(BaseModel):
    repo: str = PydanticField(description="Repository name.")
    query: str = PydanticField(
        description="Error message, stack trace, or function name to find runtime evidence for."
    )
    since_hours: int = PydanticField(default=2, ge=1, le=24, description="Time window for deployment correlation.")


class FinalizeIncidentInput(BaseModel):
    repo: str = PydanticField(description="Repository name.")
    root_cause_description: str = PydanticField(description="Brief description of the root cause.")
    root_cause_functions: List[str] = PydanticField(description="Function names identified as root cause.")
    resolution: str = PydanticField(description="How the incident was/will be resolved (1-3 sentences).")
    error_signature: Optional[str] = PydanticField(
        default=None,
        description="Normalized error string (strip variable IDs before passing).",
    )
    incident_id: Optional[str] = PydanticField(default=None, description="Optional alert/ticket ID.")
    execution_id: Optional[str] = PydanticField(default=None, description="Execution ID for audit trail.")


# ---------------------------------------------------------------------------
# CodeGraphStore — graph/code reads (Postgres data, Python logic)
# ---------------------------------------------------------------------------

class CodeGraphStore:
    """Handles all graph and code data queries.

    All business logic (CTE construction, ranking, trace parsing) lives here
    in Python. Postgres provides storage and pgvector ANN search only.
    """

    def __init__(self, repos: List[Dict[str, str]]) -> None:
        self._repos = repos
        self._repo_path_map: Dict[str, str] = {
            r["name"]: r["path"] for r in repos if "name" in r and "path" in r
        }

    def _repos_root(self) -> str:
        return os.getenv("REPOS_BASE_PATH", "/tmp/indexed_repos")

    async def _search(
        self,
        query: str,
        repo: Optional[str] = None,
        limit: int = 5,
        language: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Semantic search. Returns (results, evidence_source)."""
        from app.mcp.tools.code_tools import search_code
        results = await search_code(query=query, repo=repo, limit=limit, language=language)
        # Strip body from search results — agent uses _get_function for bodies
        for r in results:
            r.pop("body", None)
        return results, "_search"

    async def _get_function(
        self,
        name: str,
        repo: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Fetch full function body. Returns (result, evidence_source)."""
        from app.mcp.tools.code_tools import get_function
        result = await get_function(name=name, repo=repo)
        # Cap body length for token efficiency
        if isinstance(result, dict) and "body" in result:
            result["body"] = result["body"][:3000]
        return result, "_get_function"

    async def _get_callers(
        self,
        function_name: str,
        repo: Optional[str] = None,
        limit: int = 10,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Find all callers of a function. Returns (results, evidence_source)."""
        from app.mcp.tools.code_tools import get_callers
        results = await get_callers(function_name=function_name, repo=repo, limit=limit)
        return results, "_get_callers"

    async def _get_recent_changes(
        self,
        file_path: str,
        repo: str,
        days: int = 7,
    ) -> Tuple[Dict[str, Any], str]:
        """Recent git log for a file. Returns (result, evidence_source)."""
        from app.mcp.tools.code_tools import get_recent_changes
        result = await get_recent_changes(file_path=file_path, repo=repo, days=days)
        return result, "_get_recent_changes"

    async def _get_file_context(
        self,
        file_path: str,
        repo: str,
        line_start: int,
        line_end: int,
    ) -> Tuple[Dict[str, Any], str]:
        """Read specific lines from a file. Returns (result, evidence_source)."""
        from app.mcp.tools.code_tools import get_file_context
        result = await get_file_context(
            file_path=file_path, repo=repo,
            line_start=line_start, line_end=line_end,
        )
        return result, "_get_file_context"

    async def _trace_flow(
        self,
        entry_function: str,
        repo: str,
        max_depth: int = 2,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Bounded recursive call graph traversal.

        Uses a CTE with cycle detection, utility namespace filter, and LIMIT 40.
        Returns (nodes, evidence_source).
        """
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        WITH RECURSIVE call_tree(callee_name, callee_file, depth, path) AS (
                            SELECT callee_name, callee_file, 1, ARRAY[caller_name]::text[]
                              FROM code_calls
                             WHERE caller_name = :fn
                               AND caller_repo  = :repo
                               AND callee_file NOT SIMILAR TO
                                   '%(logging|os|sys|datetime|typing|abc|__init__|builtins)%'
                            UNION ALL
                            SELECT c.callee_name, c.callee_file, ct.depth + 1,
                                   ct.path || c.caller_name
                              FROM code_calls c
                              JOIN call_tree ct ON c.caller_name = ct.callee_name
                             WHERE ct.depth < :max_depth
                               AND NOT (c.caller_name = ANY(ct.path))
                               AND c.caller_repo = :repo
                               AND c.callee_file NOT SIMILAR TO
                                   '%(logging|os|sys|datetime|typing|abc|__init__|builtins)%'
                        )
                        SELECT DISTINCT callee_name, callee_file, MIN(depth) AS depth
                          FROM call_tree
                         GROUP BY callee_name, callee_file
                         ORDER BY depth, callee_name
                         LIMIT 40
                    """),
                    {"fn": entry_function, "repo": repo, "max_depth": max_depth},
                )
                nodes = [
                    {"name": r.callee_name, "file": r.callee_file, "depth": r.depth}
                    for r in result
                ]
            return nodes, "_trace_flow"
        except Exception as exc:
            logger.debug("_trace_flow failed for %s/%s: %s", repo, entry_function, exc)
            return [], "_trace_flow"

    async def _diff_summary(
        self,
        repo: str,
        since_hours: int = 24,
        path_filter: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """Git log change timeline for incident window. Returns (result, source)."""
        local_path = self._repo_path_map.get(repo)
        if not local_path:
            return {"error": f"Repo '{repo}' path not configured"}, "_diff_summary"

        def _run() -> Dict[str, Any]:
            cmd = [
                "git", "-C", local_path,
                "log", f"--since={since_hours} hours ago",
                "--name-only",
                "--pretty=format:%H|%ae|%ai|%s",
                "--no-merges",
            ]
            if path_filter:
                cmd += ["--", path_filter]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                commits: List[Dict[str, Any]] = []
                current: Optional[Dict[str, Any]] = None
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        if current:
                            commits.append(current)
                            current = None
                    elif "|" in line and current is None:
                        parts = line.split("|", 3)
                        if len(parts) == 4:
                            current = {
                                "hash": parts[0][:12],
                                "author": parts[1],
                                "timestamp": parts[2],
                                "message": parts[3],
                                "files": [],
                            }
                    elif current is not None:
                        current["files"].append(line)
                if current:
                    commits.append(current)
                return {"repo": repo, "since_hours": since_hours, "commits": commits[:50]}
            except subprocess.TimeoutExpired:
                return {"error": "git log timed out"}
            except Exception as exc:
                return {"error": str(exc)}

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run)
        return result, "_diff_summary"

    async def _get_deployment_timeline(
        self,
        repo: str,
        since_hours: int = 72,
        environment: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Deployments within a time window. Returns (deployments, source)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        try:
            async with AsyncSessionLocal() as session:
                params: Dict[str, Any] = {"rn": repo, "cutoff": cutoff}
                extra = "AND environment = :env" if environment else ""
                if environment:
                    params["env"] = environment
                result = await session.execute(
                    text(f"""
                        SELECT id, repo_name, commit_sha, deployed_at,
                               environment, deployed_by, tag, notes
                          FROM deployments
                         WHERE repo_name = :rn
                           AND deployed_at >= :cutoff
                           {extra}
                         ORDER BY deployed_at DESC
                         LIMIT 20
                    """),
                    params,
                )
                deploys = [
                    {
                        "id": r.id,
                        "repo": r.repo_name,
                        "commit_sha": r.commit_sha,
                        "deployed_at": r.deployed_at.isoformat() if r.deployed_at else None,
                        "environment": r.environment,
                        "deployed_by": r.deployed_by,
                        "tag": r.tag,
                        "notes": r.notes,
                    }
                    for r in result
                ]
            return deploys, "_get_deployment_timeline"
        except Exception:
            return [], "_get_deployment_timeline"

    async def _get_imports(
        self,
        file_path: str,
        repo: str,
        direction: Literal["imports", "imported_by"] = "imports",
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Static import graph lookup. Returns (rows, source)."""
        try:
            async with AsyncSessionLocal() as session:
                if direction == "imports":
                    result = await session.execute(
                        text("""
                            SELECT from_module, import_name, is_relative
                              FROM code_imports
                             WHERE repo_name = :rn AND file_path = :fp
                             ORDER BY from_module, import_name
                             LIMIT 100
                        """),
                        {"rn": repo, "fp": file_path},
                    )
                    rows = [
                        {"from_module": r.from_module, "import_name": r.import_name,
                         "is_relative": r.is_relative}
                        for r in result
                    ]
                else:  # imported_by
                    result = await session.execute(
                        text("""
                            SELECT file_path, import_name
                              FROM code_imports
                             WHERE repo_name = :rn
                               AND (from_module = :module OR from_module LIKE :module_prefix)
                             ORDER BY file_path
                             LIMIT 100
                        """),
                        {
                            "rn": repo,
                            "module": file_path.replace("/", ".").replace(".py", ""),
                            "module_prefix": file_path.replace("/", ".").replace(".py", "") + "%",
                        },
                    )
                    rows = [
                        {"file_path": r.file_path, "import_name": r.import_name}
                        for r in result
                    ]
            return rows, "_get_imports"
        except Exception:
            return [], "_get_imports"

    async def _get_owners(
        self,
        file_path: str,
        repo: str,
    ) -> Tuple[Dict[str, Any], str]:
        """CODEOWNERS fnmatch lookup. Returns (owner_info, source)."""
        domain = file_path.split("/")[0] if "/" in file_path else ""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT path_pattern, owners
                          FROM code_owners
                         WHERE repo_name = :rn
                         ORDER BY LENGTH(path_pattern) DESC
                    """),
                    {"rn": repo},
                )
                for row in result:
                    if fnmatch.fnmatch(file_path, row.path_pattern):
                        owners = row.owners if isinstance(row.owners, list) else []
                        return (
                            {"file": file_path, "owners": owners, "domain": domain},
                            "_get_owners",
                        )
        except Exception:
            pass
        return {"file": file_path, "owners": [], "domain": domain}, "_get_owners"

    def _parse_stack_frames(self, text_input: str) -> List[Dict[str, str]]:
        """Extract function/file frames from a Python or Node.js stack trace."""
        frames: List[Dict[str, str]] = []
        for m in PYTHON_FRAME_RE.finditer(text_input):
            frames.append({
                "file": m.group(1),
                "line": m.group(2),
                "function": m.group(3),
                "source": "python_traceback",
            })
        for m in NODE_FRAME_RE.finditer(text_input):
            frames.append({
                "function": m.group(1),
                "file": m.group(2),
                "line": m.group(3),
                "source": "node_traceback",
            })
        return frames

    async def _match_frames_to_chunks(
        self,
        frames: List[Dict[str, str]],
        repo: str,
    ) -> List[Dict[str, Any]]:
        """Match stack frame function names against code_chunks."""
        matches: List[Dict[str, Any]] = []
        names = list({f["function"] for f in frames})
        if not names:
            return []
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT name, file_path, signature, line_start, chunk_type
                          FROM code_chunks
                         WHERE repo_name = :rn
                           AND name = ANY(:names)
                         ORDER BY indexed_at DESC
                    """),
                    {"rn": repo, "names": names},
                )
                matches = [
                    {
                        "name": r.name,
                        "file_path": r.file_path,
                        "signature": r.signature,
                        "line_start": r.line_start,
                        "evidence_grade": "runtime-confirmed",
                        "evidence_sources": ["stack_trace_frame"],
                    }
                    for r in result
                ]
        except Exception as exc:
            logger.debug("_match_frames_to_chunks failed: %s", exc)
        return matches


# ---------------------------------------------------------------------------
# CodeMemoryStore — investigation_memory + rca_history (Phase 1: no learning)
# ---------------------------------------------------------------------------

class CodeMemoryStore:
    """Persistent memory layer for investigation findings and RCA history.

    Phase 1: stores findings and RCAs with no reinforcement weights.
    Phase 2 (future): add resolution_weight, investigation_scores, clusters.
    """

    async def ensure_tables(self) -> None:
        """Create required tables if they don't exist."""
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS investigation_memory (
                    id            SERIAL PRIMARY KEY,
                    repo_name     VARCHAR(255) NOT NULL,
                    function_name VARCHAR(500) NOT NULL,
                    file_path     VARCHAR(1000),
                    entity_id     VARCHAR(16),
                    summary       TEXT,
                    tags          JSONB DEFAULT '[]',
                    access_count  INTEGER DEFAULT 1,
                    last_accessed TIMESTAMPTZ DEFAULT NOW(),
                    created_at    TIMESTAMPTZ DEFAULT NOW(),
                    status        VARCHAR(20) DEFAULT 'active',
                    UNIQUE (repo_name, function_name)
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS rca_history (
                    id                    SERIAL PRIMARY KEY,
                    repo_name             VARCHAR(255),
                    error_signature       VARCHAR(500),
                    root_cause_functions  JSONB,
                    root_cause_entity_ids JSONB,
                    contributing_files    JSONB,
                    resolution_summary    TEXT,
                    sub_tool_trace        JSONB,
                    evidence_grade        VARCHAR(30) DEFAULT 'inferred',
                    confirmation_count    INTEGER DEFAULT 1,
                    integrity_flag        VARCHAR(50),
                    is_novel_pattern      BOOLEAN DEFAULT FALSE,
                    remediation_steps     JSONB,
                    remediation_grade     VARCHAR(30) DEFAULT 'suggestive',
                    incident_id           VARCHAR(255),
                    execution_id          VARCHAR(255),
                    created_at            TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS deployments (
                    id           SERIAL PRIMARY KEY,
                    repo_name    VARCHAR(255) NOT NULL,
                    commit_sha   VARCHAR(40),
                    deployed_at  TIMESTAMPTZ NOT NULL,
                    environment  VARCHAR(50) DEFAULT 'production',
                    deployed_by  VARCHAR(255),
                    tag          VARCHAR(255),
                    notes        TEXT,
                    created_at   TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_imports (
                    id           SERIAL PRIMARY KEY,
                    repo_name    VARCHAR(255) NOT NULL,
                    file_path    VARCHAR(1000) NOT NULL,
                    from_module  VARCHAR(500) NOT NULL,
                    import_name  VARCHAR(255) NOT NULL,
                    is_relative  BOOLEAN DEFAULT FALSE,
                    indexed_at   TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (repo_name, file_path, from_module, import_name)
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_owners (
                    id            SERIAL PRIMARY KEY,
                    repo_name     VARCHAR(255) NOT NULL,
                    path_pattern  VARCHAR(500) NOT NULL,
                    owners        JSONB NOT NULL,
                    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (repo_name, path_pattern)
                )
            """))
            await session.commit()

    async def seed_cache(self, cache: Dict[str, str], repos: List[Dict[str, str]]) -> int:
        """Pre-seed retrieval cache with most-accessed investigation_memory entries.

        Returns count of entries loaded.
        """
        loaded = 0
        for repo in repos:
            rn = repo.get("name", "")
            if not rn:
                continue
            try:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        text("""
                            SELECT function_name, file_path, summary, entity_id
                              FROM investigation_memory
                             WHERE repo_name = :rn AND status = 'active'
                             ORDER BY access_count DESC
                             LIMIT 10
                        """),
                        {"rn": rn},
                    )
                    for row in result:
                        key = f"fn|{row.function_name}|{rn}"
                        if key not in cache and row.summary:
                            cache[key] = json.dumps({
                                "name": row.function_name,
                                "file_path": row.file_path,
                                "summary": row.summary,
                                "entity_id": row.entity_id,
                                "_source": "investigation_memory",
                            }, separators=(",", ":"))
                            loaded += 1
            except Exception as exc:
                logger.debug("seed_cache skipped for %s: %s", rn, exc)
        return loaded

    async def bump_access(self, repo: str, function_name: str) -> None:
        """Increment access_count for a remembered function."""
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        UPDATE investigation_memory
                           SET access_count  = access_count + 1,
                               last_accessed = NOW()
                         WHERE repo_name = :rn AND function_name = :fn
                    """),
                    {"rn": repo, "fn": function_name},
                )
                await session.commit()
        except Exception:
            pass

    async def save_finding(
        self,
        repo: str,
        function_name: str,
        summary: str,
        file_path: Optional[str] = None,
        tags: Optional[List[str]] = None,
        entity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a key function finding to investigation_memory."""
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        INSERT INTO investigation_memory
                            (repo_name, function_name, file_path, entity_id, summary, tags)
                        VALUES
                            (:rn, :fn, :fp, :eid, :s, :t::jsonb)
                        ON CONFLICT (repo_name, function_name)
                        DO UPDATE SET
                            summary       = EXCLUDED.summary,
                            tags          = EXCLUDED.tags,
                            file_path     = COALESCE(EXCLUDED.file_path, investigation_memory.file_path),
                            entity_id     = COALESCE(EXCLUDED.entity_id, investigation_memory.entity_id),
                            access_count  = investigation_memory.access_count + 1,
                            last_accessed = NOW()
                    """),
                    {
                        "rn": repo, "fn": function_name, "fp": file_path,
                        "eid": entity_id, "s": summary,
                        "t": json.dumps(tags or []),
                    },
                )
                await session.commit()
            return {"saved": True, "repo": repo, "function": function_name}
        except Exception as exc:
            logger.error("save_finding failed: %s", exc)
            return {"saved": False, "error": str(exc)}

    async def get_rca_history(
        self,
        error_signature: Optional[str] = None,
        repo: Optional[str] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Retrieve matching RCA history entries."""
        try:
            async with AsyncSessionLocal() as session:
                if error_signature:
                    result = await session.execute(
                        text("""
                            SELECT id, repo_name, error_signature, root_cause_functions,
                                   resolution_summary, evidence_grade, confirmation_count,
                                   is_novel_pattern, created_at
                              FROM rca_history
                             WHERE (:rn::text IS NULL OR repo_name = :rn)
                               AND (
                                   error_signature ILIKE '%' || :sig || '%'
                                   OR :sig ILIKE '%' || error_signature || '%'
                               )
                             ORDER BY confirmation_count DESC, created_at DESC
                             LIMIT :limit
                        """),
                        {"rn": repo, "sig": error_signature, "limit": limit},
                    )
                else:
                    result = await session.execute(
                        text("""
                            SELECT id, repo_name, error_signature, root_cause_functions,
                                   resolution_summary, evidence_grade, confirmation_count,
                                   is_novel_pattern, created_at
                              FROM rca_history
                             WHERE (:rn::text IS NULL OR repo_name = :rn)
                             ORDER BY created_at DESC
                             LIMIT :limit
                        """),
                        {"rn": repo, "limit": limit},
                    )
                return [
                    {
                        "id": r.id,
                        "repo": r.repo_name,
                        "error_signature": r.error_signature,
                        "root_cause_functions": r.root_cause_functions or [],
                        "resolution_summary": r.resolution_summary,
                        "evidence_grade": r.evidence_grade,
                        "confirmation_count": r.confirmation_count,
                        "is_novel_pattern": r.is_novel_pattern,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in result
                ]
        except Exception as exc:
            logger.debug("get_rca_history failed: %s", exc)
            return []

    async def record_rca(
        self,
        repo: str,
        root_cause_functions: List[str],
        resolution_summary: str,
        error_signature: Optional[str] = None,
        evidence_grade: str = "inferred",
        sub_tool_trace: Optional[List[Dict]] = None,
        remediation_steps: Optional[List[Dict]] = None,
        incident_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> int:
        """Insert or update an RCA record. Returns the RCA id."""
        # Check if this is a novel pattern (no prior rca_history entry for these functions)
        existing = await self.get_rca_history(
            error_signature=error_signature, repo=repo, limit=1
        )
        is_novel = len(existing) == 0

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    INSERT INTO rca_history
                        (repo_name, error_signature, root_cause_functions,
                         resolution_summary, evidence_grade, sub_tool_trace,
                         remediation_steps, remediation_grade,
                         is_novel_pattern, incident_id, execution_id)
                    VALUES
                        (:rn, :sig, :rcf::jsonb, :res, :eg, :stt::jsonb,
                         :rms::jsonb, 'suggestive', :novel, :iid, :eid)
                    RETURNING id
                """),
                {
                    "rn": repo,
                    "sig": error_signature,
                    "rcf": json.dumps(root_cause_functions),
                    "res": resolution_summary,
                    "eg": evidence_grade,
                    "stt": json.dumps(sub_tool_trace or []),
                    "rms": json.dumps(remediation_steps or []),
                    "novel": is_novel,
                    "iid": incident_id,
                    "eid": execution_id,
                },
            )
            rca_id = result.scalar()
            await session.commit()
        return rca_id

    def build_remediation_steps(
        self,
        root_cause_functions: List[str],
        error_type: str = "other",
        has_recent_deploy: bool = False,
        recent_deploy: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build rule-based remediation steps. Always grade='suggestive'."""
        templates: Dict[str, List[Tuple[str, str]]] = {
            "timeout":      [("add_timeout", "Add explicit timeout to {fn}"),
                             ("circuit_breaker", "Wrap {fn} with circuit breaker"),
                             ("async_review", "Check for blocking calls inside {fn}")],
            "auth_failure": [("check_token_expiry", "Verify token TTL config"),
                             ("review_auth_chain", "Trace auth chain with code_explain_flow")],
            "null_pointer": [("null_guard", "Add null/None checks to {fn}"),
                             ("check_callers", "Review all callers with code_explain_flow")],
            "rate_limit":   [("add_retry_backoff", "Add exponential backoff"),
                             ("review_rate_config", "Review rate limit configuration")],
            "config_error": [("check_env_vars", "Check environment variable bindings"),
                             ("diff_config", "Compare config with last deployed version")],
        }

        steps: List[Dict[str, Any]] = []
        step_num = 0

        # Rollback step prepended when recent deploy present
        if has_recent_deploy and recent_deploy:
            steps.append({
                "step": step_num,
                "action": "rollback",
                "priority": "high",
                "details": (
                    f"Incident may correlate with deploy {recent_deploy.get('tag', 'unknown')} "
                    f"({recent_deploy.get('deployed_at', '')}). Consider rollback."
                ),
                "rollback_target": recent_deploy.get("tag"),
                "deployed_at": recent_deploy.get("deployed_at"),
            })
            step_num += 1

        fn_name = root_cause_functions[0] if root_cause_functions else "{fn}"
        for action, description_template in templates.get(error_type, [("investigate", "Investigate {fn}")]):
            steps.append({
                "step": step_num,
                "action": action,
                "details": description_template.format(fn=fn_name),
                "target_function": fn_name,
            })
            step_num += 1

        return steps


# ---------------------------------------------------------------------------
# CodeInvestigationRuntime — session orchestration, public tools
# ---------------------------------------------------------------------------

class CodeInvestigationRuntime:
    """Session-scoped orchestration layer.

    Instantiated once per ``build_code_analyzer_tools()`` call.
    Holds the per-execution retrieval cache and orchestrates all 5 public tools.
    """

    def __init__(
        self,
        graph: CodeGraphStore,
        memory: CodeMemoryStore,
        repos: List[Dict[str, str]],
        original_query: str = "",
    ) -> None:
        self._graph = graph
        self._memory = memory
        self._repos = repos
        self._original_query = original_query

        # Per-execution session state (process-memory only, never persisted)
        self._cache: Dict[str, str] = {}
        self._seen_functions: List[str] = []
        self._seen_queries: List[str] = []
        self._seen_traces: List[str] = []
        self._prior_memory_loaded: int = 0
        self._execution_start: float = time.monotonic()
        self._last_investigate_trace: List[Dict[str, Any]] = []

    def _ck(self, *parts: Any) -> str:
        return "|".join(str(p) for p in parts)

    def _session_context_dict(self) -> Dict[str, Any]:
        return {
            "fetched_functions": self._seen_functions,
            "searched_queries": self._seen_queries[-10:],
            "traced_entry_points": self._seen_traces,
            "prior_memory_loaded": self._prior_memory_loaded,
            "cache_size": len(self._cache),
        }

    def _make_trace_entry(
        self,
        tool: str,
        input_summary: str,
        output_summary: str,
        evidence_grade: str,
        duration_ms: int,
        tokens_est: int,
    ) -> Dict[str, Any]:
        return {
            "tool": tool,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "evidence_grade": evidence_grade,
            "duration_ms": duration_ms,
            "tokens_est": tokens_est,
        }

    # ── Public tool 1: code_investigate ────────────────────────────────────

    async def investigate(
        self,
        query: str,
        repo: str,
        depth: str = "standard",
        investigation_type: Optional[str] = None,
    ) -> str:
        """Primary investigation entry point.

        Orchestrates 2–5 internal methods based on query type and depth.
        Always runs _get_rca_history LAST (immutable synthesis order).
        """
        t_start = time.monotonic()
        sub_tool_trace: List[Dict[str, Any]] = []

        # Classify investigation (multi-strategy, weighted)
        if investigation_type:
            strategies = [{"type": investigation_type, "weight": 1.0}]
        else:
            strategies = _classify_investigation(query)

        top_strategies = sorted(strategies, key=lambda s: s["weight"], reverse=True)

        # Build merged tool sequence from top-2 strategies
        merged_tools: List[str] = []
        for s in top_strategies[:2]:
            for t in STRATEGY_MAP.get(s["type"], STRATEGY_MAP["general"]):
                if t not in merged_tools:
                    merged_tools.append(t)

        depth_limits = {"quick": 2, "standard": 5, "deep": len(merged_tools)}
        tool_limit = depth_limits.get(depth, 5)
        active_tools = merged_tools[:tool_limit]

        primary_suspects: List[Dict[str, Any]] = []
        call_chain: List[Dict[str, Any]] = []
        recent_changes: List[Dict[str, Any]] = []
        deployment_info: Optional[Dict[str, Any]] = None

        evidence_signals = {"semantic": False, "call_graph": False, "deployment": False}

        # Check session cache first
        cache_key = self._ck("investigate", query, repo, depth)
        if cache_key in self._cache:
            return self._cache[cache_key] + "\n[cached]"

        # ── Run active tools (all except _get_rca_history) ────────────────────
        for tool_name in active_tools:
            t0 = time.monotonic()

            if tool_name == "_search":
                self._seen_queries.append(query[:60])
                hits, src = await self._graph._search(query, repo=repo, limit=5)
                evidence_signals["semantic"] = bool(hits)
                for hit in hits[:3]:
                    primary_suspects.append({
                        "name": hit.get("name"),
                        "file": hit.get("file_path"),
                        "similarity": hit.get("similarity"),
                        "evidence_grade": "speculative",
                        "evidence_sources": ["_search"],
                    })
                out_summary = (
                    f"{len(hits)} hits — top: {hits[0].get('name')} "
                    f"(sim={hits[0].get('similarity', 0):.2f})"
                    if hits else "0 hits"
                )
                sub_tool_trace.append(self._make_trace_entry(
                    src, f"query={query!r} repo={repo!r}",
                    out_summary, "speculative",
                    int((time.monotonic() - t0) * 1000),
                    len(json.dumps(hits, separators=(",", ":"))) // 4,
                ))

            elif tool_name == "_trace_flow" and primary_suspects:
                top_fn = primary_suspects[0].get("name", "")
                if top_fn:
                    self._seen_traces.append(top_fn)
                    nodes, src = await self._graph._trace_flow(top_fn, repo, max_depth=2)
                    evidence_signals["call_graph"] = bool(nodes)
                    call_chain = nodes
                    # Upgrade top suspect's grade if we got graph evidence
                    if nodes and primary_suspects:
                        current_grade = primary_suspects[0]["evidence_grade"]
                        if evidence_signals["semantic"] and evidence_signals["call_graph"]:
                            primary_suspects[0]["evidence_grade"] = "correlated"
                            primary_suspects[0]["evidence_sources"].append("_trace_flow")
                    out_summary = f"{len(nodes)} nodes depth=2" if nodes else "0 nodes"
                    sub_tool_trace.append(self._make_trace_entry(
                        src, f"entry={top_fn!r} repo={repo!r}",
                        out_summary, "inferred",
                        int((time.monotonic() - t0) * 1000),
                        len(json.dumps(nodes, separators=(",", ":"))) // 4,
                    ))

            elif tool_name == "_diff_summary":
                diff_result, src = await self._graph._diff_summary(repo, since_hours=24)
                commits = diff_result.get("commits", [])
                recent_changes = commits[:10]
                out_summary = f"{len(commits)} commits in last 24h"
                sub_tool_trace.append(self._make_trace_entry(
                    src, f"repo={repo!r} since_hours=24",
                    out_summary, "inferred",
                    int((time.monotonic() - t0) * 1000),
                    len(json.dumps(commits, separators=(",", ":"))) // 4,
                ))

            elif tool_name == "_get_deployment_timeline":
                deploys, src = await self._graph._get_deployment_timeline(repo, since_hours=48)
                if deploys:
                    deployment_info = deploys[0]
                    evidence_signals["deployment"] = True
                out_summary = f"{len(deploys)} deploys in last 48h"
                sub_tool_trace.append(self._make_trace_entry(
                    src, f"repo={repo!r} since_hours=48",
                    out_summary, "deployment_correlated" if deploys else "inferred",
                    int((time.monotonic() - t0) * 1000),
                    len(json.dumps(deploys, separators=(",", ":"))) // 4,
                ))

            elif tool_name == "_get_function" and primary_suspects:
                top_fn = primary_suspects[0].get("name", "")
                if top_fn and top_fn not in self._seen_functions:
                    fn_result, src = await self._graph._get_function(top_fn, repo=repo)
                    self._seen_functions.append(top_fn)
                    # bump persistent access count
                    asyncio.create_task(self._memory.bump_access(repo, top_fn))
                    out_summary = f"fetched {top_fn}" if "error" not in fn_result else fn_result.get("error", "")
                    sub_tool_trace.append(self._make_trace_entry(
                        src, f"name={top_fn!r} repo={repo!r}",
                        out_summary, "inferred",
                        int((time.monotonic() - t0) * 1000),
                        len(json.dumps(fn_result, separators=(",", ":"))) // 4,
                    ))

            elif tool_name == "_get_recent_changes" and primary_suspects:
                top_file = primary_suspects[0].get("file", "")
                if top_file:
                    changes, src = await self._graph._get_recent_changes(top_file, repo, days=7)
                    out_summary = f"{len(changes.get('commits', []))} commits on {top_file}"
                    sub_tool_trace.append(self._make_trace_entry(
                        src, f"file={top_file!r}",
                        out_summary, "inferred",
                        int((time.monotonic() - t0) * 1000),
                        len(json.dumps(changes, separators=(",", ":"))) // 4,
                    ))

        # ── ALWAYS run _get_rca_history LAST (immutable synthesis order) ──────
        t0 = time.monotonic()
        historical = await self._memory.get_rca_history(
            error_signature=query[:200] if len(strategies) == 1 and strategies[0]["type"] == "incident" else None,
            repo=repo,
            limit=3,
        )
        if historical:
            historical_grade = (
                "historical_rca" if historical[0].get("confirmation_count", 0) >= 2
                else "inferred"
            )
            # Historical: at most 1 slot in primary_suspects (dominance cap)
            primary_suspects.append({
                "name": (historical[0].get("root_cause_functions") or ["unknown"])[0],
                "file": None,
                "evidence_grade": historical_grade,
                "evidence_sources": ["_get_rca_history"],
                "historical_summary": historical[0].get("resolution_summary"),
                "confirmation_count": historical[0].get("confirmation_count", 1),
            })
        sub_tool_trace.append(self._make_trace_entry(
            "_get_rca_history", f"repo={repo!r}",
            f"{len(historical)} historical RCA(s) found",
            historical[0].get("evidence_grade", "inferred") if historical else "speculative",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(historical, separators=(",", ":"))) // 4,
        ))

        # ── Final evidence grading ─────────────────────────────────────────────
        overall_grade = _grade_evidence(
            has_semantic=evidence_signals["semantic"],
            has_call_graph=evidence_signals["call_graph"],
            has_runtime_trace=False,
            historical_confirmation_count=historical[0].get("confirmation_count", 0) if historical else 0,
            has_deployment_correlation=evidence_signals["deployment"],
        )

        total_tokens = sum(e.get("tokens_est", 0) for e in sub_tool_trace)
        total_ms = int((time.monotonic() - t_start) * 1000)

        result = {
            "investigation_type": top_strategies[0]["type"] if top_strategies else "general",
            "strategies": top_strategies,
            "primary_suspects": primary_suspects[:5],
            "call_chain": call_chain[:15],
            "recent_changes": recent_changes[:5],
            "deployment_info": deployment_info,
            "historical_rca": historical[:1] if historical else [],
            "evidence_grade": overall_grade,
            "evidence_description": EVIDENCE_GRADES.get(overall_grade, ""),
            "sub_tool_trace": sub_tool_trace,
            "sub_tools_called": len(sub_tool_trace),
            "total_tokens_est": total_tokens,
            "duration_ms": total_ms,
        }

        out = json.dumps(result, separators=(",", ":"), default=str)
        self._cache[cache_key] = out
        self._last_investigate_trace = sub_tool_trace
        return out

    # ── Public tool 2: code_explain_flow ───────────────────────────────────

    async def explain_flow(
        self,
        entry_function: str,
        repo: str,
        max_depth: int = 2,
        include_owners: bool = True,
        include_imports: bool = True,
    ) -> str:
        """Structural flow explanation: trace + callers + imports + owners."""
        sub_tool_trace: List[Dict[str, Any]] = []
        t_start = time.monotonic()

        cache_key = self._ck("explain", entry_function, repo, max_depth, include_owners, include_imports)
        if cache_key in self._cache:
            return self._cache[cache_key] + "\n[cached]"

        # 1. Trace flow (always first — primary structural source)
        t0 = time.monotonic()
        nodes, src = await self._graph._trace_flow(entry_function, repo, max_depth=max_depth)
        sub_tool_trace.append(self._make_trace_entry(
            src, f"entry={entry_function!r} depth={max_depth}",
            f"{len(nodes)} nodes", "inferred",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(nodes, separators=(",", ":"))) // 4,
        ))

        # 2. Callers (impact analysis)
        t0 = time.monotonic()
        callers, src = await self._graph._get_callers(entry_function, repo=repo, limit=10)
        sub_tool_trace.append(self._make_trace_entry(
            src, f"fn={entry_function!r}",
            f"{len(callers)} callers", "inferred",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(callers, separators=(",", ":"))) // 4,
        ))

        # Determine the file path for import/owner lookups
        file_path: Optional[str] = None
        if nodes:
            file_path = nodes[0].get("file")

        imports: List[Dict[str, Any]] = []
        imported_by_count = 0
        owners: Dict[str, Any] = {}

        # 3. Imports (if file_path known and include_imports)
        if include_imports and file_path:
            t0 = time.monotonic()
            imports, src = await self._graph._get_imports(file_path, repo, "imports")
            imp_by, _ = await self._graph._get_imports(file_path, repo, "imported_by")
            imported_by_count = len(imp_by)
            sub_tool_trace.append(self._make_trace_entry(
                src, f"file={file_path!r} direction=imports",
                f"{len(imports)} imports, imported_by {imported_by_count} files",
                "inferred",
                int((time.monotonic() - t0) * 1000),
                (len(json.dumps(imports, separators=(",", ":"))) + len(json.dumps(imp_by, separators=(",", ":"))))// 4,
            ))

        # 4. Owners
        if include_owners and file_path:
            t0 = time.monotonic()
            owners, src = await self._graph._get_owners(file_path, repo)
            sub_tool_trace.append(self._make_trace_entry(
                src, f"file={file_path!r}",
                f"owners={owners.get('owners', [])} domain={owners.get('domain', '')}",
                "inferred",
                int((time.monotonic() - t0) * 1000),
                len(json.dumps(owners, separators=(",", ":"))) // 4,
            ))

        overall_grade = "inferred" if nodes else "speculative"
        enrichment_ready = True
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("SELECT enrichment_phase FROM code_chunks WHERE repo_name=:rn LIMIT 1"),
                    {"rn": repo},
                )
                row = result.one_or_none()
                if row and row.enrichment_phase == "raw":
                    enrichment_ready = False
        except Exception:
            pass

        out_data = {
            "entry_function": entry_function,
            "repo": repo,
            "call_chain": nodes,
            "callers": callers,
            "imports": imports,
            "imported_by_count": imported_by_count,
            "blast_radius_note": (
                f"⚠️ High blast radius — imported by {imported_by_count} files."
                if imported_by_count >= 5 else None
            ),
            "owners": owners,
            "evidence_grade": overall_grade,
            "evidence_description": EVIDENCE_GRADES.get(overall_grade, ""),
            "enrichment_note": (
                "⚠️ Graph enrichment pending — edge weights and ownership not yet available."
                if not enrichment_ready else None
            ),
            "sub_tool_trace": sub_tool_trace,
            "duration_ms": int((time.monotonic() - t_start) * 1000),
        }
        out = json.dumps(out_data, separators=(",", ":"), default=str)
        self._cache[cache_key] = out
        return out

    # ── Public tool 3: code_analyze_change ─────────────────────────────────

    async def analyze_change(
        self,
        repo: str,
        since_hours: int = 24,
        path_filter: Optional[str] = None,
        include_blast_radius: bool = True,
    ) -> str:
        """Change impact analysis: diff + deployments + blast radius + owners."""
        sub_tool_trace: List[Dict[str, Any]] = []
        t_start = time.monotonic()

        cache_key = self._ck("change", repo, since_hours, path_filter)
        if cache_key in self._cache:
            return self._cache[cache_key] + "\n[cached]"

        # 1. Diff summary
        t0 = time.monotonic()
        diff, src = await self._graph._diff_summary(repo, since_hours=since_hours, path_filter=path_filter)
        commits = diff.get("commits", [])
        sub_tool_trace.append(self._make_trace_entry(
            src, f"repo={repo!r} since_hours={since_hours}",
            f"{len(commits)} commits", "inferred",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(commits, separators=(",", ":"))) // 4,
        ))

        # 2. Deployments (annotate commits with deploy markers)
        t0 = time.monotonic()
        deploys, src = await self._graph._get_deployment_timeline(
            repo, since_hours=since_hours + 24
        )
        has_deploy = bool(deploys)
        # Annotate commits that fall within a deploy window
        for commit in commits:
            commit_ts = commit.get("timestamp", "")
            for deploy in deploys:
                deploy_ts = deploy.get("deployed_at", "")
                if deploy_ts and commit_ts <= deploy_ts:
                    commit["deployed_in"] = deploy.get("tag") or deploy.get("commit_sha", "")[:8]
                    break
        sub_tool_trace.append(self._make_trace_entry(
            src, f"repo={repo!r} since_hours={since_hours+24}",
            f"{len(deploys)} deploys found",
            "deployment_correlated" if has_deploy else "inferred",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(deploys, separators=(",", ":"))) // 4,
        ))

        # 3. Blast radius for top-3 changed files
        blast_radius: List[Dict[str, Any]] = []
        if include_blast_radius:
            changed_files = list({
                f for c in commits for f in c.get("files", [])
            })[:3]
            for fp in changed_files:
                t0 = time.monotonic()
                imp_by, src_i = await self._graph._get_imports(fp, repo, "imported_by")
                owners, src_o = await self._graph._get_owners(fp, repo)
                blast_radius.append({
                    "file": fp,
                    "imported_by_count": len(imp_by),
                    "owners": owners.get("owners", []),
                    "domain": owners.get("domain", ""),
                })
                sub_tool_trace.append(self._make_trace_entry(
                    f"{src_i}+{src_o}", f"file={fp!r}",
                    f"imported_by {len(imp_by)}, owners={owners.get('owners', [])}",
                    "inferred",
                    int((time.monotonic() - t0) * 1000),
                    (len(json.dumps(imp_by, separators=(",", ":"))) +
                     len(json.dumps(owners, separators=(",", ":")))) // 4,
                ))

        overall_grade = (
            "deployment_correlated" if has_deploy and commits else
            "inferred" if commits else "speculative"
        )

        out_data = {
            "repo": repo,
            "since_hours": since_hours,
            "commits": commits[:20],
            "deployments": deploys,
            "blast_radius": blast_radius,
            "evidence_grade": overall_grade,
            "evidence_description": EVIDENCE_GRADES.get(overall_grade, ""),
            "sub_tool_trace": sub_tool_trace,
            "duration_ms": int((time.monotonic() - t_start) * 1000),
        }
        out = json.dumps(out_data, separators=(",", ":"), default=str)
        self._cache[cache_key] = out
        return out

    # ── Public tool 4: code_get_runtime_evidence ───────────────────────────

    async def get_runtime_evidence(
        self,
        repo: str,
        query: str,
        since_hours: int = 2,
    ) -> str:
        """Runtime evidence: stack trace parsing + anomaly correlation."""
        sub_tool_trace: List[Dict[str, Any]] = []
        t_start = time.monotonic()

        cache_key = self._ck("runtime", repo, query[:100])
        if cache_key in self._cache:
            return self._cache[cache_key] + "\n[cached]"

        # 1. Parse stack frames from query text
        t0 = time.monotonic()
        frames = self._graph._parse_stack_frames(query)
        matched: List[Dict[str, Any]] = []
        if frames:
            matched = await self._graph._match_frames_to_chunks(frames, repo)
        sub_tool_trace.append(self._make_trace_entry(
            "_parse_stack_frames+_match_frames",
            f"len={len(query)} chars",
            f"{len(frames)} frames parsed, {len(matched)} matched to code",
            "runtime-confirmed" if matched else "speculative",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(matched, separators=(",", ":"))) // 4,
        ))

        # 2. Deployment correlation for the time window
        t0 = time.monotonic()
        deploys, src = await self._graph._get_deployment_timeline(repo, since_hours=since_hours + 24)
        recent_deploy = deploys[0] if deploys else None
        sub_tool_trace.append(self._make_trace_entry(
            src, f"repo={repo!r} since_hours={since_hours+24}",
            f"{len(deploys)} deploy(s) in window",
            "deployment_correlated" if deploys else "inferred",
            int((time.monotonic() - t0) * 1000),
            len(json.dumps(deploys, separators=(",", ":"))) // 4,
        ))

        overall_grade = (
            "runtime-confirmed" if matched else
            "deployment_correlated" if deploys else
            "speculative"
        )

        out_data = {
            "repo": repo,
            "matched_functions": matched,
            "stack_frames_parsed": frames,
            "recent_deployment": recent_deploy,
            "evidence_grade": overall_grade,
            "evidence_description": EVIDENCE_GRADES.get(overall_grade, ""),
            "sub_tool_trace": sub_tool_trace,
            "duration_ms": int((time.monotonic() - t_start) * 1000),
        }
        out = json.dumps(out_data, separators=(",", ":"), default=str)
        self._cache[cache_key] = out
        return out

    # ── Public tool 5: code_finalize_incident ──────────────────────────────

    async def finalize_incident(
        self,
        repo: str,
        root_cause_description: str,
        root_cause_functions: List[str],
        resolution: str,
        error_signature: Optional[str] = None,
        incident_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> str:
        """Investigation closure: record RCA + async learning.

        Returns immediately. Learning (save_finding, async strategy update)
        runs in the background without blocking.
        """
        # Build remediation steps (suggestive grade — never inherits finding grade)
        t0 = time.monotonic()
        deploys, _ = await self._graph._get_deployment_timeline(repo, since_hours=48)
        recent_deploy = deploys[0] if deploys else None
        remediation_steps = self._memory.build_remediation_steps(
            root_cause_functions=root_cause_functions,
            error_type="other",
            has_recent_deploy=bool(recent_deploy),
            recent_deploy=recent_deploy,
        )

        # Determine evidence grade from session signals
        has_runtime = any(
            e.get("evidence_grade") == "runtime-confirmed"
            for e in self._last_investigate_trace
        )
        has_call_graph = any(
            "_trace_flow" in e.get("tool", "")
            for e in self._last_investigate_trace
        )
        has_semantic = any(
            "_search" in e.get("tool", "")
            for e in self._last_investigate_trace
        )
        evidence_grade = _grade_evidence(
            has_semantic=has_semantic,
            has_call_graph=has_call_graph,
            has_runtime_trace=has_runtime,
            has_deployment_correlation=bool(recent_deploy),
        )

        # ── Fire-and-forget learning layer ────────────────────────────────────
        asyncio.create_task(
            self._async_finalize(
                repo=repo,
                root_cause_functions=root_cause_functions,
                resolution=resolution,
                evidence_grade=evidence_grade,
                error_signature=error_signature,
                remediation_steps=remediation_steps,
                sub_tool_trace=self._last_investigate_trace,
                incident_id=incident_id,
                execution_id=execution_id,
            )
        )

        result = {
            "status": "finalizing",
            "finding": {
                "grade": evidence_grade,
                "description": EVIDENCE_GRADES.get(evidence_grade, ""),
                "root_cause_functions": root_cause_functions,
                "root_cause_description": root_cause_description,
                "evidence_sources": [e.get("tool") for e in self._last_investigate_trace],
            },
            "remediation": {
                "grade": "suggestive",  # ALWAYS suggestive — never inherits finding grade
                "note": "Automated suggestions only. Operator judgment required for all actions.",
                "steps": remediation_steps,
            },
            "message": "RCA recorded. Learning layer updating asynchronously.",
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }
        return json.dumps(result, separators=(",", ":"), default=str)

    async def _async_finalize(
        self,
        repo: str,
        root_cause_functions: List[str],
        resolution: str,
        evidence_grade: str,
        error_signature: Optional[str],
        remediation_steps: List[Dict[str, Any]],
        sub_tool_trace: List[Dict[str, Any]],
        incident_id: Optional[str],
        execution_id: Optional[str],
    ) -> None:
        """Background learning tasks — runs after agent response is delivered."""
        try:
            rca_id = await self._memory.record_rca(
                repo=repo,
                root_cause_functions=root_cause_functions,
                resolution_summary=resolution,
                error_signature=error_signature,
                evidence_grade=evidence_grade,
                sub_tool_trace=sub_tool_trace,
                remediation_steps=remediation_steps,
                incident_id=incident_id,
                execution_id=execution_id,
            )
            logger.info("_async_finalize: RCA %d recorded for %s", rca_id, repo)

            # Save each root cause function to investigation_memory
            for fn in root_cause_functions[:5]:
                await self._memory.save_finding(
                    repo=repo,
                    function_name=fn,
                    summary=f"Root cause in incident: {resolution[:200]}",
                    tags=["root_cause", f"incident:{incident_id}" if incident_id else "incident"],
                )
        except Exception as exc:
            logger.error("_async_finalize failed for %s: %s", repo, exc)


# ---------------------------------------------------------------------------
# Builder — creates the 5 StructuredTool instances
# ---------------------------------------------------------------------------

def build_code_analyzer_tools(
    repos: Optional[List[Dict[str, str]]] = None,
) -> List[StructuredTool]:
    """Build and return exactly 5 LangChain StructuredTool instances.

    Args:
        repos: List of repo configs [{"name": str, "path": str, "language": str}].
               Paths must be under REPOS_BASE_PATH (enforced by the executor before calling this).

    Returns:
        5 StructuredTool objects for the ReAct agent.
    """
    _repos = repos or []
    _repo_names = [r.get("name", "") for r in _repos]

    # Instantiate the three runtime classes (process-memory, never persisted)
    graph = CodeGraphStore(repos=_repos)
    memory = CodeMemoryStore()
    runtime = CodeInvestigationRuntime(graph=graph, memory=memory, repos=_repos)

    # Ensure all required DB tables exist and seed cache asynchronously
    async def _ensure_and_seed() -> None:
        await memory.ensure_tables()
        loaded = await memory.seed_cache(runtime._cache, _repos)
        runtime._prior_memory_loaded = loaded

    # Schedule setup to run when the event loop is available
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_ensure_and_seed())
        else:
            loop.run_until_complete(_ensure_and_seed())
    except RuntimeError:
        pass  # Not in an async context yet; will set up on first call

    repo_hint = ""
    if _repo_names:
        repo_hint = f" Configured repositories: {_repo_names}."

    # ── Tool 1: code_investigate ─────────────────────────────────────────────

    async def _investigate(
        query: str,
        repo: str,
        depth: str = "standard",
        investigation_type: Optional[str] = None,
    ) -> str:
        return await runtime.investigate(query, repo, depth, investigation_type)

    # ── Tool 2: code_explain_flow ────────────────────────────────────────────

    async def _explain_flow(
        entry_function: str,
        repo: str,
        max_depth: int = 2,
        include_owners: bool = True,
        include_imports: bool = True,
    ) -> str:
        return await runtime.explain_flow(
            entry_function, repo, max_depth, include_owners, include_imports
        )

    # ── Tool 3: code_analyze_change ──────────────────────────────────────────

    async def _analyze_change(
        repo: str,
        since_hours: int = 24,
        path_filter: Optional[str] = None,
        include_blast_radius: bool = True,
    ) -> str:
        return await runtime.analyze_change(repo, since_hours, path_filter, include_blast_radius)

    # ── Tool 4: code_get_runtime_evidence ────────────────────────────────────

    async def _get_runtime_evidence(
        repo: str,
        query: str,
        since_hours: int = 2,
    ) -> str:
        return await runtime.get_runtime_evidence(repo, query, since_hours)

    # ── Tool 5: code_finalize_incident ───────────────────────────────────────

    async def _finalize_incident(
        repo: str,
        root_cause_description: str,
        root_cause_functions: List[str],
        resolution: str,
        error_signature: Optional[str] = None,
        incident_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> str:
        return await runtime.finalize_incident(
            repo=repo,
            root_cause_description=root_cause_description,
            root_cause_functions=root_cause_functions,
            resolution=resolution,
            error_signature=error_signature,
            incident_id=incident_id,
            execution_id=execution_id,
        )

    tools = [
        StructuredTool.from_function(
            coroutine=_investigate,
            name="code_investigate",
            description=(
                "PRIMARY investigation entry point. Orchestrates semantic search, call graph "
                "traversal, deployment correlation, and RCA history internally. "
                "Returns synthesized investigation report with evidence_grade and sub_tool_trace. "
                "Use depth='quick' for simple lookups (~2 tools), 'standard' for incidents (default), "
                "'deep' for complex investigations."
                + repo_hint
            ),
            args_schema=InvestigateInput,
        ),
        StructuredTool.from_function(
            coroutine=_explain_flow,
            name="code_explain_flow",
            description=(
                "Explain how a specific function works: call graph, callers, "
                "import dependencies, and CODEOWNERS. Use when you need to understand "
                "the structural role of a specific function — after code_investigate "
                "identifies it as a suspect."
                + repo_hint
            ),
            args_schema=ExplainFlowInput,
        ),
        StructuredTool.from_function(
            coroutine=_analyze_change,
            name="code_analyze_change",
            description=(
                "Analyze recent code changes for change-impact and blast radius. "
                "Returns commits, deployment correlation, imported-by counts for changed files, "
                "and owner information. Use when the issue likely started after a recent "
                "deploy or commit."
                + repo_hint
            ),
            args_schema=AnalyzeChangeInput,
        ),
        StructuredTool.from_function(
            coroutine=_get_runtime_evidence,
            name="code_get_runtime_evidence",
            description=(
                "Extract runtime evidence from a stack trace or error message: parse frames, "
                "match to source functions, correlate with recent deployments. "
                "Produces 'runtime-confirmed' evidence grade when frames match. "
                "Use this FIRST when a stack trace or log anomaly is available — "
                "it is the highest-confidence signal."
                + repo_hint
            ),
            args_schema=RuntimeEvidenceInput,
        ),
        StructuredTool.from_function(
            coroutine=_finalize_incident,
            name="code_finalize_incident",
            description=(
                "ALWAYS call this at the end of every investigation to record findings. "
                "Stores RCA, generates remediation steps (grade='suggestive' — never operator-certain), "
                "and triggers async learning. Returns immediately. "
                "Remediation grade is always 'suggestive' — never claim certainty for automated suggestions."
                + repo_hint
            ),
            args_schema=FinalizeIncidentInput,
        ),
    ]

    logger.info(
        "build_code_analyzer_tools: created %d tools (repos=%s)",
        len(tools),
        _repo_names,
    )
    return tools
