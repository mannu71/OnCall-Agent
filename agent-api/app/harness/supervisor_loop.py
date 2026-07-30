"""Bounded supervisor retry loop — the harness's core orchestration primitive.

This is the **quality** supervisor: it runs *after* an agent turn completes,
scores the final answer, and decides retry / HITL / escalate. It is distinct
from the **Action Supervisor** (:mod:`app.core.supervision.action_supervisor`),
which reviews individual write-class actions *before* they execute via the
ask-gate. The two are complementary — one gates the answer, the other gates the
actions — and both feed the same approval/trajectory audit trail.

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

import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from app.core.context.tool_output import get_stats as get_compression_stats
from app.core.context.tool_output import start_stats as start_compression_stats
from app.core.quality.supervisor import SupervisorAction
from app.harness.helpers import estimate_confidence
from app.harness.hitl import emit_hitl_pause
from app.harness.usage_ledger import drain_usage, open_ledger


async def _record_supervisor_reward(execution_id: Optional[str], score: Any) -> None:
    """Best-effort late-bound reward: attach the supervisor's quality score
    to the run's trajectory_events (see ``append_reward_for_trace``).

    Gated on ``settings.step_events_enabled`` — a no-op trace (nothing
    recorded) is expected whenever step events are off, so this never
    raises and never logs above debug in that case.
    """
    if not execution_id:
        return
    try:
        from app.config import settings
        if not bool(getattr(settings, "step_events_enabled", False)):
            return
        from app.infrastructure.persistence import trajectory_event_repository
        await trajectory_event_repository.append_reward_for_trace(
            execution_id, "supervisor_score", score,
        )
    except Exception as exc:  # noqa: BLE001 — reward recording must never break a run
        logging.getLogger(__name__).debug(
            "supervisor_loop: reward recording skipped for execution_id=%s (%s)",
            execution_id, exc,
        )


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
) -> Tuple[Dict[str, Any], int, int, int, int]:
    """Run the agent under supervisor review with hard termination bounds.

    Returns ``(result, accum_input_tokens, accum_output_tokens,
    accum_cache_read_tokens, accum_cache_creation_tokens)``. ``input_tokens`` is
    the WHOLE prompt — cache_read/cache_creation are a breakdown OF it, not
    counters to add to it (see ``TokenUsageCallback`` in
    ``core/streaming/callbacks.py`` and ``core/observability/cache_metrics.py``).

    The totals cover the WHOLE agent tree: each turn's own counters plus the
    usage banked by any delegated subagents (``app.harness.usage_ledger``).
    Children run under their own execution_id and callback, so they used to be
    absent from both the returned totals and the budget below.
    """
    supervisor_retry_count = 0
    current_query = base_query
    result: Dict[str, Any] = {}
    accum_input_tokens = 0
    accum_output_tokens = 0
    accum_cache_read_tokens = 0
    accum_cache_creation_tokens = 0
    # Install the subagent usage ledger for this run. Opened here because this
    # frame is an ancestor context of every delegated child (children mutate the
    # ledger in place, so their writes are visible across the task boundary).
    open_ledger()
    # Same frame, same reason: compression happens inside tool calls made by the
    # agent and by every child, and the accumulator is mutated in place.
    start_compression_stats()
    _breakdown: list = []
    _aux: list = []
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
        accum_cache_creation_tokens += result.get("cache_creation_tokens", 0) or 0

        # Fold in everything this turn spent outside the parent's own callback:
        # delegated children AND auxiliary LLM calls (compaction, grader,
        # extractors). Drained — not just read — so each iteration banks only
        # its own.
        extra_usage = drain_usage()
        if not extra_usage.empty():
            accum_input_tokens += extra_usage.input_tokens
            accum_output_tokens += extra_usage.output_tokens
            accum_cache_read_tokens += extra_usage.cache_read_tokens
            accum_cache_creation_tokens += extra_usage.cache_creation_tokens
            logger_instance.info(
                "supervisor_loop: folded in %d subagent run(s) + %d auxiliary LLM "
                "call(s) — input=%d output=%d (turn own input=%s); "
                "run total input=%d output=%d",
                extra_usage.runs, extra_usage.aux_calls,
                extra_usage.input_tokens, extra_usage.output_tokens,
                result.get("input_tokens", 0) or 0,
                accum_input_tokens, accum_output_tokens,
                extra={"execution_id": execution_id},
            )
            # Attribution: which child cost what. The aggregate hides a wide
            # spread (execution 240: 24,137 to 302,527 across seven children),
            # and that spread is the actionable part.
            _breakdown.extend(extra_usage.breakdown())
            _aux.extend(extra_usage.aux_breakdown())

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

        verdict = await supervisor.evaluate(
            final_answer=final_answer,
            tool_calls=result.get("tool_calls", []),
            confidence=confidence,
            retry_count=supervisor_retry_count,
            messages=result.get("messages", []),
        )
        await _record_supervisor_reward(execution_id, verdict.score)

        if verdict.action == SupervisorAction.PASS:
            break

        if verdict.action == SupervisorAction.RETRY:
            supervisor_retry_count += 1
            # A RETRY re-runs the ENTIRE ReAct loop — by far the largest single
            # token multiplier in a run (roughly 2x). Logged at WARNING with the
            # score and its breakdown so the retry RATE and what triggered it are
            # visible in production; the threshold should not be tuned without
            # that number.
            logger_instance.warning(
                "supervisor_loop: RETRY %d/%d — re-running the full agent loop "
                "(score=%.3f, reason=%s, breakdown=%s)",
                supervisor_retry_count, max_iterations - 1, verdict.score,
                verdict.reason, getattr(verdict, "score_breakdown", None),
                extra={"execution_id": execution_id},
            )
            result["supervisor_retried"] = True
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

    # Compression accounting for the run. Logged (not just accumulated) because
    # the sidecar fails SILENTLY useful: it answers 200 OK and hands the text
    # straight back for content it cannot shrink, so "compression is enabled"
    # tells you nothing about whether it did anything.
    if _breakdown:
        result["subagent_usage"] = sorted(
            _breakdown, key=lambda c: c["input_tokens"], reverse=True,
        )
    if _aux:
        merged: dict = {}
        for row in _aux:
            acc = merged.setdefault(
                row["source"],
                {"source": row["source"], "calls": 0, "input_tokens": 0, "output_tokens": 0},
            )
            acc["calls"] += row["calls"]
            acc["input_tokens"] += row["input_tokens"]
            acc["output_tokens"] += row["output_tokens"]
        result["auxiliary_usage"] = sorted(
            merged.values(), key=lambda r: r["input_tokens"], reverse=True,
        )

    _stats = get_compression_stats()
    if _stats is not None and (_stats.calls or _stats.skipped_uncompressible):
        logger_instance.info(
            "compression: %s", _stats.summary(),
            extra={"execution_id": execution_id},
        )
        result["compression_stats"] = {
            "calls": _stats.calls,
            "chars_before": _stats.chars_before,
            "chars_after": _stats.chars_after,
            "saved_pct": _stats.saved_pct(),
            "unchanged": _stats.unchanged,
            "skipped_uncompressible": _stats.skipped_uncompressible,
            "discarded": _stats.discarded,
            "errors": _stats.errors,
        }

    return (
        result, accum_input_tokens, accum_output_tokens,
        accum_cache_read_tokens, accum_cache_creation_tokens,
    )
