"""Code analyzer support endpoints — thin HTTP layer over filesystem repo discovery."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import repo_discovery

router = APIRouter(prefix="/code-analyzer", tags=["code-analyzer"])


class RepoInfo(BaseModel):
    name: str
    path: str
    is_git: bool
    detected_languages: List[str]
    suggested_language: str
    file_count_sample: int


class RepoListResponse(BaseModel):
    base_path: str
    base_exists: bool
    repos: List[RepoInfo]


def _repo_from_dict(data: dict) -> RepoInfo:
    return RepoInfo(
        name=data["name"],
        path=data.get("path", ""),
        is_git=data.get("is_git", False),
        detected_languages=data.get("detected_languages", []),
        suggested_language=data.get("suggested_language", "python"),
        file_count_sample=data.get("file_count_sample", 0),
    )


@router.get("/repos", response_model=RepoListResponse)
async def list_repos(
    refresh: bool = Query(
        False,
        description="Bypass the in-memory cache and walk the filesystem now.",
    ),
) -> RepoListResponse:
    payload = await repo_discovery.list_discovered_repos(refresh=refresh)

    return RepoListResponse(
        base_path=payload["base_path"],
        base_exists=payload["base_exists"],
        repos=[_repo_from_dict(r) for r in payload.get("repos", [])],
    )


@router.get("/repos/{repo_name}", response_model=RepoInfo)
async def get_repo(repo_name: str) -> RepoInfo:
    result = await repo_discovery.get_discovered_repo(repo_name)
    return _repo_from_dict(result)
