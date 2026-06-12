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
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.find_symbol(
        name=symbol,
        repo=repo,
        kind=kind,
        limit=limit,
        model_id=model_id,
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
) -> Dict[str, Any]:
    from app.services.crawler_service import crawler_service

    return await crawler_service.trace_path(
        symbol=symbol,
        repo=repo,
        direction=direction,
        depth=depth,
        model_id=model_id,
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
