"""Agent-profile repository.

CRUD for reusable agent profiles (see ``app/models/db_models.py`` and migration
``025_agent_profiles.sql``) plus the merge that folds a profile into an agent
node's config. A profile lets a user configure any *type* of agent (support,
data-analysis, code-explorer, …); an agent node references one by name and the
executor merges the profile fields UNDER any explicit node overrides.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import AgentProfileModel

logger = logging.getLogger(__name__)


class AgentProfileRepository(BaseAsyncRepository):
    """Repository for named, reusable agent profiles."""

    async def get(self, name: str) -> Optional[Dict[str, Any]]:
        row = await self._one(
            select(AgentProfileModel).where(AgentProfileModel.name == name)
        )
        return _to_dict(row) if row is not None else None

    async def list(self) -> List[Dict[str, Any]]:
        rows = await self._all(select(AgentProfileModel))
        return [_to_dict(r) for r in rows]

    async def upsert(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        role_prompt: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        default_tools: Optional[List[str]] = None,
        output_schema: Optional[str] = None,
        default_policies: Optional[List[Dict[str, Any]]] = None,
        deep_features: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert or update a profile. Builtins cannot be overwritten."""
        async def _work(session):
            row = (await session.execute(
                select(AgentProfileModel).where(AgentProfileModel.name == name)
            )).scalar_one_or_none()
            if row is not None and row.builtin:
                raise ValueError(f"agent profile '{name}' is builtin and cannot be modified")
            if row is None:
                row = AgentProfileModel(name=name)
                session.add(row)
            if description is not None:
                row.description = description
            if role_prompt is not None:
                row.role_prompt = role_prompt or None
            if capabilities is not None:
                row.capabilities = capabilities
            if default_tools is not None:
                row.default_tools = default_tools
            if output_schema is not None:
                row.output_schema = output_schema or None
            if default_policies is not None:
                row.default_policies = default_policies
            if deep_features is not None:
                row.deep_features = deep_features
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return _to_dict(row)
        return await self._run(_work)

    async def delete(self, name: str) -> bool:
        async def _work(session):
            row = (await session.execute(
                select(AgentProfileModel).where(AgentProfileModel.name == name)
            )).scalar_one_or_none()
            if row is None:
                return False
            if row.builtin:
                raise ValueError(f"agent profile '{name}' is builtin and cannot be deleted")
            await session.delete(row)
            return True
        return await self._run(_work)

    async def merge_into_agent_config(
        self, agent_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Fold the referenced profile into ``agent_config`` (node overrides win).

        Reads ``agent_config['profile']`` (or ``params.profile``); when absent or
        unknown, returns the config unchanged. Each profile field is only applied
        when the node has not already set the corresponding key, so explicit node
        configuration always takes precedence. Best-effort: any error returns the
        original config so a profile lookup never breaks a run.
        """
        cfg = dict(agent_config or {})
        params = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
        profile_name = cfg.get("profile") or params.get("profile")
        if not profile_name:
            return cfg
        try:
            profile = await self.get(str(profile_name))
        except Exception as exc:  # noqa: BLE001 — never break a run on profile lookup
            logger.warning("agent profile lookup failed for %s: %s", profile_name, exc)
            return cfg
        if profile is None:
            logger.info("agent profile '%s' not found; using node config as-is", profile_name)
            return cfg

        def _missing(*keys: str) -> bool:
            return all(cfg.get(k) in (None, "", [], {}) and params.get(k) in (None, "", [], {})
                       for k in keys)

        if profile.get("role_prompt") and _missing("rolePrompt", "role_prompt"):
            cfg["rolePrompt"] = profile["role_prompt"]
        if profile.get("capabilities") and _missing("capabilities"):
            cfg["capabilities"] = profile["capabilities"]
        if profile.get("output_schema") and _missing("outputSchema", "output_schema"):
            cfg["outputSchema"] = profile["output_schema"]
        if profile.get("default_policies") and _missing("policies"):
            cfg["policies"] = profile["default_policies"]

        deep = profile.get("deep_features") or {}
        if isinstance(deep, dict):
            if "planning" in deep and _missing("planning"):
                cfg["planning"] = bool(deep["planning"])
            if "filesystem" in deep and _missing("filesystem"):
                cfg["filesystem"] = bool(deep["filesystem"])
            if deep.get("subagents") and _missing("subagents"):
                cfg["subagents"] = deep["subagents"]

        logger.info("agent profile '%s' merged into agent config", profile_name)
        return cfg


def _to_dict(row: AgentProfileModel) -> Dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "role_prompt": row.role_prompt,
        "capabilities": row.capabilities or [],
        "default_tools": row.default_tools or [],
        "output_schema": row.output_schema,
        "default_policies": row.default_policies or [],
        "deep_features": row.deep_features or {},
        "builtin": bool(row.builtin),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# Module-level singleton (mirrors other repositories in this package).
agent_profile_repository = AgentProfileRepository()
