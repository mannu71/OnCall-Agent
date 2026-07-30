"""codegraph admin API — list / inspect / reindex / delete codegraph-indexed repos.

Parallels the crawler endpoints (``/crawler/...``) but targets the codegraph store
so the Codebase Explorer can manage either backend. Thin HTTP layer over
``app.services.codegraph_admin``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app.services import codegraph_admin, repo_git

router = APIRouter(prefix="/codegraph", tags=["codegraph"])

# `repo_git` and `codegraph_admin` are synchronous: they shell out to git and
# read SQLite. Called directly from an async handler they block the event loop
# for the whole duration — and a `git fetch` over the repo bind mount is
# seconds, not milliseconds — freezing every other request in the process,
# including live SSE runs. Every such call goes through asyncio.to_thread.


@router.get("/repos", summary="List codegraph-indexed repositories")
async def list_repos() -> Dict[str, Any]:
    return {"repos": await asyncio.to_thread(codegraph_admin.list_projects)}


@router.get("/repos/{repo}/files", summary="List indexed files for a codegraph repo")
async def list_files(repo: str) -> Dict[str, Any]:
    return {"files": await asyncio.to_thread(codegraph_admin.list_files, repo)}


@router.get("/repos/{repo}/file-nodes", summary="Nodes + edges for one file")
async def file_nodes(repo: str, file: str = Query(..., description="File path")) -> Dict[str, Any]:
    return await asyncio.to_thread(codegraph_admin.file_nodes, repo, file)


@router.get("/repos/{repo}/node", summary="Single node detail by qualified name")
async def get_node(repo: str, qualified_name: str = Query(...)) -> Dict[str, Any]:
    node = await asyncio.to_thread(codegraph_admin.get_node, repo, qualified_name)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


@router.get("/repos/{repo}/layout", summary="3D force-directed graph layout for a codegraph repo")
async def get_layout(
    repo: str,
    level: str = Query("overview", description="'overview' (cluster centroids) or 'detail'"),
    center_node: Optional[str] = Query(None, description="Qualified name to center detail layout on"),
    radius: int = Query(2),
    max_nodes: int = Query(2000),
) -> Dict[str, Any]:
    result = await codegraph_admin.get_layout(
        repo, level=level, center_node=center_node, radius=radius, max_nodes=max_nodes
    )
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/repos/{repo}/branches", summary="List git branches for a repo on disk")
async def list_branches(repo: str) -> Dict[str, Any]:
    """List local branches + current state for a repo (fast, ref reads only).

    Remote branches are deliberately not enumerated here — a busy repo can have
    hundreds and reading them off a slow bind mount is expensive. The Fetch
    action loads/refreshes them instead. Non-git dirs return
    ``{is_git: false, ...}`` (not an error) so the UI can simply hide the picker.
    """
    try:
        return await asyncio.to_thread(
            repo_git.list_branches, repo, include_remote=False
        )
    except repo_git.RepoGitError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/repos/{repo}/fetch", summary="git fetch --prune, then refresh branch list")
async def fetch_branches(repo: str) -> Dict[str, Any]:
    """Fetch remote refs, then return the refreshed listing.

    A fetch failure (no creds / offline) is reported as ``{ok: false, error}``
    with the still-usable local branch list, not an HTTP error.
    """
    try:
        return await asyncio.to_thread(repo_git.fetch, repo)
    except repo_git.RepoGitError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/repos/{repo}/checkout", summary="Switch a repo to another branch")
async def checkout_branch(
    repo: str,
    branch: str = Body(..., embed=True, description="Local or remote-tracking branch name"),
) -> Dict[str, Any]:
    """Check out *branch* in the repo on disk (refuses on a dirty tree).

    Does not reindex — the caller chains ``POST /codegraph/index/{repo}`` after a
    successful switch. Returns 409 when the working tree is dirty, 400 for an
    unknown branch, 404 for a non-git repo.
    """
    try:
        result = await asyncio.to_thread(repo_git.checkout_branch, repo, branch)
    except repo_git.RepoGitError as exc:
        msg = str(exc)
        status = 400 if msg.startswith(("unknown branch", "branch is required")) else 404
        raise HTTPException(status_code=status, detail=msg)
    if not result.get("ok"):
        if result.get("reason") == "dirty":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"'{repo}' has uncommitted changes — commit, stash, or discard "
                        "them before switching branch."
                    ),
                    "dirty_files": result.get("dirty_files", []),
                    "current": result.get("current"),
                },
            )
        raise HTTPException(status_code=500, detail=result.get("error", "checkout failed"))
    return result


@router.post("/index/{repo}", summary="Reindex a codegraph repo (fast mode)")
async def reindex(repo: str) -> Dict[str, Any]:
    result = await codegraph_admin.reindex_project(repo)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.delete("/index/{repo}", summary="Delete a codegraph repo's index")
async def delete_index(repo: str) -> Dict[str, Any]:
    result = await asyncio.to_thread(codegraph_admin.delete_project, repo)
    if result.get("error"):
        status = 404 if "not indexed" in str(result["error"]) else 500
        raise HTTPException(status_code=status, detail=result["error"])
    return result
