"""Crawler API endpoints.

Exposes the crawler flows (indexFlow, findSymbolFlow, getBodyFlow) and
supporting queries (flow_runs log, llm_cache stats) over HTTP.

Prefix: /crawler
Tag:    crawler
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crawler", tags=["crawler"])


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────

class IndexRequest(BaseModel):
    force: bool = Field(False, description="Bypass the skip-if-unchanged SHA check.")
    model_id: Optional[str] = None
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None


# ─────────────────────────────────────────────────────────────────────────────
# GET /crawler/repos  — list all repositories in repo_abstractions
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/repos", summary="List all indexed repositories")
async def list_indexed_repos() -> Dict[str, Any]:
    """Return all repositories that have been indexed and stored in ``repo_abstractions``.

    Used by the Configure Code Analyzer Node UI to populate a selection
    picker so users can choose from repos that are already crawled, without
    needing to manually enter paths.

    Returns:
        ``{ repos: [{ repo_name, files_indexed, model_id, generated_at }] }``
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                "SELECT repo_name, files_indexed, model_id, generated_at "
                "FROM repo_abstractions ORDER BY repo_name ASC"
            )
        )
        results = rows.fetchall()

    repos = [
        {
            "repo_name": r[0],
            "files_indexed": r[1],
            "model_id": r[2],
            "generated_at": r[3].isoformat() if r[3] else None,
        }
        for r in results
    ]
    return {"repos": repos, "count": len(repos)}


class FindRequest(BaseModel):
    name: str = Field(..., description="Symbol name to find.")
    repo: str = Field(..., description="Repository name under REPOS_BASE_PATH.")
    kind: Optional[str] = Field(None, description="Symbol type hint (function/class/method/constant).")
    limit: int = Field(5, ge=1, le=20)
    model_id: Optional[str] = None


class BodyRequest(BaseModel):
    handle: str = Field(..., description="Body handle from crawler_find_symbol.")
    page: int = Field(1, ge=1)


# ─────────────────────────────────────────────────────────────────────────────
# POST /crawler/index/{repo}
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/index/{repo}", summary="Index a repository")
async def index_repo(repo: str, body: IndexRequest) -> Dict[str, Any]:
    """Run indexFlow against the named repository.

    Crawls source files, extracts core abstractions via LLM, and stores the
    overview in ``repo_abstractions``.  Use ``force=true`` to reindex even if
    the file checksum hasn't changed.
    """
    from app.mcp.tools.crawler_tools import crawler_index_repo

    result = await crawler_index_repo(
        repo=repo,
        force=body.force,
        model_id=body.model_id,
        include_patterns=body.include_patterns,
        exclude_patterns=body.exclude_patterns,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /crawler/index/{repo}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/index/{repo}", summary="Get cached repo overview")
async def get_index(repo: str) -> Dict[str, Any]:
    """Return the cached abstraction overview for a repository (404 if not indexed)."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        row = await session.execute(
            text(
                "SELECT overview, files_indexed, files_sha256, model_id, "
                "tokens_in, tokens_out, generated_at "
                "FROM repo_abstractions WHERE repo_name = :repo"
            ),
            {"repo": repo},
        )
        r = row.fetchone()

    if r is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repository '{repo}' has not been indexed. POST to /crawler/index/{repo} first.",
        )

    overview, files_indexed, sha256, model_id, tokens_in, tokens_out, generated_at = r
    return {
        "repo_name": repo,
        "files_indexed": files_indexed,
        "files_sha256": sha256,
        "model_id": model_id,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "abstractions": overview.get("abstractions", []),
        "relationships": overview.get("relationships", []),
        "mermaid": overview.get("mermaid", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /crawler/find
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/find", summary="Find a symbol definition")
async def find_symbol(body: FindRequest) -> Dict[str, Any]:
    """Run findSymbolFlow to locate a symbol definition in an indexed repo.

    Returns up to ``limit`` results, each with a ``body_handle`` you can pass
    to ``POST /crawler/body`` to retrieve the actual source lines.
    """
    from app.mcp.tools.crawler_tools import crawler_find_symbol

    result = await crawler_find_symbol(
        symbol=body.name,
        repo=body.repo,
        kind=body.kind,
        limit=body.limit,
        model_id=body.model_id,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST /crawler/body
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/body", summary="Retrieve source lines by handle")
async def get_body(body: BodyRequest) -> Dict[str, Any]:
    """Run getBodyFlow to fetch numbered source lines for a body handle.

    Handles are issued by ``/crawler/find`` and are interchangeable with
    handles from the v1 ``get_body`` tool.
    """
    from app.mcp.tools.crawler_tools import crawler_get_body

    result = await crawler_get_body(handle=body.handle, page=body.page)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /crawler/runs
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs", summary="List flow run logs")
async def list_runs(
    flow_name: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Return paginated ``flow_runs`` rows with timing and token usage."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    filters = []
    params: Dict[str, Any] = {"limit": limit, "offset": offset}

    if flow_name:
        filters.append("flow_name = :flow_name")
        params["flow_name"] = flow_name
    if repo:
        filters.append("repo_name = :repo")
        params["repo"] = repo
    if session_id:
        filters.append("session_id = :session_id")
        params["session_id"] = session_id

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                f"SELECT id, flow_name, session_id, repo_name, success, error, "
                f"started_at, duration_ms "
                f"FROM flow_runs {where} "
                f"ORDER BY started_at DESC "
                f"LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        total_row = await session.execute(
            text(f"SELECT COUNT(*) FROM flow_runs {where}"),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )

    runs = [
        {
            "id": r[0],
            "flow_name": r[1],
            "session_id": r[2],
            "repo_name": r[3],
            "success": r[4],
            "error": r[5],
            "started_at": r[6].isoformat() if r[6] else None,
            "duration_ms": r[7],
        }
        for r in rows.fetchall()
    ]
    total = total_row.scalar() or 0

    return {"total": total, "limit": limit, "offset": offset, "runs": runs}


# ─────────────────────────────────────────────────────────────────────────────
# GET /crawler/runs/{run_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}", summary="Get a single flow run with full trace")
async def get_run(run_id: int) -> Dict[str, Any]:
    """Return a single ``flow_runs`` row including its full per-node trace."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        row = await session.execute(
            text(
                "SELECT id, flow_name, session_id, repo_name, inputs, response, "
                "trace, success, error, started_at, duration_ms "
                "FROM flow_runs WHERE id = :id"
            ),
            {"id": run_id},
        )
        r = row.fetchone()

    if r is None:
        raise HTTPException(status_code=404, detail=f"flow_run {run_id} not found.")

    return {
        "id": r[0],
        "flow_name": r[1],
        "session_id": r[2],
        "repo_name": r[3],
        "inputs": r[4],
        "response": r[5],
        "trace": r[6],
        "success": r[7],
        "error": r[8],
        "started_at": r[9].isoformat() if r[9] else None,
        "duration_ms": r[10],
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /crawler/cache/stats
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/cache/stats", summary="LLM cache statistics")
async def cache_stats() -> Dict[str, Any]:
    """Return summary statistics for the ``llm_cache`` table."""
    from app.crawler.cache import cache_stats as _stats

    return await _stats()


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /crawler/cache
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/cache", summary="Truncate LLM cache (admin)")
async def clear_cache() -> Dict[str, Any]:
    """Truncate the ``llm_cache`` table. Same admin gate as /llm_config."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        result = await session.execute(text("DELETE FROM llm_cache"))
        await session.commit()
        deleted = result.rowcount

    logger.warning("llm_cache truncated: %d rows deleted", deleted)
    return {"deleted": deleted, "message": "LLM cache cleared."}


# ─────────────────────────────────────────────────────────────────────────────
# Phase B request models
# ─────────────────────────────────────────────────────────────────────────────

class TraceRequest(BaseModel):
    symbol: str = Field(..., description="Symbol name to trace.")
    repo: str = Field(..., description="Repository name under REPOS_BASE_PATH.")
    direction: str = Field("callers", description="'callers' or 'callees'.")
    depth: int = Field(2, ge=1, le=5)
    model_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural-language query.")
    repo: str = Field(..., description="Repository name under REPOS_BASE_PATH.")
    limit: int = Field(10, ge=1, le=25)
    model_id: Optional[str] = None


class InvestigateRequest(BaseModel):
    alert: str = Field(..., description="Full alert text (error, stack trace, etc.).")
    repo: str = Field(..., description="Repository name under REPOS_BASE_PATH.")
    model_id: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# POST /crawler/trace
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/trace", summary="Trace call-graph edges for a symbol")
async def trace_path(body: TraceRequest) -> Dict[str, Any]:
    """Run tracePathFlow to find callers or callees of a symbol.

    Returns edges with file, line, body_handle, and confidence level.
    """
    from app.mcp.tools.crawler_tools import crawler_trace_path

    result = await crawler_trace_path(
        symbol=body.symbol,
        repo=body.repo,
        direction=body.direction,
        depth=body.depth,
        model_id=body.model_id,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST /crawler/search
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/search", summary="Semantic search over a repository")
async def search_semantic(body: SearchRequest) -> Dict[str, Any]:
    """Run searchSemanticFlow to find code relevant to a natural-language query.

    All results are disk-verified. Each hit includes a body_handle for
    retrieving the full source lines via ``POST /crawler/body``.
    """
    from app.mcp.tools.crawler_tools import crawler_search_semantic

    result = await crawler_search_semantic(
        query=body.query,
        repo=body.repo,
        limit=body.limit,
        model_id=body.model_id,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST /crawler/investigate
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/investigate", summary="Root-cause analysis for an on-call alert")
async def investigate_alert(body: InvestigateRequest) -> Dict[str, Any]:
    """Run investigateAlertFlow to produce a structured RCA for an alert.

    Returns root_cause, severity, contributing_factors, evidence_files,
    suggestive recommendations, and immediate_actions.
    """
    from app.mcp.tools.crawler_tools import crawler_investigate_alert

    result = await crawler_investigate_alert(
        alert=body.alert,
        repo=body.repo,
        model_id=body.model_id,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Phase B graph operations (Zero-token visual explorer support)
# ─────────────────────────────────────────────────────────────────────────────

class DiffImpactRequest(BaseModel):
    files: List[str] = Field(default_factory=list, description="List of changed file paths.")
    symbols: List[str] = Field(default_factory=list, description="List of changed symbol names.")


@router.get("/repos/{repo}/files", summary="List all indexed files for a repository")
async def get_repo_files(
    repo: str,
    language: Optional[str] = Query(None, description="Filter by language"),
    with_errors_only: bool = Query(False, description="Only return files with parse errors"),
    limit: int = Query(200, ge=1, le=1000)
) -> Dict[str, Any]:
    """List indexed files for the given repository, including parse errors and statistics."""
    from app.mcp.tools.crawler_tools import crawler_files
    result = await crawler_files(repo=repo, language=language, with_errors_only=with_errors_only, limit=limit)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/repos/{repo}/nodes", summary="Fetch all knowledge graph nodes and edges for a specific file")
async def get_file_nodes(
    repo: str,
    file_path: str = Query(..., description="File path relative or absolute")
) -> Dict[str, Any]:
    """Fetch all knowledge graph nodes defined in the given file, plus intra-file edges (Zero LLM cost)."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    nodes_sql = """
        SELECT kind, name, qualified_name, file_path, line_start, line_end, language, parent_name,
               signature, docstring, exported, is_async, is_static, is_abstract, is_test, decorators, type_parameters, domain
        FROM kg_nodes
        WHERE repo_name = :repo AND file_path = :file_path
        ORDER BY line_start ASC
    """

    edges_sql = """
        SELECT source_qname, target_qname, kind, confidence, line
        FROM kg_edges
        WHERE repo_name = :repo AND file_path = :file_path
    """

    try:
        async with AsyncSessionLocal() as session:
            nodes_rows = await session.execute(
                text(nodes_sql),
                {"repo": repo, "file_path": file_path}
            )
            nodes_res = nodes_rows.fetchall()

            edges_rows = await session.execute(
                text(edges_sql),
                {"repo": repo, "file_path": file_path}
            )
            edges_res = edges_rows.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}")

    nodes = [
        {
            "kind": r[0],
            "name": r[1],
            "qualified_name": r[2],
            "file": r[3],
            "line_start": r[4],
            "line_end": r[5],
            "language": r[6],
            "parent_name": r[7],
            "signature": r[8],
            "docstring": r[9],
            "exported": bool(r[10]),
            "is_async": bool(r[11]),
            "is_static": bool(r[12]),
            "is_abstract": bool(r[13]),
            "is_test": bool(r[14]),
            "decorators": r[15],
            "type_parameters": r[16],
            "domain": r[17],
        }
        for r in nodes_res
    ]

    edges = [
        {
            "source_qname": r[0],
            "target_qname": r[1],
            "kind": r[2],
            "confidence": r[3],
            "line": r[4],
        }
        for r in edges_res
    ]

    return {"repo": repo, "file_path": file_path, "nodes": nodes, "edges": edges}


@router.get("/repos/{repo}/node", summary="Fetch metadata for a single knowledge graph node")
async def get_node(
    repo: str,
    qualified_name: str = Query(..., description="Fully-qualified symbol name")
) -> Dict[str, Any]:
    """Fetch all metadata, signature, and body handle for a specific qualified name."""
    from app.mcp.tools.crawler_tools import crawler_node
    result = await crawler_node(qualified_name=qualified_name, repo=repo)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/repos/{repo}/callers", summary="Find all callers of a symbol")
async def get_callers(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
    depth: int = Query(2, ge=1, le=5)
) -> Dict[str, Any]:
    """Find all callers of a symbol transitively up to the specified depth (recursive CTE)."""
    from app.mcp.tools.crawler_tools import crawler_callers
    result = await crawler_callers(symbol=symbol, repo=repo, depth=depth)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/repos/{repo}/callees", summary="Find all callees of a symbol")
async def get_callees(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
    depth: int = Query(2, ge=1, le=5)
) -> Dict[str, Any]:
    """Find all methods/symbols called by the target symbol recursively."""
    from app.mcp.tools.crawler_tools import crawler_callees
    result = await crawler_callees(symbol=symbol, repo=repo, depth=depth)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/repos/{repo}/impact", summary="Impact analysis for a symbol")
async def get_impact(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)")
) -> Dict[str, Any]:
    """Perform impact analysis on a symbol, listing callers, connected tests, and references."""
    from app.mcp.tools.crawler_tools import crawler_impact
    result = await crawler_impact(symbol=symbol, repo=repo)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/repos/{repo}/references", summary="Find all references to a symbol grouped by relation kind")
async def get_references(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
    limit: int = Query(20, ge=1, le=100)
) -> Dict[str, Any]:
    """Find all imports, calls, inherits, and other references to the target symbol."""
    from app.mcp.tools.crawler_tools import crawler_find_references
    result = await crawler_find_references(symbol=symbol, repo=repo, limit=limit)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.post("/repos/{repo}/diff-impact", summary="Compute deterministic impact tree for a diff")
async def post_diff_impact(repo: str, body: DiffImpactRequest) -> Dict[str, Any]:
    """Given a list of changed files/symbols, recursively find all affected entry points and tests."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    from app.crawler.handles import make_body_handle

    sql = """
        WITH RECURSIVE impact_walk AS (
            -- Base case: find nodes directly defined in files or matching symbols
            SELECT 
                qualified_name, 
                name, 
                kind, 
                file_path, 
                line_start, 
                line_end,
                is_test,
                exported,
                0 AS hop
            FROM kg_nodes
            WHERE repo_name = :r
              AND (file_path = ANY(:files) OR name = ANY(:symbols) OR qualified_name = ANY(:symbols))
            
            UNION
            
            -- Recursive case: find who calls/depends on these nodes
            SELECT 
                n.qualified_name,
                n.name,
                n.kind,
                n.file_path,
                n.line_start,
                n.line_end,
                n.is_test,
                n.exported,
                w.hop + 1 AS hop
            FROM kg_edges e
            JOIN impact_walk w
              ON  e.repo_name = :r
              AND (e.target_qname = w.qualified_name OR e.target_qname = w.name)
            JOIN kg_nodes n
              ON  n.repo_name = :r
              AND n.qualified_name = e.source_qname
            WHERE w.hop < 5
        )
        SELECT DISTINCT ON (qualified_name)
            qualified_name, name, kind, file_path, line_start, line_end, is_test, exported, hop
        FROM impact_walk
        ORDER BY qualified_name, hop ASC;
    """
    
    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(sql),
                {"r": repo, "files": body.files, "symbols": body.symbols}
            )
            results = rows.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database walk failed: {exc}")
        
    affected_nodes = []
    affected_tests = []
    affected_entry_points = []
    
    for row in results:
        qname, name, kind, file_path, line_start, line_end, is_test, exported, hop = row
        handle = None
        if file_path and line_start:
            handle = make_body_handle(repo, file_path, max(1, int(line_start) - 2), int(line_end or line_start) + 10)
            
        entry = {
            "name": name,
            "qualified_name": qname,
            "kind": kind,
            "file": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "hop": hop,
            "body_handle": handle
        }
        
        affected_nodes.append(entry)
        if is_test:
            affected_tests.append(entry)
        elif exported or kind in ("route", "method") or "Controller" in qname:
            affected_entry_points.append(entry)
            
    return {
        "repo": repo,
        "input_files": body.files,
        "input_symbols": body.symbols,
        "total_impacted_nodes": len(affected_nodes),
        "impacted_nodes": affected_nodes,
        "affected_tests": affected_tests,
        "affected_entry_points": affected_entry_points
    }

