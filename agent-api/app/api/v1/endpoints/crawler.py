"""Crawler API endpoints — thin HTTP layer over ``CrawlerService``."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.crawler_service import crawler_service

router = APIRouter(prefix="/crawler", tags=["crawler"])


def _raise_on_error(result: Dict[str, Any], *, status: int = 500) -> None:
    if "error" in result:
        raise HTTPException(status_code=status, detail=result["error"])


class IndexRequest(BaseModel):
    force: bool = Field(False, description="Bypass the skip-if-unchanged SHA check.")
    model_id: Optional[str] = None
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None


class FindRequest(BaseModel):
    name: str = Field(..., description="Symbol name to find.")
    repo: str = Field(..., description="Repository name under REPOS_BASE_PATH.")
    kind: Optional[str] = Field(None, description="Symbol type hint (function/class/method/constant).")
    limit: int = Field(5, ge=1, le=20)
    model_id: Optional[str] = None


class BodyRequest(BaseModel):
    handle: str = Field(..., description="Body handle from crawler_find_symbol.")
    page: int = Field(1, ge=1)


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


class DiffImpactRequest(BaseModel):
    files: List[str] = Field(default_factory=list, description="List of changed file paths.")
    symbols: List[str] = Field(default_factory=list, description="List of changed symbol names.")


@router.get("/repos", summary="List all indexed repositories")
async def list_indexed_repos() -> Dict[str, Any]:
    return await crawler_service.list_indexed_repos()


@router.post("/index/{repo}", summary="Index a repository")
async def index_repo(repo: str, body: IndexRequest) -> Dict[str, Any]:
    result = await crawler_service.index_repo(
        repo,
        force=body.force,
        model_id=body.model_id,
        include_patterns=body.include_patterns,
        exclude_patterns=body.exclude_patterns,
    )
    _raise_on_error(result)
    return result


@router.get("/index/{repo}", summary="Get cached repo overview")
async def get_index(repo: str) -> Dict[str, Any]:
    overview = await crawler_service.get_index_overview(repo)
    if overview is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repository '{repo}' has not been indexed. POST to /crawler/index/{repo} first.",
        )
    return overview


@router.post("/find", summary="Find a symbol definition")
async def find_symbol(body: FindRequest) -> Dict[str, Any]:
    result = await crawler_service.find_symbol(
        name=body.name,
        repo=body.repo,
        kind=body.kind,
        limit=body.limit,
        model_id=body.model_id,
    )
    _raise_on_error(result)
    return result


@router.post("/body", summary="Retrieve source lines by handle")
async def get_body(body: BodyRequest) -> Dict[str, Any]:
    result = await crawler_service.get_body(handle=body.handle, page=body.page)
    _raise_on_error(result, status=404)
    return result


@router.get("/runs", summary="List flow run logs")
async def list_runs(
    flow_name: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    return await crawler_service.list_runs(
        flow_name=flow_name,
        repo=repo,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", summary="Get a single flow run with full trace")
async def get_run(run_id: int) -> Dict[str, Any]:
    run = await crawler_service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"flow_run {run_id} not found.")
    return run


@router.get("/cache/stats", summary="LLM cache statistics")
async def cache_stats() -> Dict[str, Any]:
    return await crawler_service.cache_stats()


@router.delete("/cache", summary="Truncate LLM cache (admin)")
async def clear_cache() -> Dict[str, Any]:
    return await crawler_service.clear_cache()


@router.post("/trace", summary="Trace call-graph edges for a symbol")
async def trace_path(body: TraceRequest) -> Dict[str, Any]:
    result = await crawler_service.trace_path(
        symbol=body.symbol,
        repo=body.repo,
        direction=body.direction,
        depth=body.depth,
        model_id=body.model_id,
    )
    _raise_on_error(result)
    return result


@router.post("/search", summary="Semantic search over a repository")
async def search_semantic(body: SearchRequest) -> Dict[str, Any]:
    result = await crawler_service.search_semantic(
        query=body.query,
        repo=body.repo,
        limit=body.limit,
        model_id=body.model_id,
    )
    _raise_on_error(result)
    return result


@router.post("/investigate", summary="Root-cause analysis for an on-call alert")
async def investigate_alert(body: InvestigateRequest) -> Dict[str, Any]:
    result = await crawler_service.investigate_alert(
        alert=body.alert,
        repo=body.repo,
        model_id=body.model_id,
    )
    _raise_on_error(result)
    return result


@router.get("/repos/{repo}/files", summary="List all indexed files for a repository")
async def get_repo_files(
    repo: str,
    language: Optional[str] = Query(None, description="Filter by language"),
    with_errors_only: bool = Query(False, description="Only return files with parse errors"),
    limit: int = Query(200, ge=1, le=1000),
) -> Dict[str, Any]:
    result = await crawler_service.get_repo_files(
        repo,
        language=language,
        with_errors_only=with_errors_only,
        limit=limit,
    )
    _raise_on_error(result)
    return result


@router.get("/repos/{repo}/nodes", summary="Fetch knowledge graph nodes/edges for a file")
async def get_file_nodes(
    repo: str,
    file_path: str = Query(..., description="File path relative or absolute"),
) -> Dict[str, Any]:
    try:
        return await crawler_service.get_file_nodes(repo, file_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}") from exc


@router.get("/repos/{repo}/node", summary="Fetch metadata for a single knowledge graph node")
async def get_node(
    repo: str,
    qualified_name: str = Query(..., description="Fully-qualified symbol name"),
) -> Dict[str, Any]:
    result = await crawler_service.get_node(repo, qualified_name)
    _raise_on_error(result, status=404)
    return result


@router.get("/repos/{repo}/callers", summary="Find all callers of a symbol")
async def get_callers(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
    depth: int = Query(2, ge=1, le=5),
) -> Dict[str, Any]:
    result = await crawler_service.get_callers(repo, symbol, depth=depth)
    _raise_on_error(result)
    return result


@router.get("/repos/{repo}/callees", summary="Find all callees of a symbol")
async def get_callees(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
    depth: int = Query(2, ge=1, le=5),
) -> Dict[str, Any]:
    result = await crawler_service.get_callees(repo, symbol, depth=depth)
    _raise_on_error(result)
    return result


@router.get("/repos/{repo}/impact", summary="Impact analysis for a symbol")
async def get_impact(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
) -> Dict[str, Any]:
    result = await crawler_service.get_impact(repo, symbol)
    _raise_on_error(result)
    return result


@router.get("/repos/{repo}/references", summary="Find references to a symbol")
async def get_references(
    repo: str,
    symbol: str = Query(..., description="Symbol name (bare or qualified)"),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    result = await crawler_service.get_references(repo, symbol, limit=limit)
    _raise_on_error(result)
    return result


@router.post("/repos/{repo}/diff-impact", summary="Compute deterministic impact tree for a diff")
async def post_diff_impact(repo: str, body: DiffImpactRequest) -> Dict[str, Any]:
    try:
        return await crawler_service.diff_impact(repo, files=body.files, symbols=body.symbols)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database walk failed: {exc}") from exc
