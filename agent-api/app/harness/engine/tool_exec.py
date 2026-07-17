"""Native turn-loop engine — tool dispatch.

Executes the ``tool_calls`` on one AIMessage and returns the matching
``ToolMessage`` list, applying the same governance/guardrail/steer-note
machinery ``agent_runner.execute_agent_stream`` applies for the LangGraph
path (policy tool-count ceiling, ``ToolCallGuardrailController``,
failure classification, steer-note injection at the tool boundary) so both
engines behave identically from the model's point of view.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolExecResult:
    tool_messages: List[Any]
    steer_messages: List[Any] = field(default_factory=list)


async def _invoke_one(
    tool: Any,
    tool_name: str,
    args: Dict[str, Any],
    timeout: Optional[float] = None,
) -> str:
    try:
        if timeout and timeout > 0:
            result = await asyncio.wait_for(tool.ainvoke(args), timeout=timeout)
        else:
            result = await tool.ainvoke(args)
        return result if isinstance(result, str) else str(result)
    except asyncio.TimeoutError:
        # Honest error surfaced to the model (never raised) — the turn
        # continues so the model can adapt (narrower query / different tool)
        # rather than the whole run hanging on one slow tool.
        return (
            f"Error: tool '{tool_name}' timed out after {timeout:.0f}s and was "
            f"cancelled. Try a narrower query or a different approach."
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the model, not raised
        return f"Error: {exc}\n Please fix your mistakes."


async def _record_failure_fingerprint(
    tool_name: str, output_str: str, execution_id: Optional[str],
) -> None:
    """Live failure->governance hook (3.2): upsert this failure's fingerprint
    into failure_ledger in real time. Gated by
    settings.governance_conversion_enabled (default False) — the flag check
    is the first thing done so the common (flag-off) path costs one
    attribute read, no DB round-trip. Best-effort — never breaks a run.
    """
    try:
        from app.config import settings
        if not getattr(settings, "governance_conversion_enabled", False):
            return
        from app.core.improvement.analyzer import fingerprint_error
        from app.infrastructure.persistence import failure_ledger_repository
        fp = fingerprint_error(f"{tool_name}: {output_str}")
        await failure_ledger_repository.record(fp, execution_id)
    except Exception:  # noqa: BLE001 — governance must never break a run
        pass


async def execute_tool_calls(
    ai_message: Any,
    tools_by_name: Dict[str, Any],
    *,
    logger_instance: Any = None,
    execution_id: Optional[str] = None,
    execution_port: Any = None,
    stream_callback: Any = None,
    tool_call_count_before: int = 0,
    recorder: Any = None,
    step_index: int = 0,
    tool_timeout: Optional[float] = None,
) -> ToolExecResult:
    """Run every ``tool_calls`` entry on ``ai_message`` concurrently.

    Mirrors LangGraph's ``ToolNode``: tool calls on a single AIMessage run
    in parallel (``asyncio.gather``); each raised exception is caught and
    surfaced to the model as ToolMessage content rather than aborting the
    turn. Returns the ToolMessages in the original tool_calls order, plus
    any steer notes drained at the tool boundary.
    """
    from langchain_core.messages import HumanMessage, ToolMessage
    from app.core.tools.tool_guardrails import (
        ToolCallGuardrailController,
        classify_tool_failure,
        append_toolguard_guidance,
    )

    log = logger_instance or logger
    tool_calls = list(getattr(ai_message, "tool_calls", None) or [])
    if not tool_calls:
        return ToolExecResult(tool_messages=[])

    _resolved_policy = None
    try:
        from app.core import policy as _policy_mod
        _resolved_policy = _policy_mod.get_current()
    except Exception:  # noqa: BLE001 — governance must never break a run
        _resolved_policy = None

    guardrail = ToolCallGuardrailController(
        getattr(_resolved_policy, "guardrail_config", None)
    )
    tool_call_count = tool_call_count_before

    async def _run_one(tc: Dict[str, Any]) -> ToolMessage:
        nonlocal tool_call_count
        tc_name = tc.get("name") or ""
        tc_args = tc.get("args") or {}
        tc_id = tc.get("id")

        tool_call_count += 1
        if _resolved_policy is not None:
            try:
                from app.core.policy.runtime import evaluate_tool_count
                quota = evaluate_tool_count(_resolved_policy, tool_call_count)
                if quota.blocks and stream_callback is not None:
                    try:
                        await stream_callback.on_error(f"[Policy] {quota.reason}")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001 — governance must never break a run
                pass

        gc_pre = guardrail.before_call(tc_name, tc_args)
        if gc_pre.should_halt and stream_callback is not None:
            try:
                await stream_callback.on_error(f"[Guardrail HALT] {gc_pre.message}")
            except Exception:  # noqa: BLE001
                pass

        if stream_callback is not None:
            try:
                await stream_callback.on_tool_call(tc_name, tc_args)
            except Exception:  # noqa: BLE001
                pass

        _t0 = asyncio.get_event_loop().time()
        tool = tools_by_name.get(tc_name)
        if tool is None:
            output_str = f"Error: tool '{tc_name}' is not available."
        else:
            output_str = await _invoke_one(tool, tc_name, tc_args, timeout=tool_timeout)
        _latency_ms = (asyncio.get_event_loop().time() - _t0) * 1000.0

        is_failed, _reason = classify_tool_failure(tc_name, output_str)
        if is_failed:
            await _record_failure_fingerprint(tc_name, output_str, execution_id)
        if recorder is not None:
            try:
                recorder.record_tool_call(
                    step_index, tool_name=tc_name,
                    status="error" if is_failed else "ok",
                    latency_ms=_latency_ms,
                    error_class=_reason if is_failed else None,
                )
            except Exception:  # noqa: BLE001 — recording must never break a run
                pass
        gc_post = guardrail.after_call(tc_name, tc_args, output_str, failed=is_failed)
        if gc_post.action in ("warn", "block", "halt"):
            output_str = append_toolguard_guidance(output_str, gc_post)
            log.warning(
                "engine: guardrail %s after tool '%s' (count=%d) — %s",
                gc_post.action.upper(), tc_name, gc_post.count, gc_post.message,
                extra={"execution_id": execution_id},
            )
            if gc_post.action == "halt":
                if stream_callback is not None:
                    try:
                        await stream_callback.on_error(
                            f"Tool '{tc_name}' halted the run: {gc_post.message}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                raise RuntimeError(
                    f"Tool '{tc_name}' halted the agent run: {gc_post.message}"
                )

        if stream_callback is not None:
            try:
                await stream_callback.on_tool_result(
                    tc_name, output_str[:2000], failed=is_failed,
                )
            except Exception:  # noqa: BLE001
                pass

        return ToolMessage(content=output_str, tool_call_id=tc_id)

    tool_messages = await asyncio.gather(*(_run_one(tc) for tc in tool_calls))

    # ── /steer injection ──────────────────────────────────────────────
    # Drain any engineer notes queued via POST /steer, injected at the tool
    # boundary (after all of this turn's tool results, before the next
    # model call) — same point LangGraph's on_tool_end handler injects them.
    steer_messages: List[Any] = []
    if execution_id and execution_port is not None:
        for note in execution_port.drain_steer_notes(execution_id):
            steer_messages.append(HumanMessage(content=f"[Engineer Note] {note}"))
            log.info(
                "engine: injected steer note at tool boundary (execution_id=%s)",
                execution_id,
            )

    return ToolExecResult(tool_messages=list(tool_messages), steer_messages=steer_messages)


__all__ = ["ToolExecResult", "execute_tool_calls"]
