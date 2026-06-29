"""Bounded supervisor retry loop — the harness's core orchestration primitive.

Extracted verbatim (behaviour-preserving) from ``ReactStrategy.execute`` so the
"run agent → score → retry/HITL/escalate, under hard bounds" loop is reusable by
any strategy and unit-testable in isolation.

Bounds (defensive, independent of the supervisor's own ``max_retries``):
  * an absolute iteration ceiling (``max_retries + 1``),
  * a wall-clock deadline, and
  * the supervisor's cumulative token budget.

The caller supplies two closures so this module stays agnostic of how an agent is
built and run:
  * ``run_agent(agent, query) -> result_dict``
  * ``rebuild_agent() -> agent`` (used on RETRY to inject corrective guidance)
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from app.core.supervisor import SupervisorAction

# NOTE: ``emit_hitl_pause`` and ``estimate_confidence`` are imported lazily inside
# ``run_supervised`` — importing them at module load creates a cycle
# (harness.supervisor_loop → strategies.react.hitl → strategies/__init__ →
#  strategies.react.strategy → harness.supervisor_loop).


async def run_supervised(
    *,
    agent: Any,
    run_agent: Callable[[Any, str], Awaitable[Dict[str, Any]]],
    rebuild_agent: Callable[[], Any],
    supervisor: Optional[Any],
    base_query: str,
    execution_id: Optional[str],
    logger_instance: Any,
    wall_clock_budget: float,
    execution_port: Any = None,
) -> Tuple[Dict[str, Any], int, int]:
    """Run the agent under supervisor review with hard termination bounds.

    Returns ``(result, accum_input_tokens, accum_output_tokens, accum_cache_read_tokens)``.
    """
    # Lazy imports to avoid the import cycle documented at module top.
    from app.workflow.strategies.react.helpers import estimate_confidence
    from app.workflow.strategies.react.hitl import emit_hitl_pause

    supervisor_retry_count = 0
    current_query = base_query
    result: Dict[str, Any] = {}
    accum_input_tokens = 0
    accum_output_tokens = 0
    accum_cache_read_tokens = 0
    _cfg = getattr(supervisor, "_cfg", None) if supervisor else None
    token_budget = getattr(_cfg, "token_budget", 0) or 0

    # Defensive hard bounds (see module docstring).
    max_iterations = (getattr(_cfg, "max_retries", 0) + 1) if supervisor else 1
    loop_deadline = time.monotonic() + float(wall_clock_budget)
    iteration = 0

    while True:
        iteration += 1
        if iteration > max_iterations:
            logger_instance.warning(
                "supervisor_loop: iteration cap reached (%d > %d) — stopping",
                iteration, max_iterations,
                extra={"execution_id": execution_id},
            )
            result["supervisor_retry_exhausted"] = True
            break
        if time.monotonic() > loop_deadline:
            logger_instance.warning(
                "supervisor_loop: wall-clock budget exhausted (%.0fs) — stopping",
                wall_clock_budget,
                extra={"execution_id": execution_id},
            )
            result["supervisor_retry_exhausted"] = True
            break

        if token_budget and (accum_input_tokens + accum_output_tokens) >= token_budget:
            logger_instance.warning(
                "supervisor_loop: pre-call token budget already exhausted (%d >= %d) — stopping",
                accum_input_tokens + accum_output_tokens,
                token_budget,
                extra={"execution_id": execution_id},
            )
            result["supervisor_token_budget_exhausted"] = True
            break

        result = await run_agent(agent, current_query)
        accum_input_tokens += result.get("input_tokens", 0) or 0
        accum_output_tokens += result.get("output_tokens", 0) or 0
        accum_cache_read_tokens += result.get("cache_read_tokens", 0) or 0

        final_answer = result.get("final_answer") or ""
        confidence = estimate_confidence(final_answer, result.get("tool_calls", []))

        if supervisor is None:
            break

        total_tokens = accum_input_tokens + accum_output_tokens
        if token_budget and total_tokens >= token_budget:
            logger_instance.warning(
                "supervisor_loop: token budget exhausted (%d >= %d) — stopping",
                total_tokens, token_budget,
                extra={"execution_id": execution_id},
            )
            result["supervisor_token_budget_exhausted"] = True
            break

        verdict = supervisor.evaluate(
            final_answer=final_answer,
            tool_calls=result.get("tool_calls", []),
            confidence=confidence,
            retry_count=supervisor_retry_count,
        )

        if verdict.action == SupervisorAction.PASS:
            break

        if verdict.action == SupervisorAction.RETRY:
            supervisor_retry_count += 1
            current_query = (
                f"{verdict.retry_guidance}\n\n---\n\nOriginal query:\n{base_query}"
            )
            agent = rebuild_agent()
            continue

        if verdict.action == SupervisorAction.HITL:
            await emit_hitl_pause(
                execution_id,
                {
                    "request_id": str(uuid.uuid4()),
                    "draft_answer": final_answer,
                    "message": (
                        f"Supervisor quality score {verdict.score:.2f} — "
                        f"engineer review requested. {verdict.reason}"
                    ),
                },
                execution_port=execution_port,
            )
            break

        if verdict.action == SupervisorAction.ESCALATE:
            result["supervisor_escalated"] = True
            result["supervisor_reason"] = verdict.reason
            break
        break

    return result, accum_input_tokens, accum_output_tokens, accum_cache_read_tokens
