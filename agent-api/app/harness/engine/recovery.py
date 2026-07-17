"""Native turn-loop engine — resilience-ladder helpers.

Backoff-wrapped model calls (reusing ``app.core.resilience.retry.with_retry`` — the
same decorrelated-jitter backoff both the LangGraph path and the Bedrock
fallback-chain failover already rely on) plus the shared constants for the
truncation-escalation / resume-mid-thought continuation rungs.
"""
from __future__ import annotations

from typing import Any, Callable, Coroutine, Optional, TypeVar

from app.core.resilience.retry import with_retry

T = TypeVar("T")

#: Same text agent_runner uses for its truncation-continuation turn — kept
#: identical so eval trajectories don't diverge on wording alone.
CONTINUE_TRUNCATED_NUDGE = (
    "Continue and COMPLETE your previous answer. Do not repeat what you "
    "already wrote; finish it, citing concrete file:line evidence."
)

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

#: Max output-token escalation ceiling — mirrors the reference loop's
#: "escalate once" step (8k -> 64k class jump), generalized as a multiplier
#: capped at a hard ceiling regardless of the configured base.
MAX_OUTPUT_TOKENS_CEILING = 64_000

#: How many "resume mid-thought" continuation turns are allowed after the
#: one-time output-token escalation, before giving up honestly.
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3


async def call_model_with_backoff(
    call_fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    retry_predicate: Optional[Callable[[Any], bool]] = None,
    max_retries: int = 3,
    **kwargs: Any,
) -> T:
    """Exponential-backoff wrapper around one model call.

    Thin pass-through to ``app.core.resilience.retry.with_retry`` — the same
    decorrelated-jitter backoff (429/529-class errors) both the LangGraph
    path and the fallback-chain failover already use. ``retry_predicate``
    preserves the executor's contract: when a Bedrock fallback chain exists,
    throttles (``should_fallback``) bubble out immediately so the OUTER
    failover loop can switch target instead of sleeping through retries here.
    """
    return await with_retry(
        call_fn, *args, retry_on=retry_predicate, max_retries=max_retries, **kwargs,
    )


__all__ = [
    "call_model_with_backoff",
    "CONTINUE_TRUNCATED_NUDGE",
    "BUDGET_SYNTHESIS_NUDGE",
    "MAX_OUTPUT_TOKENS_CEILING",
    "MAX_OUTPUT_TOKENS_RECOVERY_LIMIT",
]
