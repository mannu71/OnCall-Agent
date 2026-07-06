"""codegraph admin API — list / inspect / reindex / delete codegraph-indexed repos.

Parallels the crawler endpoints (``/crawler/...``) but targets the codegraph store
so the Codebase Explorer can manage either backend. Thin HTTP layer over
``app.services.codegraph_admin``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import codegraph_admin

router = APIRouter(prefix="/codegraph", tags=["codegraph"])


@router.get("/repos", summary="List codegraph-indexed repositories")
async def list_repos() -> Dict[str, Any]:
    return {"repos": codegraph_admin.list_projects()}


@router.get("/repos/{repo}/files", summary="List indexed files for a codegraph repo")
async def list_files(repo: str) -> Dict[str, Any]:
    return {"files": codegraph_admin.list_files(repo)}


@router.get("/repos/{repo}/file-nodes", summary="Nodes + edges for one file")
async def file_nodes(repo: str, file: str = Query(..., description="File path")) -> Dict[str, Any]:
    return codegraph_admin.file_nodes(repo, file)


@router.get("/repos/{repo}/node", summary="Single node detail by qualified name")
async def get_node(repo: str, qualified_name: str = Query(...)) -> Dict[str, Any]:
    node = codegraph_admin.get_node(repo, qualified_name)
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


@router.post("/index/{repo}", summary="Reindex a codegraph repo (fast mode)")
async def reindex(repo: str) -> Dict[str, Any]:
    result = await codegraph_admin.reindex_project(repo)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.delete("/index/{repo}", summary="Delete a codegraph repo's index")
async def delete_index(repo: str) -> Dict[str, Any]:
    result = codegraph_admin.delete_project(repo)
    if result.get("error"):
        status = 404 if "not indexed" in str(result["error"]) else 500
        raise HTTPException(status_code=status, detail=result["error"])
    return result
