"""MCP server configuration repository."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import MCPServerModel

logger = logging.getLogger(__name__)


class MCPConfigRepository(BaseAsyncRepository):
    """Repository for MCP server configuration data access."""

    def __init__(self):
        """Initialize MCP config repository."""
        logger.info("MCPConfigRepository initialized")

    async def list_all(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        """List all MCP servers (optionally including disabled ones)."""
        query = select(MCPServerModel)
        if not include_disabled:
            query = query.where(MCPServerModel.enabled == True)
        servers = await self._all(query)
        return [self._mcp_server_to_dict(server) for server in servers]

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get MCP server by name, or None if not found."""
        server = await self._one(select(MCPServerModel).where(MCPServerModel.name == name))
        return self._mcp_server_to_dict(server) if server else None

    async def create(self, server_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new MCP server and return it."""
        async def _work(session):
            server = MCPServerModel(
                name=server_data["name"],
                command=server_data["command"],
                args=server_data.get("args", []),
                env=server_data.get("env", {}),
                enabled=server_data.get("enabled", True),
                description=server_data.get("description"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(server)
            await session.flush()
            await session.refresh(server)
            return self._mcp_server_to_dict(server)
        return await self._run(_work)

    async def update(self, name: str, server_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an MCP server by name; return it, or None if not found."""
        async def _work(session):
            server = (await session.execute(
                select(MCPServerModel).where(MCPServerModel.name == name)
            )).scalar_one_or_none()
            if not server:
                return None

            if "command" in server_data:
                server.command = server_data["command"]
            if "args" in server_data:
                server.args = server_data["args"]
            if "env" in server_data:
                server.env = server_data["env"]
            if "enabled" in server_data:
                server.enabled = server_data["enabled"]
            if "description" in server_data:
                server.description = server_data["description"]
            if "name" in server_data and server_data["name"] != name:
                server.name = server_data["name"]
            server.updated_at = datetime.now(timezone.utc)

            await session.flush()
            await session.refresh(server)
            return self._mcp_server_to_dict(server)
        return await self._run(_work)

    async def delete(self, name: str) -> bool:
        """Delete an MCP server by name; return True if a row was removed."""
        async def _work(session):
            result = await session.execute(
                delete(MCPServerModel).where(MCPServerModel.name == name).returning(MCPServerModel.id)
            )
            return result.scalar_one_or_none() is not None
        return await self._run(_work)

    async def exists(self, name: str) -> bool:
        """Return True if an MCP server with *name* exists."""
        row = await self._one(select(MCPServerModel.id).where(MCPServerModel.name == name))
        return row is not None
    
    def _mcp_server_to_dict(self, server: MCPServerModel) -> Dict[str, Any]:
        """Convert MCP server model to dictionary."""
        return {
            "id": server.id,
            "name": server.name,
            "command": server.command,
            "args": server.args or [],
            "env": server.env or {},
            "enabled": server.enabled,
            "description": server.description,
            "created_at": server.created_at.isoformat() if server.created_at else None,
            "updated_at": server.updated_at.isoformat() if server.updated_at else None
        }