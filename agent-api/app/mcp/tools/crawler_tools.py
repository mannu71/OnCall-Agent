"""MCP / LangChain tool wrappers — thin delegates to ``CrawlerService``."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


async def crawler_index_repo(
    repo: str,
    force: bool = False,
    model_id: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.index_repo(
        repo,
        force=force,
        model_id=model_id,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )


async def crawler_find_symbol(
    symbol: str,
    repo: str,
    kind: Optional[str] = None,
    limit: int = 5,
    model_id: Optional[str] = None,
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.find_symbol(
        name=symbol,
        repo=repo,
        kind=kind,
        limit=limit,
        model_id=model_id,
        repos=repos,
    )


async def crawler_get_body(handle: str, page: int = 1) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_body(handle=handle, page=page)


async def crawler_trace_path(
    symbol: str,
    repo: str,
    direction: str = "callers",
    depth: int = 2,
    model_id: Optional[str] = None,
    repos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.trace_path(
        symbol=symbol,
        repo=repo,
        direction=direction,
        depth=depth,
        model_id=model_id,
        repos=repos,
    )


async def crawler_search_semantic(
    query: str,
    repo: str,
    limit: int = 10,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.search_semantic(
        query=query,
        repo=repo,
        limit=limit,
        model_id=model_id,
    )


async def crawler_investigate_alert(
    alert: str,
    repo: str,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.investigate_alert(
        alert=alert,
        repo=repo,
        model_id=model_id,
    )


async def crawler_callers(symbol: str, repo: str, depth: int = 2) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_callers(repo, symbol, depth=depth)


async def crawler_callees(symbol: str, repo: str, depth: int = 2) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_callees(repo, symbol, depth=depth)


async def crawler_impact(symbol: str, repo: str) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_impact(repo, symbol)


async def crawler_node(qualified_name: str, repo: str) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_node(repo, qualified_name)


async def crawler_repo_map(
    repo: str, name_like: Optional[str] = None, limit: int = 60,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_repo_map(repo, name_like=name_like, limit=limit)


async def crawler_files(
    repo: str,
    language: Optional[str] = None,
    with_errors_only: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_repo_files(
        repo,
        language=language,
        with_errors_only=with_errors_only,
        limit=limit,
    )


async def crawler_find_references(
    symbol: str,
    repo: str,
    limit: int = 20,
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_references(repo, symbol, limit=limit)


# ── Project-intelligence delegates (repo_docs, migration 015) ────────────────

async def crawler_project_brief(repo: str) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_project_brief(repo)


async def crawler_module_doc(repo: str, path: Optional[str] = None) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_module_docs(repo, path=path)


async def crawler_coding_standards(repo: str) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.get_coding_standards(repo)


async def crawler_find_feature(repo: str, query: str) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.find_feature(repo, query)


# Generic coding-agent file tools (grep / read / list) — delegate to flows.

async def crawler_grep(
    pattern: str,
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    glob: Optional[str] = None,
    ignore_case: bool = True,
    max_results: int = 80,
) -> Dict[str, Any]:
    from app.services.crawler_flows import crawler_grep as _grep

    return await _grep(pattern, repo=repo, repos=repos, glob=glob,
                       ignore_case=ignore_case, max_results=max_results)


async def crawler_read_file(
    repo: str, path: str, start: Optional[int] = None, end: Optional[int] = None,
    with_anchors: bool = False,
) -> Dict[str, Any]:
    from app.services.crawler_flows import crawler_read_file as _read

    return await _read(repo, path, start=start, end=end, with_anchors=with_anchors)


async def crawler_list_files(
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    glob: Optional[str] = None,
    limit: int = 400,
) -> Dict[str, Any]:
    """List repository file paths filtered by a glob pattern.

    IMPORTANT: Always pass a glob filter (e.g. glob='*.cs', glob='src/Services/**').
    Omitting glob walks the entire repo — slow (10-15 s) and returns too many files
    to reason over. Use crawler_grep to find files by content, or crawler_repo_map
    for a fast symbol-level orientation of the repo.
    """
    from app.services.crawler_flows import crawler_list_files as _ls

    return await _ls(repo=repo, repos=repos, glob=glob, limit=limit)
