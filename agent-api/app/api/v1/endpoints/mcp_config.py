"""MCP Configuration API routes."""
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field

from app.repositories.db_repository import db_repository
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


# No dependency needed - using singleton db_repository directly


@router.get("", response_model=Dict[str, Any])
async def get_mcp_config():
    """Get full MCP configuration including servers, inputs, and input values.
    
    Returns:
        Full MCP configuration with servers
    """
    servers = await db_repository.list_mcp_servers(include_disabled=True)
    # Convert list to dict format for compatibility
    servers_dict = {server["name"]: {k: v for k, v in server.items() if k != "name"} for server in servers}
    return {"servers": servers_dict}


@router.get("/servers", response_model=Dict[str, Any])
async def get_mcp_servers():
    """Get all MCP servers.
    
    Returns:
        Dictionary of server name -> configuration
    """
    servers = await db_repository.list_mcp_servers(include_disabled=True)
    # Convert list to dict format for compatibility
    return {server["name"]: {k: v for k, v in server.items() if k != "name"} for server in servers}


@router.post("/test/{server_name}", response_model=Dict[str, Any])
async def test_mcp_server(
    server_name: str,
    server_config: Optional[MCPServerConfig] = None
):
    """Test MCP server connection.
    
    Tests the connection to an MCP server by attempting to connect
    and list available tools.
    
    Args:
        server_name: Name of the server to test
        server_config: Optional server configuration to test (uses stored config if not provided)
        
    Returns:
        Test result with success status and available tools
    """
    from app.services.mcp_client_manager import MCPClientManager
    
    # Get config from request body or from stored configuration
    if server_config:
        config = server_config.model_dump()
    else:
        config = await db_repository.get_mcp_server_by_name(server_name)
        if not config:
            raise NotFoundException(
                message=f"MCP server '{server_name}' not found",
                details={"server_name": server_name}
            )
    
    # Create a temporary manager to test connection
    manager = MCPClientManager()
    test_server_id = f"test_{server_name}"
    
    try:
        # Attempt to connect
        success = await manager.connect_server(test_server_id, config)
        
        if success:
            # Get available tools
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
        # Ensure cleanup
        await manager.disconnect_server(test_server_id)


@router.get("/servers/{server_name}", response_model=Dict[str, Any])
async def get_mcp_server(server_name: str):
    """Get a specific MCP server configuration.
    
    Args:
        server_name: Name of the server
        
    Returns:
        Server configuration
    """
    server = await db_repository.get_mcp_server_by_name(server_name)
    if not server:
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    return server


@router.post("/servers", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_mcp_server(server_data: MCPServerCreate):
    """Create a new MCP server.
    
    Args:
        server_data: Server configuration
        
    Returns:
        Created server configuration
    """
    if await db_repository.mcp_server_exists(server_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server '{server_data.name}' already exists"
        )
    
    server_dict = server_data.model_dump()
    # Convert 'disabled' to 'enabled'
    if 'disabled' in server_dict:
        server_dict['enabled'] = not server_dict.pop('disabled')
    
    saved = await db_repository.create_mcp_server(server_dict)
    logger.info(f"Created MCP server: {server_data.name}")
    return saved


@router.put("/servers/{server_name}", response_model=Dict[str, Any])
async def update_mcp_server(
    server_name: str,
    server_update: MCPServerUpdate
):
    """Update an existing MCP server.
    
    Args:
        server_name: Name of the server to update
        server_update: Updated server configuration
        
    Returns:
        Updated server configuration
    """
    existing = await db_repository.get_mcp_server_by_name(server_name)
    if not existing:
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    
    update_data = server_update.model_dump(exclude_unset=True)
    
    # Convert 'disabled' to 'enabled'
    if 'disabled' in update_data:
        update_data['enabled'] = not update_data.pop('disabled')
    
    # Handle rename
    new_name = update_data.get("name")
    if new_name and new_name != server_name:
        # Check if new name already exists
        if await db_repository.mcp_server_exists(new_name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"MCP server '{new_name}' already exists"
            )
    
    saved = await db_repository.update_mcp_server(server_name, update_data)
    logger.info(f"Updated MCP server: {server_name}")
    return saved


@router.delete("/servers/{server_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(server_name: str):
    """Delete an MCP server.
    
    Args:
        server_name: Name of the server to delete
    """
    if not await db_repository.delete_mcp_server(server_name):
        raise NotFoundException(
            message=f"MCP server '{server_name}' not found",
            details={"server_name": server_name}
        )
    logger.info(f"Deleted MCP server: {server_name}")
    return None


@router.post("/save", response_model=Dict[str, Any])
async def save_full_config(config: Dict[str, Any]):
    """Save full MCP configuration.
    
    This endpoint allows saving the entire configuration at once,
    useful for migrating from file-based storage.
    
    Args:
        config: Full configuration with 'servers' key
        
    Returns:
        Saved configuration
    """
    # Validate config structure
    if not isinstance(config, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configuration must be a dictionary"
        )
    
    servers = config.get("servers", {})
    saved_servers = []
    
    # Save each server
    for server_name, server_config in servers.items():
        server_data = {"name": server_name, **server_config}
        
        # Convert 'disabled' to 'enabled' if present
        if 'disabled' in server_data:
            server_data['enabled'] = not server_data.pop('disabled')
        
        # Check if exists and update or create
        if await db_repository.mcp_server_exists(server_name):
            saved = await db_repository.update_mcp_server(server_name, server_data)
        else:
            saved = await db_repository.create_mcp_server(server_data)
        saved_servers.append(saved)
    
    logger.info(f"Saved full MCP configuration ({len(saved_servers)} servers)")
    
    # Return in the expected format
    return {
        "servers": {s["name"]: {k: v for k, v in s.items() if k != "name"} for s in saved_servers}
    }


@router.get("/inputs", response_model=List[Any])
async def get_mcp_inputs():
    """Get MCP inputs configuration.
    
    Returns empty list for database storage (inputs not implemented yet).
    
    Returns:
        Empty list
    """
    return []


@router.get("/input-values", response_model=Dict[str, Any])
async def get_mcp_input_values():
    """Get MCP input values.
    
    Returns empty object for database storage (input values not implemented yet).
    
    Returns:
        Empty dictionary
    """
    return {}


