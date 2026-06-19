"""Agent-profile API routes.

CRUD over reusable agent profiles (see migration ``025_agent_profiles.sql`` and
``app/infrastructure/persistence/agent_profile_repository.py``) plus read-only
catalog endpoints the UI uses to populate the profile / output-schema / capability
pickers. Builtin profiles are read-only.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.infrastructure.persistence import agent_profile_repository

router = APIRouter(prefix="/agent-profiles", tags=["agent-profiles"])
logger = logging.getLogger(__name__)


class AgentProfileBody(BaseModel):
    description: Optional[str] = Field(default=None)
    role_prompt: Optional[str] = Field(default=None)
    capabilities: Optional[List[str]] = Field(default=None)
    default_tools: Optional[List[str]] = Field(default=None)
    output_schema: Optional[str] = Field(default=None)
    default_policies: Optional[List[Dict[str, Any]]] = Field(default=None)
    deep_features: Optional[Dict[str, Any]] = Field(default=None)


@router.get("", response_model=Dict[str, Any])
async def list_profiles() -> Dict[str, Any]:
    """List all agent profiles."""
    profiles = await agent_profile_repository.list()
    return {"success": True, "profiles": profiles}


@router.get("/catalog", response_model=Dict[str, Any])
async def catalog() -> Dict[str, Any]:
    """Return the building blocks for the profile editor (schemas + capabilities)."""
    from app.workflow.strategies.react.output_registry import schema_names
    from app.harness import capabilities as caps
    return {
        "success": True,
        "output_schemas": schema_names(),
        "capabilities": caps.all_ids(),
    }


@router.get("/{name}", response_model=Dict[str, Any])
async def get_profile(name: str) -> Dict[str, Any]:
    profile = await agent_profile_repository.get(name)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"agent profile '{name}' not found")
    return {"success": True, "profile": profile}


@router.put("/{name}", response_model=Dict[str, Any])
async def upsert_profile(name: str, body: AgentProfileBody) -> Dict[str, Any]:
    """Create or update a (non-builtin) agent profile."""
    try:
        profile = await agent_profile_repository.upsert(
            name,
            description=body.description,
            role_prompt=body.role_prompt,
            capabilities=body.capabilities,
            default_tools=body.default_tools,
            output_schema=body.output_schema,
            default_policies=body.default_policies,
            deep_features=body.deep_features,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"success": True, "profile": profile}


@router.delete("/{name}", response_model=Dict[str, Any])
async def delete_profile(name: str) -> Dict[str, Any]:
    try:
        deleted = await agent_profile_repository.delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"agent profile '{name}' not found")
    return {"success": True, "deleted": name}
