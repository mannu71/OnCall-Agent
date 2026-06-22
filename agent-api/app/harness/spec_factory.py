"""Build an :class:`AgentSpec` from workflow node config + execution context.

Centralizes the permission-mode resolution that was previously inline in
``ReactStrategy.execute`` so the rule (context → inputs → node config → default)
lives in one place and can be reused by other strategies / the scheduler / evals.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.harness.spec import AgentSpec


def resolve_policies(
    context: Dict[str, Any], agent_config: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """Resolve the declarative governance policy set for this execution.

    Precedence mirrors :func:`resolve_permission_mode`: explicit context →
    context.inputs → agent node config (``policies`` or ``params.policies``).
    Returns ``None`` when nothing is configured, so the policy engine applies
    platform defaults (= pre-policy behaviour).
    """
    ctx = context if isinstance(context, dict) else {}
    cfg = agent_config if isinstance(agent_config, dict) else {}
    for candidate in (
        ctx.get("policies"),
        (ctx.get("inputs") or {}).get("policies"),
        cfg.get("policies"),
        (cfg.get("params") or {}).get("policies"),
    ):
        if candidate:
            return candidate if isinstance(candidate, list) else None
    return None


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


def _as_str_list(value: Any) -> List[str]:
    """Coerce a config value into a clean list of capability/feature ids."""
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _as_bool(value: Any) -> bool:
    """Truthy parse that respects UI toggles stored as the STRING 'true'/'false'.

    The LangflowEditor toggle writes ``String(!!on)`` → 'true'|'false', so a plain
    ``bool('false')`` would wrongly be True. Treat the usual falsy strings as off.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def resolve_profile_fields(agent_config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the configurable-agent fields (capabilities / role / output schema /
    deep-agent flags) from agent node config + its ``params`` mirror.

    All optional: unset keys fall back to the investigation defaults, so existing
    workflows produce a spec identical to the pre-profile behaviour.
    """
    cfg = agent_config if isinstance(agent_config, dict) else {}
    params = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}

    def _pick(*keys: str) -> Any:
        for k in keys:
            if cfg.get(k) is not None:
                return cfg.get(k)
            if params.get(k) is not None:
                return params.get(k)
        return None

    return {
        "capabilities": _as_str_list(_pick("capabilities")),
        "role_prompt": _pick("rolePrompt", "role_prompt") or None,
        "output_schema": _pick("outputSchema", "output_schema") or None,
        "planning": _as_bool(_pick("planning")),
        "filesystem": _as_bool(_pick("filesystem")),
        "subagents": _coerce_subagents(_pick("subagents")),
        "auto_learn": _as_bool(_pick("autoLearn", "auto_learn")),
        "sandbox": _as_bool(_pick("sandbox")),
    }


def _coerce_subagents(value: Any) -> List[Dict[str, Any]]:
    """Accept either a list of dicts or a JSON-string (from the UI textarea)."""
    if isinstance(value, list):
        return [s for s in value if isinstance(s, dict)]
    if isinstance(value, str) and value.strip():
        import json
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [s for s in parsed if isinstance(s, dict)]
        except Exception:  # noqa: BLE001 — bad JSON = no subagents, never raise
            return []
    return []


def build_agent_spec(
    *,
    agent_config: Dict[str, Any],
    context: Dict[str, Any],
    has_cloudwatch: bool,
    has_code_analyzer: bool,
    session_id: Optional[str] = None,
) -> AgentSpec:
    """Assemble the declarative :class:`AgentSpec` for this execution."""
    profile = resolve_profile_fields(agent_config)
    return AgentSpec(
        agent_config=agent_config,
        has_cloudwatch=has_cloudwatch,
        has_code_analyzer=has_code_analyzer,
        permission_mode=resolve_permission_mode(context, agent_config),
        policies=resolve_policies(context, agent_config),
        session_id=session_id,
        capabilities=profile["capabilities"],
        role_prompt=profile["role_prompt"],
        output_schema=profile["output_schema"],
        planning=profile["planning"],
        filesystem=profile["filesystem"],
        subagents=profile["subagents"],
        auto_learn=profile["auto_learn"],
        sandbox=profile["sandbox"],
    )
