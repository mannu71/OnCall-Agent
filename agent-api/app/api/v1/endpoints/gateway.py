"""LLM + MCP Gateway API routes.

Per-role assignments for the gateway: which registered LLM config powers each
role (agent | crawler | subagent), and which registered MCP servers form the
agent's default toolbox (used only when a workflow wires no tool nodes).
"""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.infrastructure.persistence import (
    llm_config_repository,
    mcp_config_repository,
    model_role_repository,
    mcp_role_repository,
)
from app.infrastructure.persistence.model_role_repository import VALID_ROLES
from app.infrastructure.persistence.mcp_role_repository import VALID_MCP_ROLES

router = APIRouter(prefix="/gateway", tags=["gateway"])
logger = logging.getLogger(__name__)


class ModelRoleRequest(BaseModel):
    llm_config_name: str = Field(..., description="Name of an existing LLM config")


class MCPRoleRequest(BaseModel):
    server_names: List[str] = Field(
        default_factory=list, description="Names of existing MCP servers"
    )


async def _model_roles_map() -> Dict[str, Optional[str]]:
    """Build the full role -> config_name map with every valid role present."""
    assigned = await model_role_repository.list_all()
    return {role: assigned.get(role) for role in sorted(VALID_ROLES)}


async def _mcp_roles_map() -> Dict[str, List[str]]:
    """Build the full MCP role -> server names map with every valid role present."""
    assigned = await mcp_role_repository.list_all()
    return {role: assigned.get(role, []) for role in sorted(VALID_MCP_ROLES)}


def _validate_model_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid model role '{role}'. Valid roles: {sorted(VALID_ROLES)}",
        )


def _validate_mcp_role(role: str) -> None:
    if role not in VALID_MCP_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid MCP role '{role}'. Valid roles: {sorted(VALID_MCP_ROLES)}",
        )


# ---------------------------------------------------------------------------
# Model roles
# ---------------------------------------------------------------------------
@router.get("/model-roles", response_model=Dict[str, Optional[str]])
async def get_model_roles():
    """Return the LLM config assigned to each role (null when unassigned)."""
    return await _model_roles_map()


@router.put("/model-roles/{role}", response_model=Dict[str, Optional[str]])
async def set_model_role(role: str, request: ModelRoleRequest):
    """Assign an LLM config to a role."""
    _validate_model_role(role)
    if not await llm_config_repository.exists(request.llm_config_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{request.llm_config_name}' not found",
        )
    await model_role_repository.upsert(role, request.llm_config_name)
    logger.info("Assigned model role %s -> %s", role, request.llm_config_name)
    return await _model_roles_map()


@router.delete("/model-roles/{role}", response_model=Dict[str, Optional[str]])
async def clear_model_role(role: str):
    """Clear a role's LLM assignment (back to default)."""
    _validate_model_role(role)
    await model_role_repository.delete(role)
    logger.info("Cleared model role %s", role)
    return await _model_roles_map()


# ---------------------------------------------------------------------------
# MCP roles
# ---------------------------------------------------------------------------
@router.get("/mcp-roles", response_model=Dict[str, List[str]])
async def get_mcp_roles():
    """Return the default MCP servers assigned to each role."""
    return await _mcp_roles_map()


@router.put("/mcp-roles/{role}", response_model=Dict[str, List[str]])
async def set_mcp_role(role: str, request: MCPRoleRequest):
    """Replace a role's default MCP server set."""
    _validate_mcp_role(role)
    for name in request.server_names:
        if not await mcp_config_repository.exists(name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"MCP server '{name}' not found",
            )
    await mcp_role_repository.set_for_role(role, request.server_names)
    logger.info("Assigned MCP role %s -> %s", role, request.server_names)
    return await _mcp_roles_map()


@router.delete("/mcp-roles/{role}", response_model=Dict[str, List[str]])
async def clear_mcp_role(role: str):
    """Clear a role's default MCP servers."""
    _validate_mcp_role(role)
    await mcp_role_repository.clear(role)
    logger.info("Cleared MCP role %s", role)
    return await _mcp_roles_map()
