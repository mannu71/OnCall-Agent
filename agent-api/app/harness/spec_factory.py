"""Build an :class:`AgentSpec` from workflow node config + execution context.

Centralizes the permission-mode resolution that was previously inline in
``ReactStrategy.execute`` so the rule (context → inputs → node config → default)
lives in one place and can be reused by other strategies / the scheduler / evals.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.harness.spec import AgentSpec


def resolve_permission_mode(context: Dict[str, Any], agent_config: Dict[str, Any]) -> str:
    """Resolve the effective permission mode (default | auto_allow | plan).

    Precedence: explicit context → context.inputs → agent node config → default.
    """
    ctx = context if isinstance(context, dict) else {}
    return str(
        ctx.get("permission_mode")
        or (ctx.get("inputs") or {}).get("permission_mode")
        or agent_config.get("permissionMode")
        or (agent_config.get("params") or {}).get("permissionMode")
        or "default"
    ).lower()


def build_agent_spec(
    *,
    agent_config: Dict[str, Any],
    context: Dict[str, Any],
    has_cloudwatch: bool,
    has_code_analyzer: bool,
    session_id: Optional[str] = None,
) -> AgentSpec:
    """Assemble the declarative :class:`AgentSpec` for this execution."""
    return AgentSpec(
        agent_config=agent_config,
        has_cloudwatch=has_cloudwatch,
        has_code_analyzer=has_code_analyzer,
        permission_mode=resolve_permission_mode(context, agent_config),
        session_id=session_id,
    )
