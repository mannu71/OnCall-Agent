"""MCP Configuration API routes."""
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field

from app.repositories import MCPConfigRepository
from app.core.exceptions import NotFoundException

router = APIRouter(prefix="/mcp-config", tags=["mcp-config"])
logger = logging.getLogger(__name__)

# Field description constants
_DESC_COMMAND = "Command to run the MCP server"
_DESC_ARGS = "Arguments for the command"
_DESC_ENV = "Environment variables"
_DESC_DESCRIPTION = "Server description"
_DESC_ICON = "Icon for the server"
_DESC_DISABLED = "Whether server is disabled"


# Pydantic models for request/response
class MCPServerConfig(BaseModel):
    """MCP Server configuration model."""
    command: str = Field(..., description=_DESC_COMMAND)
    args: List[str] = Field(default_factory=list, description=_DESC_ARGS)
    env: Dict[str, str] = Field(default_factory=dict, description=_DESC_ENV)
    description: Optional[str] = Field(None, description=_DESC_DESCRIPTION)
    icon: Optional[str] = Field(None, description=_DESC_ICON)
    disabled: Optional[bool] = Field(False, description=_DESC_DISABLED)


class MCPServerCreate(BaseModel):
    """Model for creating a new MCP server."""
    name: str = Field(..., description="Server name")
    command: str = Field(..., description=_DESC_COMMAND)
    args: List[str] = Field(default_factory=list, description=_DESC_ARGS)
    env: Dict[str, str] = Field(default_factory=dict, description=_DESC_ENV)
    description: Optional[str] = Field(None, description=_DESC_DESCRIPTION)
    icon: Optional[str] = Field(None, description=_DESC_ICON)
    disabled: Optional[bool] = Field(False, description=_DESC_DISABLED)


class MCPServerUpdate(BaseModel):
    """Model for updating an MCP server."""
    name: Optional[str] = Field(None, description="New server name (for rename)")
    command: Optional[str] = Field(None, description=_DESC_COMMAND)
    args: Optional[List[str]] = Field(None, description=_DESC_ARGS)
    env: Optional[Dict[str, str]] = Field(None, description=_DESC_ENV)
    description: Optional[str] = Field(None, description=_DESC_DESCRIPTION)
    icon: Optional[str] = Field(None, description=_DESC_ICON)
    disabled: Optional[bool] = Field(None, description=_DESC_DISABLED)


# Dependency to get MCP config repository
def get_mcp_config_repo() -> MCPConfigRepository:
    """Get MCP config repository dependency.
    
    Returns:
        MCPConfigRepository instance
    """
    return MCPConfigRepository()


@router.get("", response_model=Dict[str, Any])
async def get_mcp_config(
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Get full MCP configuration including servers, inputs, and input values.
    
    Returns:
        Full MCP configuration
    """
    return await repo.get_full_config()


@router.get("/servers", response_model=Dict[str, Any])
async def get_mcp_servers(
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Get all MCP servers.
    
    Returns:
        Dictionary of server name -> configuration
    """
    return await repo.get_servers()


@router.get("/servers/{server_name}", response_model=Dict[str, Any])
async def get_mcp_server(
    server_name: str,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Get a specific MCP server configuration.
    
    Args:
        server_name: Name of the server
        
    Returns:
        Server configuration
    """
    server = await repo.get_by_id(server_name)
    if not server:
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    return {"name": server_name, **server}


@router.post("/servers", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    server_data: MCPServerCreate,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Create a new MCP server.
    
    Args:
        server_data: Server configuration
        
    Returns:
        Created server configuration
    """
    if await repo.exists(server_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server '{server_data.name}' already exists"
        )
    
    server_dict = server_data.model_dump()
    saved = await repo.save(server_dict)
    logger.info(f"Created MCP server: {server_data.name}")
    return saved


@router.put("/servers/{server_name}", response_model=Dict[str, Any])
async def update_mcp_server(
    server_name: str,
    server_update: MCPServerUpdate,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Update an existing MCP server.
    
    Args:
        server_name: Name of the server to update
        server_update: Updated server configuration
        
    Returns:
        Updated server configuration
    """
    existing = await repo.get_by_id(server_name)
    if not existing:
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    
    update_data = server_update.model_dump(exclude_unset=True)
    
    # Handle rename
    new_name = update_data.pop("name", None)
    if new_name and new_name != server_name:
        # Check if new name already exists
        if await repo.exists(new_name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"MCP server '{new_name}' already exists"
            )
        # Delete old and create with new name
        await repo.delete(server_name)
        server_dict = {"name": new_name, **existing, **update_data}
    else:
        # Merge with existing config
        server_dict = {"name": server_name, **existing, **update_data}
    
    saved = await repo.save(server_dict)
    logger.info(f"Updated MCP server: {server_name}")
    return saved


@router.delete("/servers/{server_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_name: str,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Delete an MCP server.
    
    Args:
        server_name: Name of the server to delete
    """
    if not await repo.delete(server_name):
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    logger.info(f"Deleted MCP server: {server_name}")
    return None


@router.post("/save", response_model=Dict[str, Any])
async def save_full_config(
    config: Dict[str, Any],
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Save full MCP configuration.
    
    This endpoint allows saving the entire configuration at once,
    useful for migrating from the Electron file-based storage.
    
    Args:
        config: Full configuration to save
        
    Returns:
        Saved configuration
    """
    # Validate config structure
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configuration must be a dictionary"
        )
    
    # Ensure required fields exist
    config.setdefault("servers", {})
    
    saved = await repo.save_full_config(config)
    logger.info("Saved full MCP configuration")
    return saved
