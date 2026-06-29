"""Named governance policy-set repository.

CRUD for reusable policy sets (see ``app/core/policy`` and migration
``016_policy_sets.sql``). A policy set is a name + a JSON list of policy entries;
workflows reference one by name and the policy engine expands it at agent-build
time via :func:`app.core.policy.expand_policy_refs`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import PolicySetModel

logger = logging.getLogger(__name__)


class PolicySetRepository(BaseAsyncRepository):
    """Repository for named, reusable policy sets."""

    async def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Return one policy set as a dict, or ``None`` if absent."""
        row = await self._one(select(PolicySetModel).where(PolicySetModel.name == name))
        return _to_dict(row) if row is not None else None

    async def get_policies(self, name: str) -> Optional[List[Dict[str, Any]]]:
        """Return just the policy-entry list for *name*, or ``None`` if absent."""
        row = await self.get(name)
        return row["policies"] if row is not None else None

    async def list(self) -> List[Dict[str, Any]]:
        rows = await self._all(select(PolicySetModel))
        return [_to_dict(r) for r in rows]

    async def upsert(
        self,
        name: str,
        policies: List[Dict[str, Any]],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert or update a policy set. Validates entries before persisting."""
        # Reject unknown policy types / bad params up front so a stored set is
        # always resolvable (raises PolicyConfigError on invalid config).
        from app.core.policy import build_policy_set
        build_policy_set(policies)

        async def _work(session):
            row = (await session.execute(
                select(PolicySetModel).where(PolicySetModel.name == name)
            )).scalar_one_or_none()
            if row is None:
                row = PolicySetModel(name=name, policies=policies, description=description)
                session.add(row)
            else:
                row.policies = policies
                if description is not None:
                    row.description = description
                row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return _to_dict(row)
        return await self._run(_work)

    async def delete(self, name: str) -> bool:
        async def _work(session):
            row = (await session.execute(
                select(PolicySetModel).where(PolicySetModel.name == name)
            )).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            return True
        return await self._run(_work)


def _to_dict(row: PolicySetModel) -> Dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "policies": row.policies or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# Module-level singleton (mirrors other repositories in this package).
policy_set_repository = PolicySetRepository()
