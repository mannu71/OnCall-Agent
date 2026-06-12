"""MCP role assignment repository.

Maps a gateway role to a set of default MCP servers (one role -> many servers).
Used as the agent's default toolbox when a workflow wires no tool nodes.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models.db_models import MCPRoleAssignmentModel

logger = logging.getLogger(__name__)

# Valid gateway roles for MCP assignments (extensible).
VALID_MCP_ROLES = {"agent"}


class MCPRoleRepository:
    """Repository for MCP role assignment data access."""

    def __init__(self):
        """Initialize MCP role repository."""
        logger.info("MCPRoleRepository initialized")

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in VALID_MCP_ROLES:
            raise ValueError(
                f"Invalid MCP role '{role}'. Valid roles: {sorted(VALID_MCP_ROLES)}"
            )

    async def list_for_role(self, role: str) -> List[str]:
        """List the MCP server names assigned to a role.

        Args:
            role: Gateway role (must be in VALID_MCP_ROLES)

        Returns:
            List of assigned server names.
        """
        self._validate_role(role)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MCPRoleAssignmentModel.server_name).where(
                    MCPRoleAssignmentModel.role == role
                )
            )
            return list(result.scalars().all())

    async def set_for_role(self, role: str, names: List[str]) -> None:
        """Replace all assignments for a role with the given server names.

        Args:
            role: Gateway role (must be in VALID_MCP_ROLES)
            names: New set of server names (replace-all semantics)
        """
        self._validate_role(role)
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(MCPRoleAssignmentModel).where(
                    MCPRoleAssignmentModel.role == role
                )
            )
            now = datetime.now(timezone.utc)
            # Deduplicate while preserving order; unique constraint is on (role, name).
            for name in dict.fromkeys(names):
                session.add(
                    MCPRoleAssignmentModel(
                        role=role,
                        server_name=name,
                        created_at=now,
                    )
                )
            await session.commit()

    async def clear(self, role: str) -> bool:
        """Remove all assignments for a role.

        Args:
            role: Gateway role (must be in VALID_MCP_ROLES)

        Returns:
            True if any assignment was removed, False if none existed.
        """
        self._validate_role(role)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(MCPRoleAssignmentModel)
                .where(MCPRoleAssignmentModel.role == role)
                .returning(MCPRoleAssignmentModel.id)
            )
            deleted = result.scalars().all()
            await session.commit()
            return len(deleted) > 0

    async def list_all(self) -> Dict[str, List[str]]:
        """List all MCP role assignments.

        Returns:
            Dictionary mapping role -> list of server names.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(MCPRoleAssignmentModel))
            rows = result.scalars().all()
            grouped: Dict[str, List[str]] = {}
            for row in rows:
                grouped.setdefault(row.role, []).append(row.server_name)
            return grouped
