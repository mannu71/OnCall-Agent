"""tracePathFlow nodes.

Pipeline: LookupOverview → FindSymbolDef → ScanCallerCandidates
          → LLMResolveEdges → VerifyEachEdge → BuildEdgeList

Traces call-graph edges (callers or callees) for a named symbol.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

_MAX_SCAN_FILES = 40
_MAX_FILE_CHARS = 60_000
_MAX_CONTEXT_CHARS = 150_000


# ─────────────────────────────────────────────────────────────────────────────
# Node 0: TraverseKGEdges  (knowledge-graph fast path — replaces LLM tracing)
# ─────────────────────────────────────────────────────────────────────────────


class TraverseKGEdges(AsyncNode):
    """Walk ``kg_edges`` via a recursive CTE to find callers/callees.

    Reads from shared:
        repo, symbol, direction ('callers'|'callees'), depth (default 2)

    Writes to shared:
        _verified_edges  list[{from, to, label, file, line, confidence}]
                         (same shape VerifyEachEdge produces)
        _trace
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo":      shared["repo"],
            "symbol":    shared["symbol"],
            "direction": shared.get("direction", "callers"),
            "depth":     int(shared.get("depth", 2)),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        t0 = time.monotonic()
        repo      = prep_res["repo"]
        symbol    = prep_res["symbol"]
        direction = prep_res["direction"]
        depth     = max(1, min(prep_res["depth"], 6))   # clamp to [1, 6] hops

        suffix_pattern = f"%::{symbol}"

        if direction == "callers":
            sql = """
                WITH RECURSIVE walk AS (
                    SELECT
                        e.source_qname AS from_q,
                        e.target_qname AS to_q,
                        e.file_path    AS file_path,
                        e.line         AS line,
                        e.kind         AS kind,
                        e.confidence   AS confidence,
                        1              AS hop
                    FROM kg_edges e
                    WHERE e.repo_name = :r
                      AND e.kind = 'calls'
                      AND (e.target_qname = :sym OR e.target_qname LIKE :suffix)

                    UNION ALL

                    SELECT
                        e.source_qname,
                        e.target_qname,
                        e.file_path,
                        e.line,
                        e.kind,
                        e.confidence,
                        w.hop + 1
                    FROM kg_edges e
                    JOIN walk w ON e.target_qname = w.from_q
                    WHERE e.repo_name = :r
                      AND e.kind = 'calls'
                      AND w.hop < :d
                )
                SELECT DISTINCT from_q, to_q, file_path, line, kind, confidence, hop
                FROM walk
                ORDER BY hop, file_path, line
                LIMIT 500
            """
        else:
            sql = """
                WITH RECURSIVE walk AS (
                    SELECT
                        e.source_qname AS from_q,
                        e.target_qname AS to_q,
                        e.file_path    AS file_path,
                        e.line         AS line,
                        e.kind         AS kind,
                        e.confidence   AS confidence,
                        1              AS hop
                    FROM kg_edges e
                    WHERE e.repo_name = :r
                      AND e.kind = 'calls'
                      AND (e.source_qname = :sym OR e.source_qname LIKE :suffix)

                    UNION ALL

                    SELECT
                        e.source_qname,
                        e.target_qname,
                        e.file_path,
                        e.line,
                        e.kind,
                        e.confidence,
                        w.hop + 1
                    FROM kg_edges e
                    JOIN walk w ON e.source_qname = w.to_q
                    WHERE e.repo_name = :r
                      AND e.kind = 'calls'
                      AND w.hop < :d
                )
                SELECT DISTINCT from_q, to_q, file_path, line, kind, confidence, hop
                FROM walk
                ORDER BY hop, file_path, line
                LIMIT 500
            """

        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(sql),
                {"r": repo, "sym": symbol, "suffix": suffix_pattern, "d": depth},
            )
            results = rows.fetchall()

        ms = int((time.monotonic() - t0) * 1000)

        edges: List[Dict[str, Any]] = []
        for row in results:
            from_q, to_q, file_path, line, _kind, confidence, hop = row
            from_display = from_q.split("::")[-1] if "::" in from_q else from_q
            to_display   = to_q.split("::")[-1]   if "::" in to_q   else to_q
            edges.append({
                "from":       from_display,
                "to":         to_display,
                "label":      "calls",
                "file":       file_path,
                "line":       int(line) if line else None,
                "confidence": confidence or "extracted",
                "hop":        int(hop),
            })

        return {"edges": edges, "ms": ms}

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_verified_edges"] = exec_res["edges"]
        _append_trace(
            shared, "TraverseKGEdges",
            result_count=len(exec_res["edges"]),
            tokens_in=0, tokens_out=0, cached=False,
            ms=exec_res["ms"],
        )
        logger.info(
            "TraverseKGEdges: repo=%s symbol=%s direction=%s depth=%d → %d edges in %dms",
            shared.get("repo"), shared.get("symbol"),
            prep_res["direction"], prep_res["depth"],
            len(exec_res["edges"]), exec_res["ms"],
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 1: LookupOverview
# ─────────────────────────────────────────────────────────────────────────────

class LookupOverview(AsyncNode):
    """Load the indexed overview for *repo* from ``repo_abstractions``."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"repo": shared["repo"]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("SELECT overview FROM repo_abstractions WHERE repo_name = :repo"),
                {"repo": prep_res["repo"]},
            )
            row = row.fetchone()

        if row is None:
            raise ValueError(
                f"LookupOverview: repo '{prep_res['repo']}' has not been indexed. "
                "Run indexFlow first."
            )
        overview = row[0]
        return {
            "abstractions": overview.get("abstractions", []),
            "file_map": overview.get("file_map", {}),
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_overview"] = exec_res
        _append_trace(shared, "LookupOverview", len(exec_res["abstractions"]), 0, 0, False)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 2: FindSymbolDef
# ─────────────────────────────────────────────────────────────────────────────

class FindSymbolDef(AsyncNode):
    """Use the LLM to identify which file defines the target symbol."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "symbol": shared["symbol"],
            "direction": shared.get("direction", "callers"),
            "file_map": shared["_overview"]["file_map"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        file_listing = "\n".join(
            f"- {path}"
            for paths in prep_res["file_map"].values()
            for path in paths
        )
        direction = prep_res["direction"]

        prompt = f"""Project: "{prep_res['repo']}"

Symbol to trace: "{prep_res['symbol']}"
Trace direction: {direction} ({"who calls this symbol" if direction == "callers" else "what this symbol calls"})

Known files:
{file_listing[:4000]}

Which file most likely defines "{prep_res['symbol']}"?
Also list files that are likely to {"call" if direction == "callers" else "be called by"} it.

Output YAML only:

```yaml
definition_file: app/auth.py
candidate_files:
  - app/api/routes.py
  - app/middleware.py
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=self.cur_retry == 0
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            data = yaml.safe_load(yaml_str) or {}
        except Exception:
            data = {}

        def_file = data.get("definition_file", "")
        candidates = data.get("candidate_files", [])
        if not isinstance(candidates, list):
            candidates = []

        return {
            "definition_file": def_file,
            "candidate_files": candidates[:_MAX_SCAN_FILES],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_def_file"] = exec_res["definition_file"]
        shared["_trace_candidates"] = exec_res["candidate_files"]
        _append_trace(
            shared, "FindSymbolDef",
            len(exec_res["candidate_files"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 3: ScanCallerCandidates
# ─────────────────────────────────────────────────────────────────────────────

class ScanCallerCandidates(AsyncNode):
    """Read candidate files from disk and build LLM context."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        candidates = shared["_trace_candidates"]
        def_file = shared.get("_def_file", "")
        all_files = list(dict.fromkeys([def_file] + candidates)) if def_file else candidates
        return {"repo": shared["repo"], "files": all_files[:_MAX_SCAN_FILES]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        from app.config import settings
        repos_root = settings.repos_base_path
        repo_dir = os.path.join(repos_root, prep_res["repo"])

        def _read() -> List[Tuple[str, str]]:
            results = []
            for relpath in prep_res["files"]:
                if not relpath:
                    continue
                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        content = f.read()
                    results.append((relpath, content))
                except Exception as exc:
                    logger.debug("ScanCallerCandidates: skip %s — %s", relpath, exc)
            return results

        t0 = time.monotonic()
        file_contents = await asyncio.to_thread(_read)
        ms = int((time.monotonic() - t0) * 1000)

        parts: List[str] = []
        total = 0
        included: List[str] = []
        for relpath, content in file_contents:
            truncated = content[:_MAX_FILE_CHARS]
            entry = f"--- File: {relpath} ---\n{truncated}\n\n"
            if total + len(entry) > _MAX_CONTEXT_CHARS:
                break
            parts.append(entry)
            included.append(relpath)
            total += len(entry)

        return {"file_context": "".join(parts), "included": included, "ms": ms}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_trace_context"] = exec_res["file_context"]
        _append_trace(shared, "ScanCallerCandidates", len(exec_res["included"]),
                      0, 0, False, ms=exec_res["ms"])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 4: LLMResolveEdges
# ─────────────────────────────────────────────────────────────────────────────

class LLMResolveEdges(AsyncNode):
    """Ask the LLM to identify call edges for the target symbol."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "symbol": shared["symbol"],
            "direction": shared.get("direction", "callers"),
            "depth": shared.get("depth", 2),
            "file_context": shared["_trace_context"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        direction = prep_res["direction"]
        edge_desc = (
            "who calls this symbol (callers)"
            if direction == "callers"
            else "what this symbol calls (callees)"
        )

        prompt = f"""Project: "{prep_res['repo']}"

Find {edge_desc} for symbol "{prep_res['symbol']}".
Search depth: {prep_res['depth']} hops.

File contents:
{prep_res['file_context']}

List every direct call edge. For each edge provide:
- from: caller function/class name
- to: callee function/class name
- file: file where the call occurs
- line: line number of the call site
- label: brief description (e.g. "calls", "inherits", "imports")

Output YAML only:

```yaml
- from: handle_request
  to: {prep_res['symbol']}
  file: app/api/routes.py
  line: 88
  label: calls
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=self.cur_retry == 0
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            edges = yaml.safe_load(yaml_str) or []
            if not isinstance(edges, list):
                edges = []
        except Exception as exc:
            raise ValueError(
                f"LLMResolveEdges: cannot parse YAML: {exc}\nRaw: {response[:400]}"
            )

        return {
            "edges": edges,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_raw_edges"] = exec_res["edges"]
        _append_trace(
            shared, "LLMResolveEdges",
            len(exec_res["edges"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.debug("LLMResolveEdges: %d raw edges", len(exec_res["edges"]))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 5: VerifyEachEdge
# ─────────────────────────────────────────────────────────────────────────────

class VerifyEachEdge(AsyncNode):
    """Hard gate: verify each edge's (file, line) exists on disk."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"repo": shared["repo"], "raw_edges": shared["_raw_edges"]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        from app.config import settings
        repos_root = settings.repos_base_path
        repo_dir = os.path.join(repos_root, prep_res["repo"])

        def _verify() -> List[Dict[str, Any]]:
            verified = []
            for edge in prep_res["raw_edges"]:
                relpath = str(edge.get("file", "")).strip()
                line_no = edge.get("line")

                if not relpath or line_no is None:
                    # Edges without location are kept but flagged unverified
                    verified.append({**edge, "confidence": "unverified"})
                    continue

                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        total_lines = sum(1 for _ in f)
                    if 1 <= int(line_no) <= total_lines:
                        verified.append({
                            **edge,
                            "file": relpath,
                            "line": int(line_no),
                            "confidence": "verified",
                        })
                    else:
                        logger.debug("VerifyEachEdge: line %d out of range in %s", line_no, relpath)
                except PathJailError:
                    logger.debug("VerifyEachEdge: path jail %s", abs_path)
                except FileNotFoundError:
                    logger.debug("VerifyEachEdge: not found %s", abs_path)
                except Exception as exc:
                    logger.debug("VerifyEachEdge: %s → %s", relpath, exc)
            return verified

        t0 = time.monotonic()
        verified = await asyncio.to_thread(_verify)
        ms = int((time.monotonic() - t0) * 1000)
        return {"verified": verified, "ms": ms}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_verified_edges"] = exec_res["verified"]
        _append_trace(shared, "VerifyEachEdge", len(exec_res["verified"]),
                      0, 0, False, ms=exec_res["ms"])
        logger.info(
            "VerifyEachEdge: %d/%d edges kept",
            len(exec_res["verified"]), len(prep_res["raw_edges"]),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 6: BuildEdgeList
# ─────────────────────────────────────────────────────────────────────────────

class BuildEdgeList(AsyncNode):
    """Assemble the final trace response with body handles on each edge."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "symbol": shared["symbol"],
            "direction": shared.get("direction", "callers"),
            "edges": shared["_verified_edges"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.handles import make_body_handle as _make_body_handle

        results = []
        for edge in prep_res["edges"]:
            relpath = edge.get("file", "")
            line_no = edge.get("line")
            entry: Dict[str, Any] = {
                "from": edge.get("from", "?"),
                "to": edge.get("to", "?"),
                "label": edge.get("label", "calls"),
                "file": relpath,
                "line": line_no,
                "confidence": edge.get("confidence", "unverified"),
            }
            if relpath and line_no:
                entry["body_handle"] = _make_body_handle(
                    prep_res["repo"], relpath,
                    max(1, int(line_no) - 2), int(line_no) + 10,
                )
            results.append(entry)

        return {"edges": results, "total": len(results)}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["response"] = {
            "symbol": prep_res["symbol"],
            "repo": prep_res["repo"],
            "direction": prep_res["direction"],
            "total_edges": exec_res["total"],
            "edges": exec_res["edges"],
        }
        _append_trace(shared, "BuildEdgeList", exec_res["total"], 0, 0, False)
        return None
