"""Runtime quota checks derived from a :class:`ResolvedPolicy`.

These are pure functions over the running totals so they are trivial to unit
test and free of side effects — the caller decides how to react (ask via the
existing HITL channel, halt, or warn).
"""
from __future__ import annotations

from app.core.policy.types import PolicyAction, ResolvedPolicy, RuntimeDecision


def evaluate_cost(resolved: ResolvedPolicy, cost_usd: float) -> RuntimeDecision:
    """Classify accumulated spend against the policy's cost ceiling/thresholds.

    Precedence: a hard ``budget_cost_usd`` breach → DENY; otherwise the highest
    crossed ``ask_cost_thresholds_usd`` → ASK; otherwise ALLOW.
    """
    if resolved.budget_cost_usd is not None and cost_usd >= resolved.budget_cost_usd:
        return RuntimeDecision(
            action=PolicyAction.DENY,
            reason=(
                f"Cost budget exceeded: ${cost_usd:.4f} ≥ "
                f"${resolved.budget_cost_usd:.2f} ceiling."
            ),
            deciding_policy="cost_budget",
        )
    crossed = [t for t in resolved.ask_cost_thresholds_usd if cost_usd >= t]
    if crossed:
        return RuntimeDecision(
            action=PolicyAction.ASK,
            reason=(
                f"Cost ${cost_usd:.4f} crossed the ${max(crossed):.2f} approval "
                "threshold; confirm before continuing."
            ),
            deciding_policy="cost_budget",
        )
    return RuntimeDecision()


def evaluate_tool_count(resolved: ResolvedPolicy, calls_so_far: int) -> RuntimeDecision:
    """DENY once the per-session tool-call limit is reached."""
    if resolved.max_tool_calls is not None and calls_so_far >= resolved.max_tool_calls:
        return RuntimeDecision(
            action=PolicyAction.DENY,
            reason=(
                f"Tool-call limit reached: {calls_so_far} ≥ "
                f"{resolved.max_tool_calls} per session."
            ),
            deciding_policy="max_tool_calls_per_session",
        )
    return RuntimeDecision()
