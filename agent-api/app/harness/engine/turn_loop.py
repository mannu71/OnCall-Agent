"""Native turn-loop engine — the hand-rolled ReAct loop.

Mirrors the reference agentic-loop pattern: a single append-only message
list is the sole source of truth; one state struct is mutated atomically at
named "continue sites"; one turn = compress -> call model -> execute tools
-> decide whether to continue. Implements the full resilience ladder: bounded
max-turns forced synthesis, reactive-compact-and-retry on context overflow,
truncation escalation (raise max_output_tokens once) then up to N "resume
mid-thought" continuations, a mid-thought-preamble continuation, and
exponential-backoff retry on transient/throttle errors. Errors are withheld
from the stream until their recovery rung is exhausted — only then does
``stream_callback.on_error`` fire.

Governance, prompt caching, and playbook tools come from
``app.harness.react_agent.prepare_action_space`` so both engines build an
identical action space. Result-envelope shape and message serialization
reuse ``agent_runner._serialize_agent_result`` so callers (executor.py,
the UI, evals) cannot tell which engine produced a result.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.harness.engine.loop_state import ContinueReason, StopReason, TokenLedger, TurnLoopState
from app.harness.engine import recovery, tool_exec

logger = logging.getLogger(__name__)

#: How many extra turns the max-turns forced-synthesis recovery gets before a
#: hard stop, mirroring agent_runner's _CONTINUATION_RECURSION_LIMIT (10 graph
#: steps ~= 5 ReAct iterations) in native turn-count terms.
_CONTINUATION_TURN_BUDGET = 5

_FORCED_SYNTHESIS_NUDGE = (
    "You have gathered enough information and may NOT call any more tools. "
    "Give your FINAL answer now, citing concrete file:line evidence. If you "
    "could not fully determine something, say so plainly."
)


def _default_max_turns() -> int:
    # LangGraph counts a "step" as one node transition (~2 per ReAct
    # iteration); recursion_limit=25 ~= 12 iterations. Halve it for the
    # native loop's direct turn count.
    return max(1, settings.agent_recursion_limit // 2)


class _NoopCompressionPipeline:
    """Fallback when the compaction manager can't be constructed — never
    blocks a run; compaction just doesn't happen this turn."""

    async def maybe_compact(self, messages: List[Any]) -> List[Any]:
        return messages

    async def reactive_compact(self, messages: List[Any]) -> List[Any]:
        return messages


def _continue_site(state: TurnLoopState, reason: ContinueReason, log: Any) -> None:
    """The single place another loop iteration gets scheduled."""
    state.continue_reason = reason
    log.debug("engine: continue reason=%s turn=%d", reason.value, state.turn_count)


def _extract_stream_text(content: Any) -> str:
    """Bedrock streams content as a list of blocks; other providers as str."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class TurnLoop:
    """One instance = one agent run (or one subagent child run)."""

    def __init__(
        self,
        llm: Any,
        tools: List[Any],
        system_prompt: str,
        *,
        agent_config: Optional[Dict[str, Any]] = None,
        permission_mode: str = "default",
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        execution_port: Any = None,
        policies: Optional[List[Dict[str, Any]]] = None,
        stream_callback: Any = None,
        model_name: Optional[str] = None,
        max_turns: Optional[int] = None,
        logger_instance: Any = None,
        retry_predicate: Any = None,
    ) -> None:
        from app.harness.react_agent import prepare_action_space

        self.action_space = prepare_action_space(
            llm, tools, system_prompt,
            permission_mode=permission_mode, session_id=session_id,
            execution_port=execution_port, policies=policies,
        )
        # prepare_action_space only binds tools onto the model for the
        # providers it prompt-caches (Bedrock/Anthropic); create_react_agent
        # would otherwise bind tools itself. Do that here for parity across
        # every provider, since this loop doesn't go through create_react_agent.
        if self.action_space.is_bedrock:
            self._model = self.action_space.model_for_agent
        else:
            self._model = self.action_space.model_for_agent.bind_tools(
                self.action_space.all_tools
            )
        self.tools_by_name = {t.name: t for t in self.action_space.all_tools}
        self.execution_id = execution_id
        self.execution_port = execution_port
        self.stream_callback = stream_callback
        self.model_name = model_name
        self.max_turns = max_turns if max_turns is not None else _default_max_turns()
        self.log = logger_instance or logger
        self.retry_predicate = retry_predicate
        self._compaction_session = session_id or execution_id or "native-engine"
        self._compaction_model = (
            getattr(llm, "model_id", None) or getattr(llm, "model", None) or model_name
        )
        self._compression = self._build_compression_pipeline()

    def _build_compression_pipeline(self) -> Any:
        # Construction must never break a run — mirrors _maybe_compact's own
        # exception guard, extended to cover transport/manager setup itself.
        try:
            from app.core.memory.compaction_manager import ContextCompactionManager
            from app.core.transport import get_transport
            from app.harness.engine.compression import CompressionPipeline

            mgr = ContextCompactionManager(
                transport=get_transport(),
                session_id=self._compaction_session,
                model=self._compaction_model,
            )
            return CompressionPipeline(mgr)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("engine: compression pipeline setup skipped (%s)", exc)
            return _NoopCompressionPipeline()

    async def _maybe_compact(self, messages: List[Any]) -> List[Any]:
        """Proactive compaction before every model call — microcompact then
        threshold autocompact (same two-tier ladder the LangGraph
        pre_model_hook runs)."""
        try:
            return await self._compression.maybe_compact(list(messages))
        except Exception:  # noqa: BLE001 — compaction must never break a run
            return messages

    async def _call_model_once(
        self, full_messages: List[Any], ledger: TokenLedger, max_tokens_override: Optional[int],
    ) -> Any:
        model = self._model.bind(max_tokens=max_tokens_override) if max_tokens_override else self._model
        config = {"callbacks": [ledger.callback]}
        if self.stream_callback is None:
            return await model.ainvoke(full_messages, config=config)

        accumulated = None
        async for chunk in model.astream(full_messages, config=config):
            text = _extract_stream_text(getattr(chunk, "content", None))
            if text:
                try:
                    await self.stream_callback.on_llm_token(text)
                except Exception:  # noqa: BLE001
                    pass
            accumulated = chunk if accumulated is None else accumulated + chunk
        return accumulated

    async def _call_model(
        self,
        full_messages: List[Any],
        ledger: TokenLedger,
        max_tokens_override: Optional[int] = None,
    ) -> Any:
        """Backoff-wrapped model call — transient/throttle errors retry here
        in place; a fallback chain's throttle predicate can still bubble
        immediately so the outer failover loop switches target."""
        return await recovery.call_model_with_backoff(
            self._call_model_once, full_messages, ledger, max_tokens_override,
            retry_predicate=self.retry_predicate,
        )

    async def run(self, initial_messages: List[Any]) -> Dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage
        from app.workflow.strategies.react.agent_runner import (
            sanitize_messages_for_model,
            _serialize_agent_result,
            _stop_reason_of,
            _strip_dangling_tool_calls,
            _TRUNCATED_STOP_REASONS,
        )

        state = TurnLoopState(messages=list(initial_messages))
        system_message = (
            SystemMessage(content=self.action_space.prompt_arg)
            if isinstance(self.action_space.prompt_arg, str)
            else self.action_space.prompt_arg
        )
        tool_call_running_count = 0

        while True:
            state.turn_count += 1
            if state.turn_count > self.max_turns:
                if not state.did_forced_synthesis:
                    self.log.warning(
                        "engine: max turns (%d) reached — forcing a final synthesis "
                        "from partial state (execution_id=%s)",
                        self.max_turns, self.execution_id,
                    )
                    state.did_forced_synthesis = True
                    state.messages = _strip_dangling_tool_calls(state.messages)
                    state.messages.append(HumanMessage(content=_FORCED_SYNTHESIS_NUDGE))
                    # Give the recovery a small extra budget in case the model
                    # still tries to call a tool despite the nudge.
                    self.max_turns = state.turn_count - 1 + _CONTINUATION_TURN_BUDGET
                    _continue_site(state, ContinueReason.TOKEN_BUDGET_CONTINUATION, self.log)
                    continue
                state.stop_reason = StopReason.MAX_TURNS
                break

            model_view = await self._maybe_compact(state.messages)
            request_msgs = sanitize_messages_for_model(model_view)
            full_messages = [system_message] + request_msgs

            try:
                ai_msg = await self._call_model(
                    full_messages, state.ledger, state.max_output_tokens_override,
                )
            except Exception as exc:  # noqa: BLE001 — classify before deciding
                from app.core.error_classifier import classify_error
                classified = classify_error(exc)
                if classified.should_compress and not state.has_attempted_reactive_compact:
                    self.log.warning(
                        "engine: context overflow detected — reactive compaction "
                        "and retry (execution_id=%s)", self.execution_id,
                    )
                    state.has_attempted_reactive_compact = True
                    state.withheld_errors.append(classified)  # withheld unless recovery fails
                    state.messages = await self._compression.reactive_compact(state.messages)
                    _continue_site(state, ContinueReason.REACTIVE_COMPACT_RETRY, self.log)
                    continue
                if classified.should_compress:
                    self.log.warning(
                        "engine: context overflow persists after reactive compaction "
                        "— giving up (execution_id=%s)", self.execution_id,
                    )
                    await self._surface_withheld(state, "Context window exceeded even after compaction.")
                    state.stop_reason = StopReason.PROMPT_TOO_LONG
                    break
                # Not compressible — a throttle/transient error that exhausted
                # in-place backoff (recovery.call_model_with_backoff) or a
                # should_fallback throttle deliberately bubbled here so the
                # executor's fallback-chain failover can switch target. Either
                # way this loop can't recover it itself; propagate.
                raise
            state.messages.append(ai_msg)

            tool_calls = list(getattr(ai_msg, "tool_calls", None) or [])
            stop_reason = _stop_reason_of(ai_msg)
            is_truncated = stop_reason in _TRUNCATED_STOP_REASONS

            if tool_calls and not is_truncated:
                exec_result = await tool_exec.execute_tool_calls(
                    ai_msg, self.tools_by_name,
                    logger_instance=self.log, execution_id=self.execution_id,
                    execution_port=self.execution_port,
                    stream_callback=self.stream_callback,
                    tool_call_count_before=tool_call_running_count,
                )
                tool_call_running_count += len(tool_calls)
                state.messages.extend(exec_result.tool_messages)
                if exec_result.steer_messages:
                    state.messages.extend(exec_result.steer_messages)
                    _continue_site(state, ContinueReason.COLLAPSE_DRAIN_RETRY, self.log)
                else:
                    _continue_site(state, ContinueReason.TOOL_RESULTS, self.log)
                continue

            # ── Truncation ladder: escalate max_output_tokens once, then up
            # to MAX_OUTPUT_TOKENS_RECOVERY_LIMIT "resume" continuations. ─────
            if is_truncated and not tool_calls:
                if not state.max_output_tokens_escalated:
                    state.max_output_tokens_escalated = True
                    ceiling = min(
                        settings.agent_max_output_tokens * 2,
                        recovery.MAX_OUTPUT_TOKENS_CEILING,
                    )
                    state.max_output_tokens_override = ceiling
                    self.log.warning(
                        "engine: turn truncated (stop_reason=%s) — escalating "
                        "max_output_tokens to %d and retrying (execution_id=%s)",
                        stop_reason, ceiling, self.execution_id,
                    )
                    state.messages = _strip_dangling_tool_calls(state.messages)
                    state.messages.append(HumanMessage(content=recovery.CONTINUE_TRUNCATED_NUDGE))
                    _continue_site(state, ContinueReason.MAX_OUTPUT_TOKENS_ESCALATE, self.log)
                    continue
                if state.max_output_tokens_recovery_count < recovery.MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    state.max_output_tokens_recovery_count += 1
                    self.log.warning(
                        "engine: still truncated after escalation — resume attempt "
                        "%d/%d (execution_id=%s)",
                        state.max_output_tokens_recovery_count,
                        recovery.MAX_OUTPUT_TOKENS_RECOVERY_LIMIT, self.execution_id,
                    )
                    state.messages = _strip_dangling_tool_calls(state.messages)
                    state.messages.append(HumanMessage(content=recovery.CONTINUE_TRUNCATED_NUDGE))
                    _continue_site(state, ContinueReason.MAX_OUTPUT_TOKENS_RECOVERY, self.log)
                    continue
                # Ladder exhausted — honest partial rather than a confident
                # half-thought.
                state.truncated = True
                self.log.warning(
                    "engine: truncation recovery ladder exhausted — returning "
                    "partial answer (execution_id=%s)", self.execution_id,
                )
                state.stop_reason = StopReason.COMPLETED
                break

            # ── Mid-thought preamble: the model narrated its next action
            # ("Let me search for…") instead of acting or concluding. Give it
            # one more turn to actually act/synthesize. ─────────────────────
            from app.workflow.strategies.react.agent_runner import extract_text_content
            from app.workflow.strategies.react.helpers import looks_like_midthought
            current_text = extract_text_content(ai_msg.content) if ai_msg.content else ""
            if (
                not state.did_midthought_continuation
                and looks_like_midthought(current_text)
                and len(current_text.strip()) < 400
            ):
                state.did_midthought_continuation = True
                self.log.warning(
                    "engine: mid-thought preamble detected (%r) — continuing one "
                    "turn so it acts/synthesizes (execution_id=%s)",
                    current_text[:80], self.execution_id,
                )
                _continue_site(state, ContinueReason.MIDTHOUGHT_CONTINUATION, self.log)
                continue

            state.stop_reason = StopReason.COMPLETED
            break

        parsed = _serialize_agent_result({"messages": state.messages})
        if state.truncated:
            parsed["truncated"] = True
            if not (parsed.get("final_answer") or "").strip().startswith(
                "Investigation was cut off"
            ):
                parsed["final_answer"] = (
                    "Investigation was cut off by the model output limit before it "
                    "could complete. Partial findings so far:\n\n"
                    + (parsed.get("final_answer") or "")
                ).strip()

        total_input_tokens = state.ledger.input_tokens or parsed["fallback_input_tokens"]
        total_output_tokens = state.ledger.output_tokens or parsed["fallback_output_tokens"]
        cache_read_tokens = state.ledger.cache_read_tokens
        cache_creation_tokens = state.ledger.cache_creation_tokens

        self.log.info(
            "engine: run completed with %d messages, %d tool calls, "
            "input_tokens=%d output_tokens=%d turns=%d stop_reason=%s%s",
            len(parsed["messages"]), len(parsed["tool_calls"]),
            total_input_tokens, total_output_tokens, state.turn_count,
            state.stop_reason.value if state.stop_reason else "?",
            " [truncated]" if state.truncated else "",
        )

        if self.execution_port is not None and self.execution_id is not None:
            try:
                await self.execution_port.publish_token_usage(self.execution_id, {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_creation_tokens": cache_creation_tokens,
                })
            except Exception:  # noqa: BLE001
                pass

        result: Dict[str, Any] = {
            "final_answer": parsed["final_answer"],
            "messages": parsed["messages"],
            "tool_calls": parsed["tool_calls"],
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
        }
        if parsed.get("truncated"):
            result["truncated"] = True

        try:
            from app.core.grounding import ungrounded_ids, extract_ids
            allow = extract_ids(self._last_user_text(state.messages))
            flagged = ungrounded_ids(
                result["final_answer"] or "", parsed.get("evidence_ids") or set(), allow,
            )
            if flagged:
                result["ungrounded_ids"] = flagged
        except Exception:  # noqa: BLE001 — a telemetry guard must never break a run
            pass

        return result

    async def _surface_withheld(self, state: TurnLoopState, message: str) -> None:
        """Surface withheld errors to the stream — only called once a
        recovery rung is exhausted. Errors that recover successfully are
        never surfaced (the whole point of withholding them)."""
        if self.stream_callback is None or not state.withheld_errors:
            return
        try:
            await self.stream_callback.on_error(message)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _last_user_text(messages: List[Any]) -> str:
        from langchain_core.messages import HumanMessage
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = getattr(msg, "content", "")
                return content if isinstance(content, str) else str(content)
        return ""


__all__ = ["TurnLoop"]
