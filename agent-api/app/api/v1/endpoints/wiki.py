"""Wiki API endpoints — helpers for the Wiki output node's config panel.

Currently exposes wiki discovery so the node can auto-populate the wikis
available for an org/project instead of making the user hunt for the URL.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.azure_wiki_client import (
    AzureWikiClient,
    AzureWikiAuthenticationError,
    AzureWikiConnectionError,
    AzureWikiNotFoundError,
    AzureWikiError,
)
from app.workflow.executor.handlers.wiki import resolve_ado_pat

router = APIRouter(prefix="/wiki", tags=["wiki"])


class CredsRequest(BaseModel):
    pat: str = Field("", description="Explicit PAT (optional; falls back to env/global).")
    tokenVar: str = Field("ADO_WIKI_PAT", description="Env var name to read the PAT from.")


class ListProjectsRequest(CredsRequest):
    organization: str = Field(..., description="Azure DevOps organization name.")


class ListWikisRequest(CredsRequest):
    organization: str = Field(..., description="Azure DevOps organization name.")
    project: str = Field(..., description="Azure DevOps project name.")


def _require_pat(req: CredsRequest) -> str:
    pat = resolve_ado_pat(req.pat, req.tokenVar)
    if not pat:
        raise HTTPException(
            status_code=400,
            detail="No Azure DevOps PAT configured. Set a PAT on the node, an env var, or global credentials.",
        )
    return pat


def _map_ado_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, AzureWikiAuthenticationError):
        return HTTPException(status_code=401, detail="Authentication failed. Check the PAT and its scopes.")
    if isinstance(exc, AzureWikiNotFoundError):
        return HTTPException(status_code=404, detail="Not found for the given organization/project.")
    if isinstance(exc, AzureWikiConnectionError):
        return HTTPException(status_code=502, detail=f"Could not reach Azure DevOps: {exc}")
    if isinstance(exc, AzureWikiError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.post("/organizations")
async def list_organizations(req: CredsRequest) -> Dict[str, Any]:
    """List the Azure DevOps organizations available to the PAT's owner."""
    pat = _require_pat(req)
    try:
        async with AzureWikiClient(pat=pat) as client:
            orgs = await client.list_organizations()
        return {"organizations": orgs}
    except AzureWikiError as e:
        raise _map_ado_errors(e)


@router.post("/projects")
async def list_projects(req: ListProjectsRequest) -> Dict[str, Any]:
    """List the projects in an Azure DevOps organization."""
    organization = req.organization.strip()
    if not organization:
        raise HTTPException(status_code=400, detail="Organization is required.")
    pat = _require_pat(req)
    try:
        async with AzureWikiClient(pat=pat) as client:
            projects = await client.list_projects(organization)
        return {"projects": projects}
    except AzureWikiError as e:
        raise _map_ado_errors(e)


@router.post("/wikis")
async def list_wikis(req: ListWikisRequest) -> Dict[str, Any]:
    """List the wikis available in an Azure DevOps project.

    Returns ``{"wikis": [{id, name, type, url}]}`` where ``url`` is the
    canonical value the Wiki node stores in its Wiki URL field.
    """
    organization = req.organization.strip()
    project = req.project.strip()
    if not organization or not project:
        raise HTTPException(status_code=400, detail="Organization and Project are required.")

    pat = _require_pat(req)
    try:
        async with AzureWikiClient(pat=pat) as client:
            wikis: List[Dict[str, Any]] = await client.list_wikis(organization, project)
        return {"wikis": wikis}
    except AzureWikiError as e:
        raise _map_ado_errors(e)
