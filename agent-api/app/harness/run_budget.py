"""Engine-level run budgets — wall-clock deadline and token ceiling.

A long investigation must end *honestly*: with a synthesized partial answer that
says what is confirmed and what is not, rather than a mid-thought kill or a run
that quietly bills forever. This module owns the shared state for that.

Two rungs, checked at the top of every model call (see the ``pre_model_hook`` in
:mod:`app.harness.react_agent`):

* ~90% consumed → one graceful "stop calling tools and synthesize now" nudge,
  delivered a single time per run (:data:`BUDGET_SYNTHESIS_NUDGE`).
* 100% consumed → :class:`RunBudgetExhausted`, which
  :func:`app.harness.agent_runner.execute_agent` converts into a recovered
  partial answer with ``stop_reason`` set.

The budget lives in a :class:`~contextvars.ContextVar` so the hook can reach it
without threading a parameter through ``create_react_agent``. LangGraph node
tasks inherit a copy of the parent context, and the :class:`RunBudget` instance
is shared by reference, so the once-per-run ``nudged`` flag is honoured across
supersteps; a subagent sets its own budget inside its own task context, which
cannot leak back out to the parent.

The outer ``asyncio.wait_for`` in :mod:`app.harness.engine` remains as a
backstop for the case this cannot cover — a single model or tool call that hangs
past the deadline, where no hook fires to observe it.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.config import settings

#: Delivered once when a run crosses ~90% of its wall-clock deadline or token
#: budget, before the hard stop — the budget-exhaustion analogue of the
#: max-turns FORCED_SYNTHESIS_NUDGE. Tells the model to stop investigating and
#: synthesize a partial answer from what it already has.
BUDGET_SYNTHESIS_NUDGE = (
    "You are almost out of time/budget for this investigation. STOP calling "
    "tools now and write your final answer from the evidence you already have. "
    "Be explicit about what is confirmed vs. still uncertain, and cite concrete "
    "evidence (IDs, file:line) for every claim."
)

#: Fraction of a budget at which the one-time synthesis nudge fires.
NUDGE_FRACTION = 0.9

STOP_DEADLINE = "deadline"
STOP_TOKEN_BUDGET = "token_budget"


class RunBudgetExhausted(Exception):
    """Raised from the pre-model hook when a run budget is fully consumed.

    Carries which budget ran out so the caller can label the partial answer.
    """

    #: Honoured by app.core.resilience.retry.with_retry — this is a control-flow
    #: signal, and retrying it would do the exact thing it was raised to stop.
    never_retry = True

    def __init__(self, reason: str) -> None:
        super().__init__(f"run budget exhausted: {reason}")
        self.reason = reason


@dataclass
class RunBudget:
    """One run's budget state. Mutated in place; shared by reference."""

    deadline_monotonic: Optional[float] = None   # None → no wall-clock budget
    deadline_seconds: float = 0.0                # the configured span, for %
    token_budget: int = 0                        # 0 → disabled
    token_cb: Any = None                         # the run's TokenUsageCallback
    nudged: bool = False                         # synthesis nudge fired once
    # Token-estimate calibration (opt-in). The pre-model hook parks the estimate
    # it sent, plus the prompt-token counter reading at that moment, so its NEXT
    # entry can attribute the provider's actual prompt tokens to that estimate.
    last_prompt_estimate: int = 0
    last_prompt_tokens_seen: int = 0

    def prompt_tokens_seen(self) -> int:
        """Full prompt size booked so far = fresh input + cache reads + cache
        writes. Unlike :meth:`tokens_used` this INCLUDES the cache counters,
        because a cached prompt still occupies the context window the chars/4
        estimate is trying to predict."""
        cb = self.token_cb
        if cb is None:
            return 0
        try:
            # input_tokens is INCLUSIVE of cache_read + cache_creation, so it is
            # the full prompt cost on its own. Summing all three (as this used
            # to) roughly doubled the figure on a cache-warm run and could trip
            # the TOKEN_BUDGET stop at ~half the real budget — cutting an
            # investigation short and returning a partial answer for no reason.
            # Verified against live Bedrock 2026-07-21; see
            # app.core.observability.cache_metrics.
            return int(getattr(cb, "input_tokens", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def tokens_used(self) -> int:
        """Total tokens booked so far, or 0 when no counter is attached.

        ``input + output``, matching the former native ``TokenLedger`` — the
        cache read/creation counters are deliberately excluded so the same
        configured ceiling means the same thing on both paths.
        """
        cb = self.token_cb
        if cb is None:
            return 0
        try:
            return int(getattr(cb, "input_tokens", 0) or 0) + int(
                getattr(cb, "output_tokens", 0) or 0
            )
        except (TypeError, ValueError):
            return 0


_current: ContextVar[Optional[RunBudget]] = ContextVar("run_budget", default=None)


def set_run_budget(budget: Optional[RunBudget]) -> Any:
    """Bind a budget to the current context. Returns the token for :func:`reset`."""
    return _current.set(budget)


def get_run_budget() -> Optional[RunBudget]:
    return _current.get()


def reset_run_budget(token: Any) -> None:
    try:
        _current.reset(token)
    except (ValueError, LookupError):  # different context (e.g. task boundary)
        _current.set(None)


def budget_status(budget: Optional[RunBudget]) -> Tuple[float, Optional[str]]:
    """Fraction of the most-consumed budget and which one it is.

    Returns ``(fraction, reason)`` for whichever of the wall-clock deadline /
    token ceiling is closest to exhaustion, or ``(0.0, None)`` when no budget is
    configured.
    """
    if budget is None:
        return 0.0, None
    frac = 0.0
    reason: Optional[str] = None
    if budget.deadline_monotonic is not None and budget.deadline_seconds > 0:
        consumed = 1.0 - (budget.deadline_monotonic - time.monotonic()) / budget.deadline_seconds
        if consumed > frac:
            frac, reason = consumed, STOP_DEADLINE
    if budget.token_budget > 0:
        consumed = budget.tokens_used() / budget.token_budget
        if consumed > frac:
            frac, reason = consumed, STOP_TOKEN_BUDGET
    return frac, reason


def _resolve_budget_knob(
    agent_config: Optional[Dict[str, Any]], key: str, default: Any, cast: Any,
) -> Any:
    """Per-workflow numeric override for a run-budget knob: ``agent_config[key]``
    or its ``params`` mirror wins over the global default. Absent/invalid → the
    global default (so existing workflows are unchanged)."""
    cfg = agent_config or {}
    params = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
    raw = cfg.get(key)
    if raw is None:
        raw = params.get(key)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def resolve_run_deadline(agent_config: Optional[Dict[str, Any]]) -> float:
    """Wall-clock budget in seconds for one agent run (0 → unbounded)."""
    return _resolve_budget_knob(
        agent_config, "run_deadline_seconds",
        getattr(settings, "agent_run_deadline_seconds", 0.0), float,
    )


def resolve_token_budget(agent_config: Optional[Dict[str, Any]]) -> int:
    """Cumulative token ceiling for one agent run (0 → unbounded)."""
    return _resolve_budget_knob(
        agent_config, "run_token_budget",
        getattr(settings, "agent_run_token_budget", 0), int,
    )


def build_run_budget(
    agent_config: Optional[Dict[str, Any]], *, token_cb: Any = None,
) -> Optional[RunBudget]:
    """Build the budget for a run, or ``None`` when neither knob is configured
    (so an unbudgeted run pays nothing for this machinery)."""
    deadline_seconds = resolve_run_deadline(agent_config)
    token_budget = resolve_token_budget(agent_config)
    if deadline_seconds <= 0 and token_budget <= 0:
        return None
    return RunBudget(
        deadline_monotonic=(
            time.monotonic() + deadline_seconds if deadline_seconds > 0 else None
        ),
        deadline_seconds=max(0.0, deadline_seconds),
        token_budget=max(0, token_budget),
        token_cb=token_cb,
    )


__all__ = [
    "BUDGET_SYNTHESIS_NUDGE",
    "NUDGE_FRACTION",
    "STOP_DEADLINE",
    "STOP_TOKEN_BUDGET",
    "RunBudget",
    "RunBudgetExhausted",
    "budget_status",
    "build_run_budget",
    "get_run_budget",
    "reset_run_budget",
    "resolve_run_deadline",
    "resolve_token_budget",
    "set_run_budget",
]
