"""Crawler flow implementations — canonical logic for ``CrawlerService``.

MCP tools and HTTP endpoints both reach this module through ``CrawlerService``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1: crawler_index_repo
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_index_repo(
    repo: str,
    force: bool = False,
    model_id: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Index a repository so it can be searched with crawler_find_symbol.

    Crawls all source files under REPOS_BASE_PATH/{repo}, extracts 5-10 core
    abstractions and their relationships via LLM, and stores the overview in
    ``repo_abstractions``.  Subsequent calls are skipped if the file checksum
    hasn't changed (pass ``force=True`` to override).

    Args:
        repo:             Repository directory name under REPOS_BASE_PATH.
        force:            Bypass the skip-if-unchanged SHA check.
        model_id:         LLM model to use (defaults to CRAWLER_MODEL env var).
        include_patterns: Glob patterns to include (e.g. ["*.py", "*.ts"]).
        exclude_patterns: Additional glob patterns to exclude.

    Returns:
        dict with keys: repo_name, files_indexed, abstractions, relationships,
        mermaid, files_sha256.
    """
    from app.crawler import run_flow
    from app.crawler.flows import index_flow

    # Check existing index unless force=True
    if not force:
        try:
            from app.core.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                row = await session.execute(
                    text(
                        "SELECT files_sha256 FROM repo_abstractions "
                        "WHERE repo_name = :repo"
                    ),
                    {"repo": repo},
                )
                existing = row.fetchone()
                if existing:
                    logger.info(
                        "crawler_index_repo: '%s' already indexed (sha256=%s…). "
                        "Pass force=True to reindex.",
                        repo, existing[0][:8],
                    )
                    row2 = await session.execute(
                        text(
                            "SELECT overview, files_indexed, files_sha256 "
                            "FROM repo_abstractions WHERE repo_name = :repo"
                        ),
                        {"repo": repo},
                    )
                    r = row2.fetchone()
                    if r:
                        overview, files_indexed, sha256 = r
                        return {
                            "repo_name": repo,
                            "files_indexed": files_indexed,
                            "abstractions": overview.get("abstractions", []),
                            "relationships": overview.get("relationships", []),
                            "mermaid": overview.get("mermaid", ""),
                            "files_sha256": sha256,
                            "cached": True,
                        }
        except Exception as exc:
            logger.debug("crawler_index_repo: skip-check failed (%s), proceeding", exc)

    shared: Dict[str, Any] = {
        "repo": repo,
        "force": force,
        "model_id": model_id,
    }
    if include_patterns:
        shared["include_patterns"] = set(include_patterns)
    if exclude_patterns:
        shared["exclude_patterns"] = set(exclude_patterns)

    try:
        await run_flow("indexFlow", index_flow, shared)
        return shared.get("response", {})
    except Exception as exc:
        logger.error("crawler_index_repo failed for '%s': %s", repo, exc)
        return {"error": str(exc), "repo": repo}


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2: crawler_find_symbol
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_find_symbol(
    symbol: str,
    repo: str,
    kind: Optional[str] = None,
    limit: int = 5,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Find where a symbol is defined in an indexed repository.

    Uses the stored abstraction overview to narrow the search scope, then reads
    candidate files and asks the LLM to locate the definition.  Every cited
    (file, line) is verified on disk before being returned — no hallucinations.

    Args:
        symbol:   Symbol name to find (function, class, constant, etc.).
        repo:     Repository directory name under REPOS_BASE_PATH.
        kind:     Optional type hint: "function", "class", "method", "constant".
        limit:    Maximum number of results to return (default 5).
        model_id: LLM model override.

    Returns:
        dict with keys: symbol, repo, found (bool), count, results.
        Each result has: file, line, kind, context, body_handle, confidence.
        Pass body_handle to crawler_get_body to retrieve the source lines.
    """
    from app.crawler import run_flow
    from app.crawler.flows import find_symbol_flow

    shared: Dict[str, Any] = {
        "repo": repo,
        "symbol": symbol,
        "kind": kind,
        "limit": limit,
        "model_id": model_id,
    }

    try:
        await run_flow("findSymbolFlow", find_symbol_flow, shared)
        return shared.get(
            "response",
            {"symbol": symbol, "repo": repo, "found": False, "results": [], "count": 0},
        )
    except Exception as exc:
        logger.error("crawler_find_symbol failed ('%s' in '%s'): %s", symbol, repo, exc)
        return {"error": str(exc), "symbol": symbol, "repo": repo, "found": False}


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3: crawler_get_body
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_get_body(
    handle: str,
    page: int = 1,
) -> Dict[str, Any]:
    """Retrieve the source lines associated with a body handle.

    Body handles are returned by crawler_find_symbol (body_handle field).
    They are interchangeable with handles issued by the v1 ``get_body`` tool.
    Large bodies are paginated at 100 lines per page.

    Args:
        handle: Opaque handle string (e.g. "fn:abc123def456").
        page:   Page number to retrieve (1-based, default 1).

    Returns:
        dict with keys: handle, repo, file, handle_line_start, handle_line_end,
        page, total_pages, content (numbered source lines).
    """
    from app.crawler import run_flow
    from app.crawler.flows import get_body_flow

    shared: Dict[str, Any] = {
        "handle": handle,
        "page": page,
    }

    try:
        await run_flow("getBodyFlow", get_body_flow, shared)
        return shared.get("response", {})
    except Exception as exc:
        logger.error("crawler_get_body failed (handle='%s'): %s", handle, exc)
        return {"error": str(exc), "handle": handle}


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4: crawler_trace_path
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_trace_path(
    symbol: str,
    repo: str,
    direction: str = "callers",
    depth: int = 2,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Trace the call graph for a symbol — find who calls it or what it calls.

    Args:
        symbol:    Symbol name to trace.
        repo:      Repository directory name under REPOS_BASE_PATH.
        direction: "callers" (who calls this) or "callees" (what it calls).
        depth:     Number of hops to trace (default 2).
        model_id:  LLM model override.

    Returns:
        dict with keys: symbol, repo, direction, total_edges, edges.
        Each edge: {from, to, label, file, line, confidence, body_handle}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import trace_path_flow

    shared: Dict[str, Any] = {
        "repo": repo,
        "symbol": symbol,
        "direction": direction,
        "depth": depth,
        "model_id": model_id,
    }

    try:
        await run_flow("tracePathFlow", trace_path_flow, shared)
        return shared.get(
            "response",
            {"symbol": symbol, "repo": repo, "direction": direction, "total_edges": 0, "edges": []},
        )
    except Exception as exc:
        logger.error("crawler_trace_path failed ('%s' in '%s'): %s", symbol, repo, exc)
        return {"error": str(exc), "symbol": symbol, "repo": repo}


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5: crawler_search_semantic
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_search_semantic(
    query: str,
    repo: str,
    limit: int = 10,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Answer a freeform natural-language query against an indexed repository.

    Identifies relevant files via abstraction metadata, reads them, and
    extracts the most relevant code passages.  All hits are disk-verified.

    Args:
        query:    Natural-language query (e.g. "where is JWT validation done?").
        repo:     Repository directory name under REPOS_BASE_PATH.
        limit:    Maximum number of results (default 10).
        model_id: LLM model override.

    Returns:
        dict with keys: query, repo, count, hits.
        Each hit: {rank, file, line, snippet, relevance, body_handle}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import search_semantic_flow

    shared: Dict[str, Any] = {
        "repo": repo,
        "query": query,
        "limit": limit,
        "model_id": model_id,
    }

    try:
        await run_flow("searchSemanticFlow", search_semantic_flow, shared)
        return shared.get("response", {"query": query, "repo": repo, "count": 0, "hits": []})
    except Exception as exc:
        logger.error("crawler_search_semantic failed (query='%s', repo='%s'): %s", query, repo, exc)
        return {"error": str(exc), "query": query, "repo": repo}


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6: crawler_investigate_alert
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_investigate_alert(
    alert: str,
    repo: str,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Perform automated root-cause analysis for an on-call alert.

    Parses the alert text, maps it to relevant code abstractions, reads
    the implicated files, and synthesises a structured RCA with
    suggestive remediation steps.

    Args:
        alert:    Full alert text (error message, stack trace, runbook excerpt, etc.).
        repo:     Repository directory name under REPOS_BASE_PATH.
        model_id: LLM model override.

    Returns:
        dict with keys: alert_summary, repo, severity, root_cause, confidence,
        contributing_factors, evidence_files, recommendations (grade=suggestive),
        immediate_actions, contributing_symbols, contributing_abstractions.
    """
    from app.crawler import run_flow
    from app.crawler.flows import investigate_alert_flow

    shared: Dict[str, Any] = {
        "repo": repo,
        "alert": alert,
        "model_id": model_id,
    }

    try:
        await run_flow("investigateAlertFlow", investigate_alert_flow, shared)
        return shared.get("response", {"error": "No response produced", "repo": repo})
    except Exception as exc:
        logger.error("crawler_investigate_alert failed (repo='%s'): %s", repo, exc)
        return {"error": str(exc), "repo": repo}


# ─────────────────────────────────────────────────────────────────────────────
# Phase B (knowledge-graph) tools — pure Postgres queries against kg_* tables.
# All six return in milliseconds, with no LLM calls.
# ─────────────────────────────────────────────────────────────────────────────


async def crawler_callers(
    symbol: str,
    repo: str,
    depth: int = 2,
) -> Dict[str, Any]:
    """Find all callers of *symbol* (transitive, up to *depth* hops).

    Pure recursive-CTE walk over ``kg_edges`` — no LLM involvement.

    Args:
        symbol: Method/function name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        depth:  Maximum hop distance (clamped to 1..6, default 2).

    Returns:
        dict {symbol, repo, direction='callers', total_edges, edges[]}
        Each edge: {from, to, label, file, line, confidence, hop, body_handle}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import trace_path_flow

    shared: Dict[str, Any] = {
        "repo":      repo,
        "symbol":    symbol,
        "direction": "callers",
        "depth":     depth,
    }
    try:
        await run_flow("tracePathFlow", trace_path_flow, shared)
        return shared.get(
            "response",
            {"symbol": symbol, "repo": repo, "direction": "callers", "total_edges": 0, "edges": []},
        )
    except Exception as exc:
        logger.error("crawler_callers failed (symbol=%s repo=%s): %s", symbol, repo, exc)
        return {"error": str(exc), "symbol": symbol, "repo": repo}


async def crawler_callees(
    symbol: str,
    repo: str,
    depth: int = 2,
) -> Dict[str, Any]:
    """Find everything *symbol* calls, transitively up to *depth* hops.

    Mirror of :func:`crawler_callers` but walks forward through ``kg_edges``.

    Args:
        symbol: Method/function name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        depth:  Maximum hop distance (clamped to 1..6, default 2).

    Returns:
        dict {symbol, repo, direction='callees', total_edges, edges[]}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import trace_path_flow

    shared: Dict[str, Any] = {
        "repo":      repo,
        "symbol":    symbol,
        "direction": "callees",
        "depth":     depth,
    }
    try:
        await run_flow("tracePathFlow", trace_path_flow, shared)
        return shared.get(
            "response",
            {"symbol": symbol, "repo": repo, "direction": "callees", "total_edges": 0, "edges": []},
        )
    except Exception as exc:
        logger.error("crawler_callees failed (symbol=%s repo=%s): %s", symbol, repo, exc)
        return {"error": str(exc), "symbol": symbol, "repo": repo}


async def crawler_impact(
    symbol: str,
    repo: str,
) -> Dict[str, Any]:
    """Impact analysis: "if I change X, what else breaks?"

    Combines callers, test coverage, and inbound references into one report.
    Drives refactoring safety questions an agent can answer from the graph
    alone — no LLM call.

    Args:
        symbol: Method/function/class name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.

    Returns:
        dict {symbol, repo, callers[], tests[], references[],
              caller_count, test_count, reference_count}.
        Each entry: {from_qname, file, line, kind, confidence, body_handle}.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    from app.crawler.handles import make_body_handle

    suffix = f"%::{symbol}"

    try:
        async with AsyncSessionLocal() as session:
            edges_sql = """
                SELECT e.kind, e.source_qname, e.file_path, e.line, e.confidence,
                       COALESCE(n.is_test, false) AS source_is_test
                FROM kg_edges e
                LEFT JOIN kg_nodes n
                  ON  n.repo_name      = e.repo_name
                  AND n.qualified_name = e.source_qname
                WHERE e.repo_name = :r
                  AND (e.target_qname = :sym OR e.target_qname LIKE :suffix)
                ORDER BY e.kind, e.file_path, e.line
                LIMIT 1000
            """
            rows = await session.execute(
                text(edges_sql),
                {"r": repo, "sym": symbol, "suffix": suffix},
            )
            results = rows.fetchall()

        callers:    List[Dict[str, Any]] = []
        tests:      List[Dict[str, Any]] = []
        references: List[Dict[str, Any]] = []

        for kind, from_qname, file_path, line, confidence, source_is_test in results:
            entry = {
                "from_qname": from_qname,
                "file":       file_path,
                "line":       int(line) if line else None,
                "kind":       kind,
                "confidence": confidence or "extracted",
            }
            if file_path and line:
                entry["body_handle"] = make_body_handle(
                    repo, file_path,
                    max(1, int(line) - 2), int(line) + 10,
                )

            if kind == "calls":
                if source_is_test:
                    tests.append(entry)
                else:
                    callers.append(entry)
            else:
                references.append(entry)

        return {
            "symbol":          symbol,
            "repo":            repo,
            "caller_count":    len(callers),
            "test_count":      len(tests),
            "reference_count": len(references),
            "callers":         callers,
            "tests":           tests,
            "references":      references,
        }
    except Exception as exc:
        logger.error("crawler_impact failed (symbol=%s repo=%s): %s", symbol, repo, exc)
        return {"error": str(exc), "symbol": symbol, "repo": repo}


async def crawler_node(
    qualified_name: str,
    repo: str,
) -> Dict[str, Any]:
    """Fetch one ``kg_nodes`` row by its fully-qualified name.

    Returns full metadata (signature, docstring, decorators, line range,
    body_handle) so the agent can render a single node without further calls.

    Args:
        qualified_name: e.g. ``src/Services/Foo.cs::FooService::CheckEntity``.
        repo:           Repository under REPOS_BASE_PATH.

    Returns:
        dict with all node fields plus ``body_handle``, or
        ``{error, qualified_name, repo}`` if not found.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    from app.crawler.handles import make_body_handle

    try:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("""
                    SELECT kind, name, qualified_name, file_path,
                           line_start, line_end, language, parent_name,
                           signature, docstring, exported, is_async, is_static,
                           is_abstract, is_test, decorators, type_parameters
                    FROM kg_nodes
                    WHERE repo_name = :r AND qualified_name = :q
                """),
                {"r": repo, "q": qualified_name},
            )
            r = row.fetchone()

        if r is None:
            return {"error": "not found", "qualified_name": qualified_name, "repo": repo}

        (kind, name, qname, file_path, line_start, line_end, language,
         parent_name, signature, docstring, exported, is_async, is_static,
         is_abstract, is_test, decorators, type_parameters) = r

        handle = None
        if file_path and line_start:
            handle = make_body_handle(
                repo, file_path,
                max(1, int(line_start) - 2),
                int(line_end or line_start) + 10,
            )

        return {
            "repo":            repo,
            "kind":            kind,
            "name":            name,
            "qualified_name":  qname,
            "file":            file_path,
            "line_start":      int(line_start) if line_start else None,
            "line_end":        int(line_end)   if line_end   else None,
            "language":        language,
            "parent_name":     parent_name,
            "signature":       signature,
            "docstring":       docstring,
            "exported":        bool(exported),
            "is_async":        bool(is_async),
            "is_static":       bool(is_static),
            "is_abstract":     bool(is_abstract),
            "is_test":         bool(is_test),
            "decorators":      decorators,
            "type_parameters": type_parameters,
            "body_handle":     handle,
        }
    except Exception as exc:
        logger.error("crawler_node failed (qname=%s repo=%s): %s", qualified_name, repo, exc)
        return {"error": str(exc), "qualified_name": qualified_name, "repo": repo}


async def crawler_files(
    repo: str,
    language: Optional[str] = None,
    with_errors_only: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """List indexed files for *repo* with parse statistics.

    Args:
        repo:             Repository under REPOS_BASE_PATH.
        language:         Optional filter (``csharp`` | ``python`` | ...).
        with_errors_only: If True, only return files whose ``parse_error`` is set.
        limit:            Max files to return (default 200).

    Returns:
        dict {repo, count, files[]} where each file is
        {file, language, sha256, size_bytes, node_count, edge_count,
         parse_error, parsed_at}.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    where = ["repo_name = :r"]
    params: Dict[str, Any] = {"r": repo, "lim": limit}
    if language:
        where.append("language = :lang")
        params["lang"] = language
    if with_errors_only:
        where.append("parse_error IS NOT NULL")

    sql = f"""
        SELECT file_path, language, sha256, size_bytes,
               node_count, edge_count, parse_error, parsed_at
        FROM kg_files
        WHERE {' AND '.join(where)}
        ORDER BY (parse_error IS NULL) ASC, file_path ASC
        LIMIT :lim
    """

    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(sql), params)
            results = rows.fetchall()

        files = [
            {
                "file":        r[0],
                "language":    r[1],
                "sha256":      r[2],
                "size_bytes":  int(r[3]) if r[3] is not None else None,
                "node_count":  int(r[4]) if r[4] is not None else 0,
                "edge_count":  int(r[5]) if r[5] is not None else 0,
                "parse_error": r[6],
                "parsed_at":   r[7].isoformat() if r[7] else None,
            }
            for r in results
        ]
        return {"repo": repo, "count": len(files), "files": files}
    except Exception as exc:
        logger.error("crawler_files failed (repo=%s): %s", repo, exc)
        return {"error": str(exc), "repo": repo}


async def crawler_find_references(
    symbol: str,
    repo: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Find all references to *symbol* grouped by relation kind.

    Pure graph query — no LLM.  Useful for refactoring ("where is this used?").

    Args:
        symbol: Method/function/class name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        limit:  Max results per relation kind (default 20).

    Returns:
        dict {symbol, repo, calls, imports, inherits, implements, tested_by}
        where each list contains {from_qname, file, line, confidence, body_handle}.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    from app.crawler.handles import make_body_handle

    suffix = f"%::{symbol}"

    sql = """
        SELECT kind, source_qname, file_path, line, confidence
        FROM kg_edges
        WHERE repo_name = :r
          AND (target_qname = :sym OR target_qname LIKE :suffix)
        ORDER BY kind, file_path, line
        LIMIT 1000
    """

    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(sql),
                {"r": repo, "sym": symbol, "suffix": suffix},
            )
            results = rows.fetchall()

        grouped: Dict[str, List[Dict[str, Any]]] = {
            "calls": [], "imports_from": [], "inherits": [],
            "implements": [], "tested_by": [], "references": [],
        }
        for kind, source_qname, file_path, line, confidence in results:
            bucket = grouped.setdefault(kind, [])
            if len(bucket) >= limit:
                continue
            entry = {
                "from_qname": source_qname,
                "file":       file_path,
                "line":       int(line) if line else None,
                "confidence": confidence or "extracted",
            }
            if file_path and line:
                entry["body_handle"] = make_body_handle(
                    repo, file_path,
                    max(1, int(line) - 2), int(line) + 10,
                )
            bucket.append(entry)

        total = sum(len(v) for v in grouped.values())
        return {
            "symbol":     symbol,
            "repo":       repo,
            "total":      total,
            "calls":      grouped.get("calls", []),
            "imports":    grouped.get("imports_from", []),
            "inherits":   grouped.get("inherits", []),
            "implements": grouped.get("implements", []),
            "tested_by":  grouped.get("tested_by", []),
            "references": grouped.get("references", []),
        }
    except Exception as exc:
        logger.error("crawler_find_references failed (symbol=%s repo=%s): %s", symbol, repo, exc)
        return {"error": str(exc), "symbol": symbol, "repo": repo}
