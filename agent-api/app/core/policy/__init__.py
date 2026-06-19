"""Declarative policy engine.

One place to express agent governance — tool gating, output caps, cost ceilings,
tool-call limits, loop guardrails — as a small list of named, parameterized
policies that *compile* into the enforcement config the existing ReAct machinery
already consumes. See :mod:`app.core.policy.types` for the design rationale.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, List, Optional

from app.core.policy.registry import (
    PolicyConfigError,
    build_policy_set,
    is_registered,
    registered_names,
    resolve,
)
from app.core.policy.runtime import evaluate_cost, evaluate_tool_count
from app.core.policy.types import (
    Policy,
    PolicyAction,
    ResolvedPolicy,
    RuntimeDecision,
)

logger = logging.getLogger(__name__)

# Per-execution resolved policy. Set at agent build time; read by the runner for
# runtime quota checks (cost, tool-call count, guardrail config). contextvars are
# copied per asyncio task, so concurrent executions stay isolated.
_current_policy: "contextvars.ContextVar[Optional[ResolvedPolicy]]" = contextvars.ContextVar(
    "current_resolved_policy", default=None
)


def set_current(resolved: Optional[ResolvedPolicy]) -> None:
    _current_policy.set(resolved)


def get_current() -> Optional[ResolvedPolicy]:
    return _current_policy.get()


async def expand_policy_refs(
    config: Optional[List[dict]],
) -> Optional[List[dict]]:
    """Expand any named-policy-set references into their stored entries.

    A reference is an entry of the form
    ``{"type": "policy_set", "params": {"name": "<set-name>"}}`` — the engine
    replaces it with the entries stored under that name (migration 016). Inline
    entries pass through unchanged. Best-effort: a missing set or DB error logs a
    warning and drops the reference rather than failing the run.
    """
    if not config:
        return config
    out: List[dict] = []
    for entry in config:
        if isinstance(entry, dict) and entry.get("type") == "policy_set":
            name = (entry.get("params") or {}).get("name")
            if not name:
                logger.warning("policy: policy_set ref missing 'name'; skipping")
                continue
            try:
                from app.infrastructure.persistence.policy_set_repository import (
                    policy_set_repository,
                )
                stored = await policy_set_repository.get_policies(name)
            except Exception as exc:  # noqa: BLE001 — governance must never break a run
                logger.warning("policy: failed to load policy_set %r (%s)", name, exc)
                stored = None
            if stored:
                out.extend(stored)
            else:
                logger.warning("policy: policy_set %r not found; skipping", name)
        else:
            out.append(entry)
    return out


def resolve_with_platform_defaults(config: Optional[List[dict]]) -> ResolvedPolicy:
    """Resolve config and fill platform defaults so an empty set is a no-op.

    The defaults are exactly today's behaviour: the ask patterns and output cap
    used before the policy engine existed.
    """
    from app.config import settings
    from app.workflow.strategies.react.tool_permissions import DEFAULT_ASK_PATTERNS

    return resolve(config).with_defaults(
        default_ask_patterns=DEFAULT_ASK_PATTERNS,
        default_output_cap_chars=settings.tool_output_max_chars,
    )


def apply_to_tools(
    tools: List[Any],
    resolved: ResolvedPolicy,
    *,
    mode: str = "default",
    execution_id: Optional[str] = None,
    execution_port: Any = None,
) -> List[Any]:
    """Apply tool gating + output cap from a resolved policy.

    Single entry point replacing the two separate wrap calls in
    ``agent_builder``: permission gate first (so the model-facing schema and the
    prompt-cache prefix are unchanged), then the universal output cap.
    """
    from app.workflow.strategies.react.tool_permissions import (
        wrap_tools_with_output_cap,
        wrap_tools_with_permissions,
    )

    gated = wrap_tools_with_permissions(
        tools,
        mode=mode,
        execution_id=execution_id,
        execution_port=execution_port,
        ask_patterns=resolved.effective_ask_patterns(),
        deny_patterns=resolved.deny_patterns,
    )
    cap = resolved.output_cap_chars or 0
    return wrap_tools_with_output_cap(gated, cap)


__all__ = [
    "Policy",
    "PolicyAction",
    "PolicyConfigError",
    "ResolvedPolicy",
    "RuntimeDecision",
    "apply_to_tools",
    "build_policy_set",
    "expand_policy_refs",
    "evaluate_cost",
    "evaluate_tool_count",
    "get_current",
    "is_registered",
    "registered_names",
    "resolve",
    "resolve_with_platform_defaults",
    "set_current",
]
