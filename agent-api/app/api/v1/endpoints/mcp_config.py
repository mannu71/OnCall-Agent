"""MCP Configuration API routes."""
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field

from app.infrastructure.persistence import MCPConfigRepository
from app.application.exceptions import NotFoundException
from app.api.deps import get_mcp_config_repo

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


@router.get("", response_model=Dict[str, Any])
async def get_mcp_config(
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Get full MCP configuration including servers, inputs, and input values."""
    servers = await repo.list_all(include_disabled=True)
    servers_dict = {server["name"]: {k: v for k, v in server.items() if k != "name"} for server in servers}
    return {"servers": servers_dict}


@router.get("/servers", response_model=Dict[str, Any])
async def get_mcp_servers(
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Get all MCP servers."""
    servers = await repo.list_all(include_disabled=True)
    return {server["name"]: {k: v for k, v in server.items() if k != "name"} for server in servers}


@router.post("/test/{server_name}", response_model=Dict[str, Any])
async def test_mcp_server(
    server_name: str,
    server_config: Optional[MCPServerConfig] = None,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Test MCP server connection."""
    from app.services.mcp_client_manager import MCPClientManager
    
    if server_config:
        config = server_config.model_dump()
    else:
        config = await repo.get_by_name(server_name)
        if not config:
            raise NotFoundException(
                message=f"MCP server '{server_name}' not found",
                details={"server_name": server_name}
            )
    
    manager = MCPClientManager()
    test_server_id = f"test_{server_name}"
    
    try:
        success = await manager.connect_server(test_server_id, config)
        
        if success:
            tools = manager.get_available_tools(test_server_id)
            tool_count = len(tools.get(test_server_id, []))
            
            return {
                "success": True,
                "message": f"Successfully connected to MCP server '{server_name}'",
                "tool_count": tool_count,
                "tools": tools.get(test_server_id, [])
            }
        else:
            return {
                "success": False,
                "error": f"Failed to connect to MCP server '{server_name}'"
            }
            
    except Exception as e:
        logger.error(f"Error testing MCP server '{server_name}': {e}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        await manager.disconnect_server(test_server_id)


@router.get("/servers/{server_name}", response_model=Dict[str, Any])
async def get_mcp_server(
    server_name: str,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Get a specific MCP server configuration."""
    server = await repo.get_by_name(server_name)
    if not server:
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    return server


@router.post("/servers", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    server_data: MCPServerCreate,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Create a new MCP server."""
    if await repo.exists(server_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server '{server_data.name}' already exists"
        )
    
    server_dict = server_data.model_dump()
    if 'disabled' in server_dict:
        server_dict['enabled'] = not server_dict.pop('disabled')
    
    saved = await repo.create(server_dict)
    logger.info(f"Created MCP server: {server_data.name}")
    return saved


@router.put("/servers/{server_name}", response_model=Dict[str, Any])
async def update_mcp_server(
    server_name: str,
    server_update: MCPServerUpdate,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Update an existing MCP server."""
    existing = await repo.get_by_name(server_name)
    if not existing:
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    
    update_data = server_update.model_dump(exclude_unset=True)
    
    if 'disabled' in update_data:
        update_data['enabled'] = not update_data.pop('disabled')
    
    new_name = update_data.get("name")
    if new_name and new_name != server_name:
        if await repo.exists(new_name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"MCP server '{new_name}' already exists"
            )
    
    saved = await repo.update(server_name, update_data)
    logger.info(f"Updated MCP server: {server_name}")
    return saved


@router.delete("/servers/{server_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_name: str,
    repo: MCPConfigRepository = Depends(get_mcp_config_repo)
):
    """Delete an MCP server."""
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
    """Save full MCP configuration."""
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configuration must be a dictionary"
        )
    
    servers = config.get("servers", {})
    saved_servers = []
    
    for server_name, server_config in servers.items():
        server_data = {"name": server_name, **server_config}
        
        if 'disabled' in server_data:
            server_data['enabled'] = not server_data.pop('disabled')
        
        if await repo.exists(server_name):
            saved = await repo.update(server_name, server_data)
        else:
            saved = await repo.create(server_data)
        saved_servers.append(saved)
    
    logger.info(f"Saved full MCP configuration ({len(saved_servers)} servers)")
    
    return {
        "servers": {s["name"]: {k: v for k, v in s.items() if k != "name"} for s in saved_servers}
    }


@router.get("/inputs", response_model=List[Any])
async def get_mcp_inputs():
    """Get MCP inputs configuration."""
    return []


@router.get("/input-values", response_model=Dict[str, Any])
async def get_mcp_input_values():
    """Get MCP input values."""
    return {}