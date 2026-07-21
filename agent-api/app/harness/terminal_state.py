"""Terminal-state derivation — "was this run actually a success?"

Named terminal states so a budget-exhausted or never-verified run can never
silently masquerade as ``success`` in the executions table / dashboard — the
loop-engineering discipline this maps to: "an error or an exhausted budget never
counts as success."

Computed centrally here, rather than inside the agent loop, because the full
signal set only converges after the supervisor loop completes: engine-level
signals (stop_reason, truncated, verify_pending — set by
app.harness.agent_runner.execute_agent) plus supervisor-level signals
(supervisor_escalated, supervisor_retry_exhausted) plus the conversational
short-circuit (app.core.quality.intent.is_conversational).
"""
from __future__ import annotations

import enum
from typing import Any, Dict, Optional


class StopReason(str, enum.Enum):
    """Why the agent loop stopped — engine-internal."""

    COMPLETED = "completed"
    ABORTED = "aborted"
    PROMPT_TOO_LONG = "prompt_too_long"
    MAX_TURNS = "max_turns"
    TOKEN_BUDGET = "token_budget"
    DEADLINE = "deadline"
    HITL_PAUSED = "hitl_paused"


class TerminalState(str, enum.Enum):
    """User/dashboard-facing outcome, derived from StopReason + supervisor
    verdict + verify status. Distinct from StopReason: StopReason is "why the
    loop stopped" (engine-internal); TerminalState is "was this actually a
    success" (what a caller/dashboard should trust). An exhausted or unverified
    run never self-reports ``success`` — see the loop-engineering discipline
    this maps to: "an error or an exhausted budget never counts as success"."""

    SUCCESS = "success"
    NO_OP = "no_op"
    BLOCKED = "blocked"
    STALLED = "stalled"
    EXHAUSTED = "exhausted"
    UNVERIFIED = "unverified"


def derive_terminal_state(
    *,
    stop_reason: Optional[str] = None,
    did_forced_synthesis: bool = False,
    truncated: bool = False,
    verify_pending: bool = False,
    plan_incomplete: bool = False,
    supervisor_escalated: bool = False,
    supervisor_retry_exhausted: bool = False,
    is_conversational: bool = False,
) -> str:
    """Map engine/supervisor signals to a TerminalState value (first match wins).

    Precedence: conversational no_op > budget-exhausted > paused/aborted >
    supervisor gave up (stalled) > unconfirmed edit / unfinished plan
    (unverified) > success.
    """
    if is_conversational:
        return TerminalState.NO_OP.value
    if (
        did_forced_synthesis
        or truncated
        or stop_reason in (
            StopReason.MAX_TURNS.value,
            StopReason.TOKEN_BUDGET.value,
            StopReason.DEADLINE.value,
        )
    ):
        return TerminalState.EXHAUSTED.value
    if stop_reason in (StopReason.HITL_PAUSED.value, StopReason.ABORTED.value, StopReason.PROMPT_TOO_LONG.value):
        return TerminalState.BLOCKED.value
    if supervisor_escalated or supervisor_retry_exhausted:
        return TerminalState.STALLED.value
    if verify_pending or plan_incomplete:
        return TerminalState.UNVERIFIED.value
    return TerminalState.SUCCESS.value


def terminal_state_for_result(
    result: Dict[str, Any], *, is_conversational: bool = False,
    plan_incomplete: bool = False,
) -> str:
    """Convenience wrapper: pull the known signal keys out of a harness
    result dict (absent keys default safely).

    ``plan_incomplete`` comes from app.harness.completion_check over the
    run's todo list, computed by the caller (the finalizer already fetches it).
    """
    return derive_terminal_state(
        stop_reason=result.get("stop_reason"),
        did_forced_synthesis=bool(result.get("did_forced_synthesis")),
        truncated=bool(result.get("truncated")),
        verify_pending=bool(result.get("verify_pending")),
        plan_incomplete=plan_incomplete,
        supervisor_escalated=bool(result.get("supervisor_escalated")),
        supervisor_retry_exhausted=bool(result.get("supervisor_retry_exhausted")),
        is_conversational=is_conversational,
    )


__all__ = ["derive_terminal_state", "terminal_state_for_result"]
