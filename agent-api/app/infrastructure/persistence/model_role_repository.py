"""Model role assignment repository.

Maps a gateway role (agent | crawler | subagent) to a named LLM config. Used by
the LLM resolution pipeline as the fallback model for a consumer when a workflow
wires / names no Language Model node.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models.db_models import ModelRoleAssignmentModel

logger = logging.getLogger(__name__)

# Valid gateway roles for model assignments.
VALID_ROLES = {"agent", "crawler", "subagent"}


class ModelRoleRepository:
    """Repository for model role assignment data access."""

    def __init__(self):
        """Initialize model role repository."""
        logger.info("ModelRoleRepository initialized")

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in VALID_ROLES:
            raise ValueError(
                f"Invalid model role '{role}'. Valid roles: {sorted(VALID_ROLES)}"
            )

    async def list_all(self) -> Dict[str, str]:
        """List all role assignments.

        Returns:
            Dictionary mapping role -> llm_config_name for every assigned role.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ModelRoleAssignmentModel))
            rows = result.scalars().all()
            return {row.role: row.llm_config_name for row in rows}

    async def get(self, role: str) -> Optional[str]:
        """Get the LLM config name assigned to a role.

        Args:
            role: Gateway role (must be in VALID_ROLES)

        Returns:
            The assigned llm_config_name, or None if unassigned.
        """
        self._validate_role(role)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelRoleAssignmentModel.llm_config_name).where(
                    ModelRoleAssignmentModel.role == role
                )
            )
            return result.scalar_one_or_none()

    async def upsert(self, role: str, config_name: str) -> None:
        """Insert or update the assignment for a role.

        Args:
            role: Gateway role (must be in VALID_ROLES)
            config_name: Name of an existing LLM config
        """
        self._validate_role(role)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelRoleAssignmentModel).where(
                    ModelRoleAssignmentModel.role == role
                )
            )
            row = result.scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if row:
                row.llm_config_name = config_name
                row.updated_at = now
            else:
                session.add(
                    ModelRoleAssignmentModel(
                        role=role,
                        llm_config_name=config_name,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()

    async def delete(self, role: str) -> bool:
        """Clear the assignment for a role.

        Args:
            role: Gateway role (must be in VALID_ROLES)

        Returns:
            True if an assignment was removed, False if none existed.
        """
        self._validate_role(role)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ModelRoleAssignmentModel)
                .where(ModelRoleAssignmentModel.role == role)
                .returning(ModelRoleAssignmentModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None
