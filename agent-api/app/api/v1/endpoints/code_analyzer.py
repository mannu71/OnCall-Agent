"""Code analyzer support endpoints — thin HTTP layer over ``CrawlerService``."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.crawler_service import crawler_service

router = APIRouter(prefix="/code-analyzer", tags=["code-analyzer"])


class RepoInfo(BaseModel):
    name: str
    path: str
    is_git: bool
    detected_languages: List[str]
    suggested_language: str
    file_count_sample: int
    indexed: bool = False
    files_indexed: Optional[int] = None
    model_id: Optional[str] = None
    generated_at: Optional[str] = None
    db_only: bool = False


class RepoListResponse(BaseModel):
    base_path: str
    base_exists: bool
    repos: List[RepoInfo]
    indexed_count: Optional[int] = None


def _repo_from_dict(data: dict) -> RepoInfo:
    return RepoInfo(
        name=data["name"],
        path=data.get("path", ""),
        is_git=data.get("is_git", False),
        detected_languages=data.get("detected_languages", []),
        suggested_language=data.get("suggested_language", "python"),
        file_count_sample=data.get("file_count_sample", 0),
        indexed=data.get("indexed", False),
        files_indexed=data.get("files_indexed"),
        model_id=data.get("model_id"),
        generated_at=data.get("generated_at"),
        db_only=data.get("db_only", False),
    )


@router.get("/repos", response_model=RepoListResponse)
async def list_repos(
    refresh: bool = Query(
        False,
        description="Bypass the in-memory cache and walk the filesystem now.",
    ),
    unified: bool = Query(
        True,
        description="Merge filesystem discovery with DB index metadata.",
    ),
) -> RepoListResponse:
    if unified:
        payload = await crawler_service.list_repos_unified(refresh=refresh)
    else:
        payload = await crawler_service.discover_filesystem_repos(refresh=refresh)

    return RepoListResponse(
        base_path=payload["base_path"],
        base_exists=payload["base_exists"],
        repos=[_repo_from_dict(r) for r in payload.get("repos", [])],
        indexed_count=payload.get("indexed_count"),
    )


@router.get("/repos/{repo_name}", response_model=RepoInfo)
async def get_repo(repo_name: str) -> RepoInfo:
    result = await crawler_service.get_filesystem_repo(repo_name)
    return _repo_from_dict(result)
