"""Crawler flow implementations — canonical logic for ``CrawlerService``.

MCP tools and HTTP endpoints both reach this module through ``CrawlerService``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# Names-only repo map cached per repo content-version (files_sha256). Lets the
# agent orient with a cheap symbol list instead of reading the whole codebase.
_repo_map_cache = TTLCache(ttl_seconds=3600.0, maxsize=64)


async def _resolve_repos(
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
) -> List[str]:
    """Resolve a query scope to a deduplicated, ordered list of repo names.

    Precedence: explicit ``repos`` list → single ``repo``.
    """
    out: List[str] = list(repos) if repos else ([repo] if repo else [])
    seen: set = set()
    return [r for r in out if r and not (r in seen or seen.add(r))]


# ─────────────────────────────────────────────────────────────────────────────
# Generic coding-agent file tools — grep / read / list across repos. No language,
# framework, or cloud knowledge: the agent supplies the pattern and reasons over
# results, the way a coding agent uses ripgrep + read. Multi-repo/group aware.
# ─────────────────────────────────────────────────────────────────────────────

async def crawler_grep(
    pattern: str,
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    glob: Optional[str] = None,
    ignore_case: bool = True,
    max_results: int = 80,
) -> Dict[str, Any]:
    """Regex-search repository file contents (like ripgrep), across one repo,
    an explicit list, or a group. Returns matches with repo/file/line/text.

    Use this to find anything by its text — a URL, a queue/topic name, an env
    key, a function call, a config value — in any language or stack.
    """
    from app.crawler.files import grep_repo

    scope = await _resolve_repos(repo, repos)
    if not scope:
        return {"error": "no repo/repos/group resolved", "pattern": pattern}
    per = max(5, max_results // max(1, len(scope)))
    matches: List[Dict[str, Any]] = []
    for r in scope:
        if len(matches) >= max_results:
            break
        try:
            for relpath, line_no, text in await grep_repo(
                r, pattern, glob=glob, ignore_case=ignore_case, max_results=per,
            ):
                matches.append({"repo": r, "file": relpath, "line": line_no, "text": text})
        except Exception as exc:  # noqa: BLE001
            logger.debug("crawler_grep: %s failed in %s: %s", pattern, r, exc)
    return {"pattern": pattern, "repos": scope, "count": len(matches),
            "matches": matches[:max_results]}


async def crawler_read_file(
    repo: str,
    path: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    max_lines: int = 400,
    with_anchors: bool = False,
) -> Dict[str, Any]:
    """Read a repository file by path, returning numbered lines (optional range).

    Use after crawler_grep / crawler_list_files to read the exact file the agent
    found, in any language or config format. Set ``with_anchors=True`` to prefix
    each line with a hashline anchor ``L<n>#<hash>`` (instead of ``<n>:``) so the
    line can be cited to edit_file's start_anchor/end_anchor for a drift-tolerant
    edit.
    """
    from app.crawler.files import read_repo_file

    try:
        content = await read_repo_file(repo, path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "repo": repo, "file": path}
    lines = content.splitlines()
    total = len(lines)
    s = max(1, start or 1)
    e = min(total, (end or (s + max_lines - 1)))
    if e - s + 1 > max_lines:
        e = s + max_lines - 1
    if with_anchors:
        from app.harness.hashline import line_hash
        body = "\n".join(
            f"L{i}#{line_hash(lines[i - 1])}: {lines[i - 1]}" for i in range(s, e + 1)
        )
    else:
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(s, e + 1))
    return {"repo": repo, "file": path, "start": s, "end": e,
            "total_lines": total, "content": body}


async def crawler_list_files(
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    glob: Optional[str] = None,
    limit: int = 400,
) -> Dict[str, Any]:
    """List repository files (relative paths), optionally filtered by a glob,
    across one repo, an explicit list, or a group."""
    from app.crawler.files import list_repo_files

    scope = await _resolve_repos(repo, repos)
    if not scope:
        return {"error": "no repo/repos/group resolved"}
    per = max(20, limit // max(1, len(scope)))
    files: List[Dict[str, str]] = []
    for r in scope:
        try:
            for relpath in await list_repo_files(r, glob=glob, limit=per):
                files.append({"repo": r, "file": relpath})
        except Exception as exc:  # noqa: BLE001
            logger.debug("crawler_list_files: %s failed: %s", r, exc)
    return {"repos": scope, "count": len(files), "files": files[:limit]}


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
        # Invalidate the file-paths cache so subsequent crawler_list_files calls
        # reflect any newly-added files in the freshly-indexed repo.
        try:
            from app.crawler.files import _file_paths_cache
            _file_paths_cache.clear(prefix=f"paths:{repo}")
        except Exception:  # noqa: BLE001
            pass
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
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Find where a symbol is defined in an indexed repository (or group of repos).

    Uses the knowledge-graph fast path (multi-repo when ``repos``/``group`` is
    given) and falls back to the LLM scan against the primary ``repo``.  Every
    cited (file, line) is verified on disk before being returned.

    Args:
        symbol:   Symbol name to find (function, class, constant, etc.).
        repo:     Repository directory name under REPOS_BASE_PATH.
        kind:     Optional type hint: "function", "class", "method", "constant".
        limit:    Maximum number of results to return (default 5).
        model_id: LLM model override.
        repos:    Optional explicit list of repos to search across.
        group:    Optional repo-group name (resolved to its member repos).

    Returns:
        dict with keys: symbol, repo, found (bool), count, results.
        Each result has: file, line, kind, context, body_handle, confidence,
        and (multi-repo only) repo.
    """
    from app.crawler import run_flow
    from app.crawler.flows import find_symbol_flow

    scope = await _resolve_repos(repo, repos)
    shared: Dict[str, Any] = {
        "repo": repo or (scope[0] if scope else repo),
        "repos": scope,
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
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Trace the call graph for a symbol — find who calls it or what it calls.

    With ``repos``/``group`` the walk spans multiple repositories, so the call
    graph continues across repo boundaries once cross-repo edges are linked.

    Args:
        symbol:    Symbol name to trace.
        repo:      Repository directory name under REPOS_BASE_PATH.
        direction: "callers" (who calls this) or "callees" (what it calls).
        depth:     Number of hops to trace (default 2).
        model_id:  LLM model override.
        repos:     Optional explicit list of repos to trace across.
        group:     Optional repo-group name (resolved to its member repos).

    Returns:
        dict with keys: symbol, repo, direction, total_edges, edges.
        Each edge: {from, to, label, file, line, confidence, body_handle,
        from_repo?, to_repo?}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import trace_path_flow

    scope = await _resolve_repos(repo, repos)
    shared: Dict[str, Any] = {
        "repo": repo or (scope[0] if scope else repo),
        "repos": scope,
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
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Find all callers of *symbol* (transitive, up to *depth* hops).

    Pure recursive-CTE walk over ``kg_edges`` — no LLM involvement. Spans the
    group's repos when ``repos``/``group`` is supplied.

    Args:
        symbol: Method/function name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        depth:  Maximum hop distance (clamped to 1..6, default 2).
        repos:  Optional explicit list of repos to trace across.
        group:  Optional repo-group name.

    Returns:
        dict {symbol, repo, direction='callers', total_edges, edges[]}
        Each edge: {from, to, label, file, line, confidence, hop, body_handle}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import trace_path_flow

    scope = await _resolve_repos(repo, repos)
    shared: Dict[str, Any] = {
        "repo":      repo or (scope[0] if scope else repo),
        "repos":     scope,
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
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Find everything *symbol* calls, transitively up to *depth* hops.

    Mirror of :func:`crawler_callers` but walks forward through ``kg_edges``.

    Args:
        symbol: Method/function name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        depth:  Maximum hop distance (clamped to 1..6, default 2).
        repos:  Optional explicit list of repos to trace across.
        group:  Optional repo-group name.

    Returns:
        dict {symbol, repo, direction='callees', total_edges, edges[]}.
    """
    from app.crawler import run_flow
    from app.crawler.flows import trace_path_flow

    scope = await _resolve_repos(repo, repos)
    shared: Dict[str, Any] = {
        "repo":      repo or (scope[0] if scope else repo),
        "repos":     scope,
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
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Impact analysis: "if I change X, what else breaks?"

    Combines callers, test coverage, and inbound references into one report.
    Drives refactoring safety questions an agent can answer from the graph
    alone — no LLM call. Spans a group's repos when ``repos``/``group`` is given,
    so cross-repo callers (e.g. a frontend caller of a backend symbol) surface.

    Args:
        symbol: Method/function/class name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        repos:  Optional explicit list of repos.
        group:  Optional repo-group name.

    Returns:
        dict {symbol, repo, callers[], tests[], references[],
              caller_count, test_count, reference_count}.
        Each entry: {from_qname, file, line, kind, confidence, body_handle, repo}.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    from app.crawler.handles import make_body_handle

    suffix = f"%::{symbol}"
    scope = await _resolve_repos(repo, repos)
    if not scope:
        scope = [repo]

    try:
        async with AsyncSessionLocal() as session:
            edges_sql = """
                SELECT e.kind, e.source_qname, e.file_path, e.line, e.confidence,
                       e.repo_name,
                       COALESCE(n.is_test, false) AS source_is_test
                FROM kg_edges e
                LEFT JOIN kg_nodes n
                  ON  n.repo_name      = e.repo_name
                  AND n.qualified_name = e.source_qname
                WHERE e.repo_name = ANY(:repos)
                  AND (e.target_qname = :sym OR e.target_qname LIKE :suffix)
                ORDER BY e.kind, e.file_path, e.line
                LIMIT 1000
            """
            rows = await session.execute(
                text(edges_sql),
                {"repos": scope, "sym": symbol, "suffix": suffix},
            )
            results = rows.fetchall()

        callers:    List[Dict[str, Any]] = []
        tests:      List[Dict[str, Any]] = []
        references: List[Dict[str, Any]] = []

        for kind, from_qname, file_path, line, confidence, src_repo, source_is_test in results:
            entry = {
                "from_qname": from_qname,
                "file":       file_path,
                "line":       int(line) if line else None,
                "kind":       kind,
                "confidence": confidence or "extracted",
            }
            if len(scope) > 1:
                entry["repo"] = src_repo
            if file_path and line:
                entry["body_handle"] = make_body_handle(
                    src_repo, file_path,
                    max(1, int(line) - 2), int(line) + 10,
                )

            if kind in ("calls", "calls_api"):
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


async def crawler_repo_map(
    repo: str,
    name_like: Optional[str] = None,
    limit: int = 60,
) -> Dict[str, Any]:
    """Cheap, names-only map of a repo's top-level symbols (no source bodies).

    Lets the agent orient ("what's in this repo?") with a tiny list and then
    fetch detail lazily via ``crawler_find_symbol`` / ``crawler_get_body``,
    instead of reading large chunks of the codebase. The full top-symbol list is
    cached per repo content-version (``files_sha256``), so it is computed once
    per repo version; the optional ``name_like`` substring is applied in-process.

    Args:
        repo:       Repository name under REPOS_BASE_PATH.
        name_like:  Optional case-insensitive substring to filter symbol names.
        limit:      Max symbols to return after filtering.

    Returns:
        ``{repo, version, count, symbols:[{name,kind,file}]}`` or ``{error,...}``.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    _FETCH_CAP = 2000  # hard ceiling for the cached full map
    try:
        async with AsyncSessionLocal() as session:
            ver = (await session.execute(
                text("SELECT files_sha256 FROM repo_abstractions WHERE repo_name = :r"),
                {"r": repo},
            )).scalar()
            if ver is None:
                return {"error": "repo not indexed", "repo": repo}

            cache_key = f"{repo}:{ver}"
            full = _repo_map_cache.get(cache_key)
            if full is None:
                rows = await session.execute(
                    text("""
                        SELECT name, kind, file_path
                        FROM kg_nodes
                        WHERE repo_name = :r
                          AND (exported = true
                               OR kind IN ('class','interface','function','method','enum','struct'))
                        ORDER BY file_path, line_start
                        LIMIT :cap
                    """),
                    {"r": repo, "cap": _FETCH_CAP},
                )
                full = [{"name": n, "kind": k, "file": f} for (n, k, f) in rows.fetchall()]
                _repo_map_cache.set(cache_key, full)

        needle = (name_like or "").strip().lower()
        filtered = [s for s in full if not needle or needle in str(s["name"]).lower()]
        symbols = filtered[: max(1, int(limit))]
        return {
            "repo": repo,
            "version": str(ver)[:12],
            "count": len(symbols),
            "total_available": len(filtered),
            "symbols": symbols,
            "note": "Names-only map. Use crawler_find_symbol / crawler_get_body for source; "
                    "pass name_like to narrow.",
        }
    except Exception as exc:
        logger.error("crawler_repo_map failed (repo=%s): %s", repo, exc)
        return {"error": str(exc), "repo": repo}


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


# ─────────────────────────────────────────────────────────────────────────────
# Project-intelligence tools — read the repo_docs rows produced by indexFlow's
# SummarizeModules / BuildProjectBrief / ExtractStandards nodes (migration 015).
# All are pure Postgres reads (no LLM): the intelligence is generated once at
# index time and served cheaply thereafter.
# ─────────────────────────────────────────────────────────────────────────────

async def _load_docs(
    repo: str, doc_type: str, doc_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return repo_docs ``content`` dicts for *repo*/*doc_type* (optionally one path)."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    sql = ("SELECT doc_path, content, generated_at FROM repo_docs "
           "WHERE repo_name = :r AND doc_type = :t")
    params: Dict[str, Any] = {"r": repo, "t": doc_type}
    if doc_path is not None:
        sql += " AND doc_path = :p"
        params["p"] = doc_path
    sql += " ORDER BY doc_path"
    async with AsyncSessionLocal() as session:
        rows = await session.execute(text(sql), params)
        return [
            {"doc_path": dp, "generated_at": ga.isoformat() if ga else None, **(c or {})}
            for dp, c, ga in rows.fetchall()
        ]


async def crawler_project_brief(repo: str) -> Dict[str, Any]:
    """Return the project brief, domain model and architecture narrative for *repo*.

    This is the crawler's top-level understanding of the project — what it does,
    its business domain, and how the system fits together. Call it to ground
    yourself before reasoning about or changing code.

    Returns ``{repo, brief, architecture, domain_model, generated_at}`` or
    ``{error: "repo not indexed"}``.
    """
    try:
        brief = await _load_docs(repo, "brief", "")
        if not brief:
            return {"error": "no project brief — repo not indexed or brief not generated",
                    "repo": repo}
        domain = await _load_docs(repo, "domain", "")
        out = {"repo": repo, **brief[0]}
        if domain:
            out["domain_model"] = domain[0].get("domain_model", [])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("crawler_project_brief failed (repo=%s): %s", repo, exc)
        return {"error": str(exc), "repo": repo}


async def crawler_module_doc(repo: str, path: Optional[str] = None) -> Dict[str, Any]:
    """Return the wiki-style doc for a module/directory in *repo*.

    With ``path`` omitted, returns the list of documented modules (path +
    responsibility) so you can pick one. With ``path`` set, returns that module's
    full doc (responsibility, key components, data flow, dependencies). ``path``
    matches by exact directory or unique suffix.
    """
    try:
        mods = await _load_docs(repo, "module")
        if not mods:
            return {"error": "no module docs — repo not indexed", "repo": repo}
        if not path:
            return {
                "repo": repo,
                "modules": [{"path": m["doc_path"], "responsibility": m.get("responsibility", "")}
                            for m in mods],
                "count": len(mods),
            }
        needle = path.replace("\\", "/").strip("/")
        match = next((m for m in mods if m["doc_path"] == needle), None) \
            or next((m for m in mods if m["doc_path"].endswith(needle)), None)
        if match is None:
            return {"error": f"no module doc for '{path}'", "repo": repo,
                    "available": [m["doc_path"] for m in mods][:50]}
        return {"repo": repo, **match}
    except Exception as exc:  # noqa: BLE001
        logger.error("crawler_module_doc failed (repo=%s path=%s): %s", repo, path, exc)
        return {"error": str(exc), "repo": repo}


async def crawler_find_feature(repo: str, query: str) -> Dict[str, Any]:
    """Map a user-facing feature/flow to the code that implements it.

    Searches the stored feature map (deterministic substring match over feature
    name, description and module list). Answers "where is X done?" semantically
    without an LLM call.

    Returns ``{repo, query, matches:[{feature, description, modules}]}``.
    """
    try:
        feats_doc = await _load_docs(repo, "features", "")
        if not feats_doc:
            return {"error": "no feature map — repo not indexed", "repo": repo}
        features = feats_doc[0].get("features", []) or []
        needle = (query or "").strip().lower()
        matches = []
        for f in features:
            if not isinstance(f, dict):
                continue
            hay = " ".join([
                str(f.get("feature", "")), str(f.get("description", "")),
                " ".join(str(m) for m in (f.get("modules", []) or [])),
            ]).lower()
            if not needle or needle in hay:
                matches.append(f)
        return {"repo": repo, "query": query, "count": len(matches),
                "matches": matches or features}
    except Exception as exc:  # noqa: BLE001
        logger.error("crawler_find_feature failed (repo=%s): %s", repo, exc)
        return {"error": str(exc), "repo": repo}


async def crawler_coding_standards(repo: str) -> Dict[str, Any]:
    """Return *repo*'s coding-conventions profile.

    Naming, file/folder layout, framework idioms, error handling, testing, and
    the frontend-vs-backend patterns the codebase actually uses. CALL THIS BEFORE
    writing or proposing code so your changes match the project's conventions.
    """
    try:
        std = await _load_docs(repo, "standards", "")
        if not std:
            return {"error": "no coding-standards profile — repo not indexed", "repo": repo}
        return {"repo": repo, **std[0]}
    except Exception as exc:  # noqa: BLE001
        logger.error("crawler_coding_standards failed (repo=%s): %s", repo, exc)
        return {"error": str(exc), "repo": repo}


async def crawler_find_references(
    symbol: str,
    repo: str,
    limit: int = 20,
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Find all references to *symbol* grouped by relation kind.

    Pure graph query — no LLM.  Useful for refactoring ("where is this used?").
    Spans a group's repos when ``repos``/``group`` is given.

    Args:
        symbol: Method/function/class name (bare or fully-qualified).
        repo:   Repository under REPOS_BASE_PATH.
        limit:  Max results per relation kind (default 20).
        repos:  Optional explicit list of repos.
        group:  Optional repo-group name.

    Returns:
        dict {symbol, repo, calls, imports, inherits, implements, tested_by}
        where each list contains {from_qname, file, line, confidence, body_handle}.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    from app.crawler.handles import make_body_handle

    suffix = f"%::{symbol}"
    scope = await _resolve_repos(repo, repos)
    if not scope:
        scope = [repo]

    sql = """
        SELECT kind, source_qname, file_path, line, confidence, repo_name
        FROM kg_edges
        WHERE repo_name = ANY(:repos)
          AND (target_qname = :sym OR target_qname LIKE :suffix)
        ORDER BY kind, file_path, line
        LIMIT 1000
    """

    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(sql),
                {"repos": scope, "sym": symbol, "suffix": suffix},
            )
            results = rows.fetchall()

        grouped: Dict[str, List[Dict[str, Any]]] = {
            "calls": [], "imports_from": [], "inherits": [],
            "implements": [], "tested_by": [], "references": [],
        }
        for kind, source_qname, file_path, line, confidence, src_repo in results:
            bucket = grouped.setdefault(kind, [])
            if len(bucket) >= limit:
                continue
            entry = {
                "from_qname": source_qname,
                "file":       file_path,
                "line":       int(line) if line else None,
                "confidence": confidence or "extracted",
            }
            if len(scope) > 1:
                entry["repo"] = src_repo
            if file_path and line:
                entry["body_handle"] = make_body_handle(
                    src_repo, file_path,
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
