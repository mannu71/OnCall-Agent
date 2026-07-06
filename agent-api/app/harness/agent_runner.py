"""ReAct agent execution — invoke, stream, and result parsing."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.config import settings

from app.core.error_classifier import classify_error
from app.core.retry import with_retry
from app.core.telemetry import agent_span, get_current_trace_id
from app.harness.helpers import compact_input_state, looks_like_midthought
from app.harness.hitl import emit_hitl_pause
from app.workflow.strategies.react.streaming import StreamCallback
from app.workflow.execution_port import ExecutionPort

logger = logging.getLogger(__name__)


def _strip_dangling_tool_calls(messages: list) -> list:
    """Drop trailing AIMessages whose ``tool_calls`` have no matching ToolMessage.

    A recursion-limit stop leaves the final AIMessage requesting tools that were
    never executed; re-invoking that history raises INVALID_CHAT_HISTORY. Trimming
    the unmatched tail lets the model synthesize from the results it already has.
    """
    try:
        satisfied = {
            tcid for m in messages
            if (tcid := getattr(m, "tool_call_id", None)) is not None
        }
        out = list(messages)
        while out:
            calls = getattr(out[-1], "tool_calls", None) or []
            ids = [c.get("id") for c in calls if isinstance(c, dict)]
            if ids and not all(i in satisfied for i in ids):
                out.pop()
            else:
                break
        return out
    except Exception:  # noqa: BLE001 — sanitization is best-effort
        return messages


# Minimal stand-ins for a message whose content came back empty. Bedrock
# Converse (and most providers) reject ANY message with empty content
# ("messages.N: ... must have non-empty content"), which hard-fails the whole
# request. This is exactly how a run can die mid-loop: context compaction (the
# pre_model_hook) summarizes via an auxiliary LLM, and if that summary is empty
# (e.g. a mis-configured summarization model) the compacted message array
# carries an empty message into the next model call.
_EMPTY_TEXT_PLACEHOLDER = "(no content)"
_EMPTY_TOOL_PLACEHOLDER = "(no tool output)"

# Recovery/continuation turns (truncation continuation, mid-thought continuation,
# recursion-limit synthesis) need their OWN recursion budget. Inheriting the
# global ``agent_recursion_limit`` means a deployment that lowers it could starve
# these single-purpose finishing turns and leave the user with a half-answer.
# A small fixed budget is enough: at most one pending tool plus a synthesis turn.
_CONTINUATION_RECURSION_LIMIT = 10

# Synthetic recovery nudges injected mid-run as HumanMessages (recursion-limit
# forced synthesis / truncation continuation). The native engine
# (app.harness.engine.turn_loop / recovery) imports these rather than
# duplicating the literal text, so both engines' recovery turns stay in sync.
# app.harness.chat_history's turn segmentation also needs these verbatim: a
# saved trajectory can contain one of these as a mid-run HumanMessage, and
# that must never be mistaken for a new user turn boundary.
FORCED_SYNTHESIS_NUDGE = (
    "You have gathered enough information and may NOT call any more tools. "
    "Give your FINAL answer now, citing concrete file:line evidence. If you "
    "could not fully determine something, say so plainly."
)
CONTINUE_TRUNCATED_NUDGE = (
    "Continue and COMPLETE your previous answer. Do not repeat what you "
    "already wrote; finish it, citing concrete file:line evidence."
)
SYNTHETIC_NUDGE_PREFIXES = (FORCED_SYNTHESIS_NUDGE, CONTINUE_TRUNCATED_NUDGE)


def sanitize_messages_for_model(messages: list) -> list:
    """Guarantee no message reaches the model with empty content.

    Mirrors claude-code's "repair with synthetic placeholders" approach: rather
    than DROP an empty message (which would break tool_use/tool_result pairing
    and the user/assistant alternation Bedrock requires), substitute a minimal
    placeholder so the request stays structurally valid. An assistant message
    that carries ``tool_calls`` is left as-is — empty text there is legitimate.
    """
    if not messages:
        return messages
    try:
        from langchain_core.messages import AIMessage, ToolMessage
    except Exception:  # noqa: BLE001 — never break a run on a guard
        return messages

    repaired: list = []
    changed = False
    for msg in messages:
        content = getattr(msg, "content", None)
        is_empty = (
            content is None
            or (isinstance(content, str) and not content.strip())
            or (isinstance(content, list) and len(content) == 0)
        )
        # An AIMessage requesting tools is valid with empty text content.
        if is_empty and isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            repaired.append(msg)
            continue
        if not is_empty:
            repaired.append(msg)
            continue
        placeholder = _EMPTY_TOOL_PLACEHOLDER if isinstance(msg, ToolMessage) else _EMPTY_TEXT_PLACEHOLDER
        changed = True
        try:
            repaired.append(msg.model_copy(update={"content": placeholder}))
        except Exception:  # noqa: BLE001 — fall back to in-place set
            try:
                msg.content = placeholder
            except Exception:  # noqa: BLE001
                pass
            repaired.append(msg)
    if not changed:
        # Identity-preserving: callers (e.g. the pre_model_hook) rely on an
        # unchanged return to keep the compaction / prompt-cache no-op fast path.
        return messages
    logger.warning(
        "ReactStrategy: repaired %d empty message(s) with placeholders before "
        "the model call (compaction/summarization likely produced empty content)",
        sum(1 for a, b in zip(messages, repaired) if a is not b),
    )
    return repaired


def build_initial_messages(
    conversation_history: Optional[list],
    user_query: str,
    logger_instance: Any,
) -> list:
    """Replay prior chat turns (if any) then append this turn's query.

    Shared by both agent engines (LangGraph and the native turn loop) so a
    resumed/follow-up conversation looks identical regardless of which one
    ran. Compaction downstream trims this when it grows large.

    ``conversation_history`` entries are plain {role, content} text dicts
    when supplied by the UI, or the extended shape produced by
    ``app.harness.chat_history.rebuild_chat_history`` (assistant entries
    carrying ``tool_calls``, ``role="tool"`` entries carrying
    ``tool_call_id``) when tool-inclusive replay reconstructs a session's
    prior tool_use/tool_result pairs. Both shapes are handled here so a
    caller never needs to know which one it received.
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

    _prior_messages: list = []
    for _m in (conversation_history or []):
        try:
            _role = str(_m.get("role") or "").lower()
            _text = _m.get("content") or ""
            _tool_calls = _m.get("tool_calls")
            _tool_call_id = _m.get("tool_call_id")

            if _role in ("assistant", "ai", "agent"):
                # An assistant turn that only called tools carries no text —
                # legitimate, same rule sanitize_messages_for_model applies.
                if not _text and not _tool_calls:
                    continue
                if _tool_calls:
                    _prior_messages.append(AIMessage(
                        content=_text,
                        tool_calls=[
                            {
                                "id": _tc.get("id"),
                                "name": _tc.get("name"),
                                "args": _tc.get("args") or {},
                            }
                            for _tc in _tool_calls
                            if isinstance(_tc, dict) and _tc.get("id")
                        ],
                    ))
                else:
                    _prior_messages.append(AIMessage(content=_text))
            elif _role in ("user", "human"):
                if not _text:
                    continue
                _prior_messages.append(HumanMessage(content=_text))
            elif _role == "system":
                if not _text:
                    continue
                # A per-chat-session compaction summary (see
                # compact_chat_session_if_needed) replaces older turns with one
                # role="system" history entry. Replay it as a HumanMessage
                # (clearly labelled) rather than a LangChain SystemMessage —
                # this keeps the CACHE CONTRACT's "exactly one system prompt,
                # at the start" invariant intact while still surfacing the
                # summary as visible context, mid-history.
                _prior_messages.append(
                    HumanMessage(content=f"[Prior conversation summary]\n{_text}")
                )
            elif _role == "tool":
                if not _tool_call_id:
                    logger_instance.debug(
                        "ReactStrategy: skipping tool message from conversation "
                        "history (no tool_call_id)"
                    )
                    continue
                # Tool-inclusive replay (app.harness.chat_history) — a prior
                # turn's tool result, reconstructed from the persisted
                # trajectory. Content may carry an opaque pseudonymized
                # placeholder from a vault that no longer exists; treated as
                # plain text either way, same as any other tool output.
                _prior_messages.append(
                    ToolMessage(content=_text or _EMPTY_TOOL_PLACEHOLDER, tool_call_id=_tool_call_id)
                )
            else:
                logger_instance.warning(
                    "ReactStrategy: dropping conversation history message with unrecognised role %r",
                    _role,
                )
        except Exception as exc:  # noqa: BLE001
            logger_instance.debug(
                "ReactStrategy: skipping malformed conversation history entry: %s", exc
            )
            continue

    if _prior_messages:
        # Tool-inclusive replay can end on an AIMessage whose tool_calls
        # weren't all matched by a following ToolMessage — either the budget
        # in build_tool_inclusive_history cut a segment mid-way (has other
        # ToolMessages present), or the trailing turn's tool call was never
        # resolved at all (no ToolMessages present). Either way Bedrock
        # rejects that history outright, so always run the repair rather than
        # gating it on `_has_tool_messages` (which missed the latter case).
        # No-op when there is nothing dangling to strip.
        _prior_messages = _strip_dangling_tool_calls(_prior_messages)

    return _prior_messages + [HumanMessage(content=user_query)]


async def execute_agent(
    agent: Any,
    user_query: str,
    logger_instance: Any,
    execution_id: Optional[str] = None,
    stream_callback: Optional[StreamCallback] = None,
    thread_id: Optional[str] = None,
    execution_port: Optional[ExecutionPort] = None,
    conversation_history: Optional[list] = None,
    retry_predicate: Optional[Any] = None,
    recursion_limit: Optional[int] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute the LangGraph ReAct agent with the user's query.

    When a stream_callback is provided, uses ``agent.astream_events()`` to
    deliver LLM tokens and tool events in real time.  Falls back to
    ``agent.ainvoke()`` otherwise.

    The outer call is wrapped with ``with_retry`` so transient LLM errors
    (rate-limit, 502/503) are automatically retried up to 3 times.

    Args:
        agent: Compiled LangGraph agent graph.
        user_query: The user's question / investigation request.
        logger_instance: Logger for execution-scoped logging.
        execution_id: Execution ID for logging.
        stream_callback: Optional streaming callback protocol.
        model_name: Model id used for THIS run — enables the live
            token_usage_delta events to include context-window fields
            (window size / used tokens / used %). Omitted → live events still
            carry raw token counts, just without context sizing.

    Returns:
        Dict with:
          - final_answer: str — the last AI response
          - messages: list — all messages in the conversation
          - tool_calls: list — summary of tools invoked
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
    from app.core.telemetry import agent_span, get_current_trace_id

    logger_instance.info("ReactStrategy: invoking agent with query: %.100s", user_query)

    # Terminal-state / trajectory-event inputs (parity with the native turn
    # loop — see app.harness.terminal_state, app.harness.step_recorder,
    # app.harness.verify_tracking). _stop_reason/_did_forced_synthesis are
    # set at the recovery branches below; the recorder/verify_state are
    # populated once, post-hoc, right before the result dict is built.
    from app.harness.step_recorder import build_step_recorder
    from app.harness.verify_tracking import VerifyState
    _recorder = build_step_recorder(execution_id, model_id=model_name)
    _verify_state = VerifyState()
    _stop_reason = "completed"
    _did_forced_synthesis = False

    input_state = {
        "messages": build_initial_messages(conversation_history, user_query, logger_instance)
    }

    # Task #7: compact accumulated messages before invoking the agent.
    # On a fresh call ``input_state["messages"]`` has just one
    # HumanMessage so this is a guaranteed no-op (compact_if_needed
    # returns the input unchanged when total tokens are under the
    # 85% threshold). The wire-in matters for callers that
    # pre-populate ``input_state`` with continuation messages and
    # for HITL re-entries; persistence of the StructuredSummary
    # across container restarts lives in the ``memory_summaries``
    # Postgres table (created in Task #2 DDL).
    try:
        from app.core.memory.compaction_manager import ContextCompactionManager
        from app.core.transport import get_transport
        summarisation_transport = get_transport()
        compaction_mgr = ContextCompactionManager(
            transport=summarisation_transport,
            session_id=execution_id or thread_id or "default",
        )
        input_state["messages"] = await compaction_mgr.compact_if_needed(
            input_state["messages"],
        )
    except Exception as exc:  # noqa: BLE001 — never break the agent
        # Compaction failed (e.g. mis-configured summarization model). Do NOT
        # silently proceed with a potentially oversized history — surface it and
        # fall back to a deterministic, LLM-free prune of stale tool results so
        # the next model call has a bounded message array.
        logger_instance.warning(
            "ReactStrategy: LLM compaction failed (%s); falling back to "
            "deterministic history prune (execution_id=%s)",
            exc, execution_id,
        )
        if stream_callback is not None:
            try:
                await stream_callback.on_error(
                    "Context compaction failed; pruning older tool results to "
                    "stay within the model's context window."
                )
            except Exception:  # noqa: BLE001 — never break a run on a notice
                pass
        try:
            input_state = compact_input_state(input_state)
        except Exception as _prune_exc:  # noqa: BLE001 — fallback is best-effort
            logger_instance.warning(
                "ReactStrategy: deterministic prune also failed (%s); "
                "proceeding uncompacted (execution_id=%s)",
                _prune_exc, execution_id,
            )

    # Repair any empty-content messages (e.g. from a failed compaction summary)
    # before they reach Bedrock, which rejects empty messages outright.
    input_state["messages"] = sanitize_messages_for_model(input_state["messages"])

    # Instrumentation prefix: the initial messages (user query + replayed
    # chat history) must not be re-recorded as this execution's events. The
    # count is in serialized-list coordinates — _serialize_agent_result drops
    # SystemMessages (e.g. a compaction summary), so exclude them here too.
    from langchain_core.messages import SystemMessage as _SysMsg
    _instrument_skip = sum(
        1 for _m in input_state["messages"] if not isinstance(_m, _SysMsg)
    )

    # Pass thread_id so the checkpointer can persist state across interrupts.
    run_config: Dict[str, Any] = {}
    if thread_id:
        run_config = {"configurable": {"thread_id": thread_id}}

    # Bound the ReAct loop. LangGraph counts a "step" as one node
    # transition; each ReAct iteration is ~2 steps (agent + tool node).
    # recursion_limit=12 ≈ 6 iterations — above that the agent is usually
    # thrashing and burning tokens for marginal value. Override via the
    # AGENT_RECURSION_LIMIT env var if a workflow legitimately needs more,
    # or pass an explicit ``recursion_limit`` (e.g. a subagent's max_turns cap).
    run_config["recursion_limit"] = recursion_limit if recursion_limit is not None else settings.agent_recursion_limit

    # Attach a token-usage callback so we get exact counts regardless of
    # streaming mode or provider (Bedrock, Anthropic, OpenAI).
    from app.core.streaming.callbacks import TokenUsageCallback
    token_cb = TokenUsageCallback()
    token_cb.execution_port = execution_port
    token_cb.execution_id = execution_id
    token_cb.model_name = model_name
    run_config.setdefault("callbacks", [])
    run_config["callbacks"].append(token_cb)

    try:
        async with agent_span("react_agent", execution_id=execution_id):
            trace_id = get_current_trace_id()
            if trace_id and execution_port is not None:
                execution_port.set_trace_id(execution_id, trace_id)

            if stream_callback is not None:
                result_state = await with_retry(
                    execute_agent_stream,
                    agent, input_state, stream_callback, logger_instance, execution_id,
                    run_config,
                    execution_port,
                    max_retries=3,
                    retry_on=retry_predicate,
                )
            else:
                result_state = await with_retry(
                    invoke_agent, agent, input_state, run_config,
                    max_retries=3,
                    retry_on=retry_predicate,
                )
    except Exception as exc:
        # Handle LangGraph HITL interrupt — surface to caller as a structured pause.
        try:
            from langgraph.types import GraphInterrupt
            if isinstance(exc, GraphInterrupt):
                interrupt_value = exc.args[0] if exc.args else {}
                logger_instance.info(
                    "ReactStrategy: HITL interrupt raised — execution_id=%s request_id=%s",
                    execution_id,
                    (interrupt_value[0].value if interrupt_value else {}).get("request_id", "?"),
                )
                # Publish hitl_pause SSE event via the active_executions queue.
                _interrupt_data = interrupt_value[0].value if interrupt_value else {}
                await emit_hitl_pause(execution_id, _interrupt_data, execution_port=execution_port)
                # Return a sentinel result so the caller knows we paused.
                return {
                    "final_answer": None,
                    "messages": [],
                    "tool_calls": [],
                    "hitl_paused": True,
                    "hitl_request": _interrupt_data,
                    "stop_reason": "hitl_paused",
                }
        except ImportError:
            pass

        # Recursion-limit exhaustion: the agent looped without concluding. Recover
        # the partial conversation from the checkpointer and force ONE tool-free
        # synthesis turn so the user gets the gathered findings, not a raw error.
        _recovered = False
        try:
            from langgraph.errors import GraphRecursionError
            if isinstance(exc, GraphRecursionError) and run_config.get("configurable"):
                logger_instance.warning(
                    "ReactStrategy: recursion limit hit — forcing a final synthesis from "
                    "partial state (execution_id=%s)", execution_id,
                )
                _stop_reason = "max_turns"
                _did_forced_synthesis = True
                snap = await agent.aget_state(run_config)
                msgs = _strip_dangling_tool_calls(
                    list((getattr(snap, "values", None) or {}).get("messages", []))
                )
                if msgs:
                    from langchain_core.messages import HumanMessage

                    # The recovery state is, by definition, the LARGEST the
                    # context ever gets (the agent looped to the recursion
                    # limit). Synthesizing over the full bloated history is a
                    # 200K+ token call that is slow and can time out. Compact it
                    # first — deterministic, LLM-free pruning of stale tool
                    # results — so the final synthesis is small, fast, and
                    # reliable. This is the automatic long-context recovery.
                    _before = len(msgs)
                    try:
                        msgs = compact_input_state({"messages": msgs})["messages"]
                        if len(msgs) != _before:
                            logger_instance.info(
                                "ReactStrategy: compacted recovery context %d → %d messages "
                                "before final synthesis (execution_id=%s)",
                                _before, len(msgs), execution_id,
                            )
                    except Exception as _comp_exc:  # noqa: BLE001 — best-effort
                        logger_instance.warning(
                            "ReactStrategy: recovery compaction skipped (%s); "
                            "synthesizing over full history (execution_id=%s)",
                            _comp_exc, execution_id,
                        )
                    msgs = _strip_dangling_tool_calls(msgs)

                    msgs = msgs + [HumanMessage(content=FORCED_SYNTHESIS_NUDGE)]
                    result_state = await invoke_agent(
                        agent, {"messages": msgs},
                        {**run_config, "recursion_limit": _CONTINUATION_RECURSION_LIMIT},
                    )
                    _recovered = True
        except Exception as _rec_exc:  # noqa: BLE001 — recovery is best-effort
            logger_instance.warning(
                "ReactStrategy: recursion recovery failed (%s); execution_id=%s",
                _rec_exc, execution_id,
            )

        if not _recovered:
            # On context overflow, use LLM-assisted compression then retry once.
            classified = classify_error(exc)
            if classified.should_compress:
                logger_instance.warning(
                    "ReactStrategy: context overflow detected — compressing and retrying",
                    extra={"execution_id": execution_id},
                )
                from langchain_core.messages import BaseMessage as _BM
                from app.core.context_compression import compress as _compress

                existing_msgs = input_state.get("messages", [])
                if isinstance(existing_msgs, list) and all(isinstance(m, _BM) for m in existing_msgs):
                    # Attempt LLM-assisted compression; fall back to hard truncation if llm=None
                    try:
                        compressed = await _compress(existing_msgs, llm=None)
                        input_state = {"messages": compressed}
                        logger_instance.info(
                            "ReactStrategy: compressed %d → %d messages via context_compression",
                            len(existing_msgs), len(compressed),
                            extra={"execution_id": execution_id},
                        )
                    except Exception as _ce:
                        logger_instance.warning(
                            "ReactStrategy: context_compression failed (%s), falling back to _compact",
                            _ce,
                        )
                        input_state = compact_input_state(input_state)
                else:
                    input_state = compact_input_state(input_state)

                if stream_callback is not None:
                    result_state = await execute_agent_stream(
                        agent, input_state, stream_callback, logger_instance, execution_id,
                        run_config,
                    )
                else:
                    result_state = await invoke_agent(agent, input_state, run_config)
            else:
                raise

    parsed = _serialize_agent_result(result_state)

    # Detect a turn cut off by the output-token limit right before the model
    # could emit a tool_use (stop_reason=max_tokens/length, no tool_calls). Left
    # alone, LangGraph treats the truncated reasoning as "done" and we would
    # return a half-thought (e.g. "Now let me search for more context...") as the
    # final answer. Continue the turn once so the agent can finish; if it still
    # truncates or errors, surface the result as incomplete instead of confident.
    if parsed["last_ai_truncated"] and not parsed["last_ai_had_tool_calls"]:
        logger_instance.warning(
            "ReactStrategy: agent turn truncated (stop_reason=%s, output_tokens=%s) with no "
            "tool call; attempting one continuation — execution_id=%s",
            parsed["last_stop_reason"], parsed["fallback_output_tokens"], execution_id,
        )
        try:
            # The truncated turn ends with an assistant message. Bedrock (Converse)
            # rejects a request whose conversation ends on an assistant message
            # ("does not support assistant message prefill. The conversation must
            # end with a user message"), so we must append a user turn to continue.
            # The nudge both satisfies that constraint and tells the model to finish.
            from langchain_core.messages import HumanMessage
            _cont_msgs = list(result_state.get("messages", [])) + [HumanMessage(content=CONTINUE_TRUNCATED_NUDGE)]
            cont_state = await invoke_agent(
                agent,
                {"messages": _cont_msgs},
                {**run_config, "recursion_limit": _CONTINUATION_RECURSION_LIMIT},
            )
            cont_parsed = _serialize_agent_result(cont_state)
            if cont_parsed["last_ai_truncated"] and not cont_parsed["last_ai_had_tool_calls"]:
                # Still truncated after one retry — be honest rather than passing a
                # partial thought off as a finished analysis.
                logger_instance.warning(
                    "ReactStrategy: continuation still truncated (stop_reason=%s) — flagging "
                    "result as incomplete (execution_id=%s)",
                    cont_parsed["last_stop_reason"], execution_id,
                )
                cont_parsed["final_answer"] = (
                    "Investigation was cut off by the model output limit before it could "
                    "complete. Partial findings so far:\n\n"
                    + (cont_parsed["final_answer"] or "")
                ).strip()
                cont_parsed["truncated"] = True
                _stop_reason = "token_budget"
            parsed = cont_parsed
        except Exception as cont_exc:  # noqa: BLE001 — never break on a recovery attempt
            logger_instance.warning(
                "ReactStrategy: continuation attempt failed (%s); flagging result as incomplete "
                "(execution_id=%s)", cont_exc, execution_id,
            )
            parsed["final_answer"] = (
                "Investigation was cut off by the model output limit before it could "
                "complete. Partial findings so far:\n\n" + (parsed["final_answer"] or "")
            ).strip()
            parsed["truncated"] = True
            _stop_reason = "token_budget"

    # Separately: the agent sometimes ends its turn by NARRATING the next action
    # ("Let me search for all methods…", "Now let me check…") in prose WITHOUT
    # emitting the tool call, so LangGraph treats that preamble as the terminal
    # answer. The turn isn't truncated (stop_reason=end_turn) so the block above
    # doesn't catch it. When the final answer is a short action-announcing
    # fragment with no pending tool call, give the agent one more turn to
    # actually act and synthesize; keep it only if it yields a fuller answer.
    elif (
        not parsed.get("truncated")
        and looks_like_midthought(parsed.get("final_answer") or "")
        and len((parsed.get("final_answer") or "").strip()) < 400
    ):
        # Fires whether or not the last message had pending tool calls: when it
        # did, re-invoking executes the pending tool and lets the loop reach a
        # real conclusion; when it didn't, the agent gets a turn to actually act.
        logger_instance.warning(
            "ReactStrategy: agent ended on a mid-thought preamble (%r, had_tool_calls=%s); "
            "continuing one turn so it acts/synthesizes — execution_id=%s",
            (parsed.get("final_answer") or "")[:80], parsed["last_ai_had_tool_calls"], execution_id,
        )
        try:
            # A recursion-limit stop leaves the last AIMessage with UNEXECUTED
            # tool_calls (no matching ToolMessage); LangGraph rejects that history
            # on re-invoke. Trim those trailing dangling-tool-call messages so the
            # model can synthesize from the results it already gathered.
            cont_msgs = _strip_dangling_tool_calls(result_state.get("messages", []))
            cont_state = await invoke_agent(
                agent,
                {"messages": cont_msgs},
                {**run_config, "recursion_limit": _CONTINUATION_RECURSION_LIMIT},
            )
            cont_parsed = _serialize_agent_result(cont_state)
            cont_answer = (cont_parsed.get("final_answer") or "").strip()
            # Accept the continuation only if it advanced past the preamble.
            if cont_answer and cont_answer != (parsed.get("final_answer") or "").strip():
                parsed = cont_parsed
                result_state = cont_state
        except Exception as cont_exc:  # noqa: BLE001 — never break on a recovery attempt
            logger_instance.warning(
                "ReactStrategy: mid-thought continuation failed (%s); keeping original "
                "(execution_id=%s)", cont_exc, execution_id,
            )

    serialized_messages = parsed["messages"]
    tool_calls_summary  = parsed["tool_calls"]
    final_answer        = parsed["final_answer"]

    # Prefer token_cb (fires via on_llm_end, works for all providers and streaming
    # modes); fall back to usage_metadata accumulation if token_cb got nothing.
    total_input_tokens  = token_cb.input_tokens  or parsed["fallback_input_tokens"]
    total_output_tokens = token_cb.output_tokens or parsed["fallback_output_tokens"]

    logger_instance.info(
        "ReactStrategy: agent completed with %d messages, %d tool calls, "
        "input_tokens=%d output_tokens=%d (source=%s)%s",
        len(serialized_messages),
        len(tool_calls_summary),
        total_input_tokens,
        total_output_tokens,
        "callback" if token_cb.input_tokens else "usage_metadata",
        " [truncated]" if parsed.get("truncated") else "",
    )

    # cache_read_tokens / cache_creation_tokens: Bedrock (and Anthropic natively)
    # report these as SEPARATE, ADDITIVE counters — input_tokens is already the
    # non-cached (full-price) portion, it does NOT include cache reads or cache
    # writes. True total input processed = input_tokens + cache_read_tokens +
    # cache_creation_tokens. cache_read bills at ~10% of fresh input; cache
    # creation bills at a premium (~125% for Anthropic models) for the write.
    _cache_read_tokens = getattr(token_cb, "cache_read_tokens", 0) or 0
    _cache_creation_tokens = getattr(token_cb, "cache_creation_tokens", 0) or 0

    # Live token surfacing: push a token_usage_delta SSE event so the chat's
    # token counter updates from the stream (the final sync result carries the
    # same totals). Best-effort — never blocks or fails the run.
    if execution_port is not None and execution_id is not None:
        try:
            await execution_port.publish_token_usage(execution_id, {
                "input_tokens":  total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens":  total_input_tokens + total_output_tokens,
                "cache_read_tokens": _cache_read_tokens,
                "cache_creation_tokens": _cache_creation_tokens,
            })
        except Exception:  # noqa: BLE001
            pass

    # Post-hoc instrumentation (4.1/3.2/3.1 parity with the native engine —
    # see _instrument_langgraph_result's docstring for why this runs once,
    # here, rather than live during each invocation path). Best-effort.
    try:
        await _instrument_langgraph_result(
            parsed, recorder=_recorder, verify_state=_verify_state, execution_id=execution_id,
            skip_first_n=_instrument_skip,
        )
    except Exception as _instr_exc:  # noqa: BLE001 — telemetry must never break a run
        logger_instance.debug("ReactStrategy: LangGraph instrumentation skipped (%s)", _instr_exc)
    await _recorder.flush()

    if parsed.get("truncated") and _stop_reason == "completed":
        _stop_reason = "token_budget"

    result: Dict[str, Any] = {
        "final_answer":   final_answer,
        "messages":       serialized_messages,
        "tool_calls":     tool_calls_summary,
        "input_tokens":   total_input_tokens,
        "output_tokens":  total_output_tokens,
        "total_tokens":   total_input_tokens + total_output_tokens,
        "cache_read_tokens": _cache_read_tokens,
        "cache_creation_tokens": _cache_creation_tokens,
        "stop_reason": _stop_reason,
        "did_forced_synthesis": _did_forced_synthesis,
        "verify_pending": _verify_state.pending,
        "verify_last_passed": _verify_state.last_passed,
    }
    if parsed.get("truncated"):
        result["truncated"] = True

    # ── ID-grounding guard (log-only telemetry) ───────────────────────────
    # Flag identifiers cited in the final answer that no tool produced and the
    # user didn't supply — a likely fabrication. Never blocks or rewrites the
    # answer; surfaced as result["ungrounded_ids"] for the UI / eval to inspect.
    try:
        from app.core.grounding import ungrounded_ids, extract_ids
        allow = extract_ids(user_query)
        flagged = ungrounded_ids(final_answer or "", parsed.get("evidence_ids") or set(), allow)
        if flagged:
            result["ungrounded_ids"] = flagged
            logger_instance.warning(
                "ReactStrategy: %d ID(s) in the final answer are not grounded in any tool "
                "output or the query (possible fabrication): %s (execution_id=%s)",
                len(flagged), flagged[:5], execution_id,
            )
    except Exception:  # noqa: BLE001 — a telemetry guard must never break a run
        pass
    return result


#: Provider stop reasons that mean "output was cut off by the token limit",
#: not "the model finished". Bedrock Converse → "max_tokens"; OpenAI → "length".
_TRUNCATED_STOP_REASONS = {"max_tokens", "length"}


def _stop_reason_of(msg) -> str:
    """Normalize a message's stop/finish reason to a lowercase string.

    Different providers expose it under different keys in ``response_metadata``:
    Bedrock Converse → ``stopReason``, Anthropic → ``stop_reason``,
    OpenAI → ``finish_reason``. Returns "" when none is present.
    """
    meta = getattr(msg, "response_metadata", None) or {}
    for key in ("stopReason", "stop_reason", "finish_reason"):
        val = meta.get(key)
        if val:
            return str(val).lower()
    return ""


def _serialize_agent_result(result_state: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize LangGraph messages into the strategy's result contract.

    Also reports truncation state of the *last* AIMessage so the caller can tell
    a genuine completion from a turn cut off mid-thought by the output limit.

    ``final_answer`` prefers the last *terminal* AIMessage — one with no
    ``tool_calls`` — so an intermediate "reason then act" message is never
    mistaken for the answer. Falls back to the last content seen if every
    AIMessage carried tool calls.
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

    messages = result_state.get("messages", [])
    serialized_messages = []
    tool_calls_summary = []
    tool_calls_by_id: Dict[str, Dict[str, Any]] = {}  # tool_call_id -> summary entry (status backfill)
    final_answer = ""
    last_content = ""          # last AIMessage content regardless of tool_calls
    evidence_ids: set = set()  # ID-like tokens seen in FULL tool outputs (grounding)
    # Fallback token accumulators from AIMessage.usage_metadata
    # (used only when token_cb didn't capture anything via on_llm_end)
    fallback_input_tokens  = 0
    fallback_output_tokens = 0
    last_ai_truncated = False
    last_ai_had_tool_calls = False
    last_stop_reason = ""

    for msg in messages:
        if isinstance(msg, HumanMessage):
            serialized_messages.append({"role": "user", "content": extract_text_content(msg.content)})

        elif isinstance(msg, AIMessage):
            content = extract_text_content(msg.content) if msg.content else ""
            entry: Dict[str, Any] = {"role": "assistant", "content": content}

            has_tool_calls = bool(getattr(msg, "tool_calls", None))
            if has_tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "args": tc.get("args", {}),
                    }
                    for tc in msg.tool_calls
                ]
                for tc in msg.tool_calls:
                    summary_entry = {
                        "tool": tc.get("name"),
                        "args_keys": list((tc.get("args") or {}).keys()),
                    }
                    tool_calls_summary.append(summary_entry)
                    tcid = tc.get("id")
                    if tcid:
                        tool_calls_by_id[tcid] = summary_entry

            # Fallback: AIMessage.usage_metadata (LangChain ≥ 0.1)
            usage = getattr(msg, "usage_metadata", None) or {}
            fallback_input_tokens  += usage.get("input_tokens", 0)
            fallback_output_tokens += usage.get("output_tokens", 0)

            serialized_messages.append(entry)
            if content:
                last_content = content
                # Only a terminal (tool-call-free) message is a real final answer.
                if not has_tool_calls:
                    final_answer = content

            # Track the truncation state of the LAST AIMessage seen.
            last_ai_had_tool_calls = has_tool_calls
            last_stop_reason = _stop_reason_of(msg)
            last_ai_truncated = last_stop_reason in _TRUNCATED_STOP_REASONS

        elif isinstance(msg, ToolMessage):
            _full = str(msg.content)
            # Grounding: harvest IDs from the FULL output, before the 2000-char
            # serialization cap, so the guard sees every identifier a tool produced.
            try:
                from app.core.grounding import extract_ids
                evidence_ids.update(extract_ids(_full))
            except Exception:  # noqa: BLE001 — a telemetry guard must never break a run
                pass

            # Backfill the matching tool_calls_summary entry with real
            # success/failure status (content-based, not name-substring —
            # see app.core.supervisor._score_tool_health).
            _tcid = getattr(msg, "tool_call_id", "")
            _summary_entry = tool_calls_by_id.get(_tcid)
            if _summary_entry is not None:
                try:
                    from app.core.tool_guardrails import classify_tool_failure
                    _failed, _ = classify_tool_failure(_summary_entry.get("tool", ""), _full)
                    _summary_entry["ok"] = not _failed
                    _summary_entry["status"] = "error" if _failed else "ok"
                except Exception:  # noqa: BLE001 — telemetry must never break a run
                    pass

            serialized_messages.append({
                "role": "tool",
                "tool_call_id": _tcid,
                "content": _full[:2000],
            })

        elif isinstance(msg, SystemMessage):
            pass

    # If no terminal message produced content (e.g. the conversation ended on a
    # tool-call message), fall back to the last content seen rather than empty.
    if not final_answer:
        final_answer = last_content

    return {
        "final_answer":           final_answer,
        "messages":               serialized_messages,
        "tool_calls":             tool_calls_summary,
        "fallback_input_tokens":  fallback_input_tokens,
        "fallback_output_tokens": fallback_output_tokens,
        "last_ai_truncated":      last_ai_truncated,
        "last_ai_had_tool_calls": last_ai_had_tool_calls,
        "last_stop_reason":       last_stop_reason,
        "evidence_ids":           evidence_ids,
    }


async def _instrument_langgraph_result(
    parsed: Dict[str, Any], *, recorder: Any, verify_state: Any, execution_id: Optional[str],
    skip_first_n: int = 0,
) -> None:
    """Post-hoc instrumentation over the FINAL serialized LangGraph message
    list — brings the LangGraph engine to parity with the native turn loop's
    per-step trajectory events (4.1), live failure->governance fingerprinting
    (3.2), and edit->verify tracking (3.1).

    Runs ONCE, after every retry/continuation/recovery path in
    execute_agent() has settled, over ``parsed["messages"]`` (already
    produced by ``_serialize_agent_result``) — so it never double-counts
    regardless of which of execute_agent's several invocation paths
    (streaming, non-streaming, recursion-limit recovery, mid-thought
    continuation, context-overflow retry) actually produced the result.

    ``skip_first_n`` excludes the run's INITIAL messages (the user query
    plus any replayed prior-turn chat history) from recording. Without it,
    every follow-up turn of a chat session would re-record the previous
    turns' model/tool events as if they belonged to this execution — and
    re-fingerprint prior turns' tool failures into the failure ledger on
    every turn. The count must be in serialized-list coordinates: the
    caller excludes SystemMessages (dropped by ``_serialize_agent_result``)
    when computing it. LangGraph's message state is append-only, so the
    initial messages are always an intact prefix of the final list.

    Trade-off vs the native engine: no live per-tool latency (this walks the
    final message list, not a live event stream) — an honest, documented
    gap in exchange for covering every LangGraph code path uniformly
    instead of instrumenting each one separately. Best-effort: any failure
    is swallowed by the caller, matching every other telemetry guard here.
    """
    from app.core.tool_guardrails import classify_tool_failure
    from app.harness.verify_tracking import scan_tool_calls

    messages = parsed.get("messages") or []
    # The id->name map is built over the FULL list on purpose: a tool message
    # just past the skip boundary still needs its assistant tool_call resolved.
    id_to_name: Dict[str, str] = {}
    for entry in messages:
        if entry.get("role") == "assistant":
            for tc in entry.get("tool_calls") or []:
                if tc.get("id"):
                    id_to_name[tc["id"]] = tc.get("name") or ""

    step_index = 0
    for entry in messages[max(0, int(skip_first_n)):]:
        role = entry.get("role")
        if role == "assistant":
            step_index += 1
            recorder.record_model_turn(
                step_index, reason="model_call", text=entry.get("content") or "",
                tool_calls=entry.get("tool_calls"),
            )
        elif role == "tool":
            tool_call_id = entry.get("tool_call_id") or ""
            tool_name = id_to_name.get(tool_call_id, "")
            content = entry.get("content") or ""
            is_failed, reason = classify_tool_failure(tool_name, content)
            recorder.record_tool_call(
                step_index, tool_name=tool_name,
                status="error" if is_failed else "ok",
                error_class=reason if is_failed else None,
            )
            if is_failed:
                try:
                    from app.harness.engine.tool_exec import _record_failure_fingerprint
                    await _record_failure_fingerprint(tool_name, content, execution_id)
                except Exception:  # noqa: BLE001 — governance must never break a run
                    pass
            scan_tool_calls(verify_state, [{"name": tool_name}], [content])


def extract_text_content(content) -> str:
    """Convert LangChain message content to a plain string.

    LangChain may return either a plain string or a list of typed content
    blocks (e.g. ``[{'type': 'text', 'text': '…'}]``) for multimodal
    responses.  Using ``str()`` on a list produces Python's repr which
    looks like ``[{'type': 'text', 'text': '…'}]`` — unreadable in the UI.
    This helper extracts the text parts and joins them cleanly.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content) if content else ""

async def invoke_agent(
    agent: Any,
    input_state: Dict[str, Any],
    run_config: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Non-streaming invocation with timeout.

    Defaults to ``settings.agent_invoke_timeout_seconds`` (override via
    AGENT_INVOKE_TIMEOUT_SECONDS) rather than a hardcoded value, so a caller
    that wraps this in its own longer wait_for (e.g. subagent delegation's
    DELEGATION_CHILD_TIMEOUT_SECONDS) isn't silently cut short by a shorter
    inner ceiling.
    """
    _timeout = timeout_seconds if timeout_seconds is not None else settings.agent_invoke_timeout_seconds
    try:
        result_state = await asyncio.wait_for(
            agent.ainvoke(input_state, config=run_config or {}),
            timeout=_timeout,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"ReAct agent timed out after {_timeout:.0f} seconds.")
    return result_state

async def execute_agent_stream(
    agent: Any,
    input_state: Dict[str, Any],
    stream_callback: StreamCallback,
    logger_instance: Any,
    execution_id: Optional[str] = None,
    run_config: Optional[Dict[str, Any]] = None,
    execution_port: Optional[ExecutionPort] = None,
) -> Dict[str, Any]:
    """Streaming invocation using ``agent.astream_events()`` (v2)."""
    from app.core.tool_guardrails import (
        ToolCallGuardrailController,
        toolguard_synthetic_result,
        append_toolguard_guidance,
    )

    current_tool_name: str = ""
    current_tool_args: Dict[str, Any] = {}
    accumulated_state: Dict[str, Any] = {"messages": []}
    msg_map: Dict[str, Any] = {}

    # ── Governance policy (resolved at agent build time) ──────────────────
    # Read the per-task ResolvedPolicy so the loop honours its loop-guardrail
    # config and per-session tool-call ceiling. Absent (e.g. evals / legacy
    # callers) → env-based guardrail config and no tool-call limit, i.e. today's
    # behaviour.
    _resolved_policy = None
    try:
        from app.core import policy as _policy_mod
        _resolved_policy = _policy_mod.get_current()
    except Exception:  # noqa: BLE001 — governance must never break a run
        _resolved_policy = None

    # ── Guardrail controller — one per streaming invocation (= one turn) ─
    # Side-effect-free controller whose decisions (warn / block / halt)
    # are acted on by this runtime code.
    _guardrail_config = getattr(_resolved_policy, "guardrail_config", None)
    guardrail = ToolCallGuardrailController(_guardrail_config)
    _tool_calls_count = 0

    try:
        async for event in agent.astream_events(input_state, config=run_config or {}, version="v2"):
            kind = event.get("event", "")
            data = event.get("data", {})
            name = event.get("name", "")

            if kind == "on_llm_new_token":
                token = event.get("data", {}).get("chunk", "")
                if token:
                    try:
                        await stream_callback.on_llm_token(token)
                    except Exception:
                        pass

            elif kind == "on_chat_model_start":
                pass

            elif kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    # Tool-call chunks stream incrementally: the FIRST chunk carries
                    # the tool name, later chunks carry only arg deltas (empty name).
                    # Only track the name here — the single user-facing "Calling X"
                    # event is emitted once at on_tool_start (with the real name), so
                    # we never surface an empty/"undefined" tool name.
                    for tc in chunk.tool_calls:
                        _nm = tc.get("name") or ""
                        if _nm:
                            current_tool_name = _nm
                content = getattr(chunk, "content", None) if chunk else None
                # ChatBedrockConverse streams content as a LIST of blocks
                # (e.g. [{"type":"text","text":"…"}]) rather than a str, so extract
                # the text from both shapes — otherwise tokens are silently dropped
                # (content can be a list of blocks on Bedrock — extract text).
                _text = ""
                if isinstance(content, str):
                    _text = content
                elif isinstance(content, list):
                    _parts = []
                    for _b in content:
                        if isinstance(_b, str):
                            _parts.append(_b)
                        elif isinstance(_b, dict) and _b.get("type") in (None, "text"):
                            _t = _b.get("text")
                            if isinstance(_t, str):
                                _parts.append(_t)
                    _text = "".join(_parts)
                if _text:
                    try:
                        await stream_callback.on_llm_token(_text)
                    except Exception:
                        pass

            elif kind == "on_chat_model_end":
                output = data.get("output")
                if output and hasattr(output, "id"):
                    msg_map[output.id] = output

            elif kind == "on_tool_start":
                tool_input = data.get("input", {})
                tool_name = name or current_tool_name
                current_tool_name = tool_name
                current_tool_args = tool_input if isinstance(tool_input, dict) else {}

                # ── Policy: per-session tool-call ceiling ─────────────
                _tool_calls_count += 1
                if _resolved_policy is not None:
                    try:
                        from app.core.policy.runtime import evaluate_tool_count
                        _quota = evaluate_tool_count(_resolved_policy, _tool_calls_count)
                        if _quota.blocks:
                            logger_instance.warning(
                                "ReactStrategy: policy tool-call ceiling hit before '%s' — %s",
                                tool_name, _quota.reason,
                                extra={"execution_id": execution_id},
                            )
                            try:
                                await stream_callback.on_error(f"[Policy] {_quota.reason}")
                            except Exception:
                                pass
                    except Exception:  # noqa: BLE001 — governance must never break a run
                        pass

                # ── Guardrail pre-check ───────────────────────────────
                # before_call() is side-effect-free: it only reads state and
                # returns a decision.  We honour warn/block/halt by logging;
                # full blocking requires tool-wrapper interception which is
                # done at the MCPToolWrapper layer when guardrail is wired in.
                _gc_pre = guardrail.before_call(tool_name, current_tool_args)
                if _gc_pre.should_halt:
                    logger_instance.warning(
                        "ReactStrategy: guardrail HALT before tool '%s' — %s",
                        tool_name, _gc_pre.message,
                        extra={"execution_id": execution_id},
                    )
                    try:
                        await stream_callback.on_error(
                            f"[Guardrail HALT] {_gc_pre.message}"
                        )
                    except Exception:
                        pass
                elif not _gc_pre.allows_execution:
                    logger_instance.warning(
                        "ReactStrategy: guardrail BLOCK for tool '%s' (count=%d) — %s",
                        tool_name, _gc_pre.count, _gc_pre.message,
                        extra={"execution_id": execution_id},
                    )
                elif _gc_pre.action == "warn":
                    logger_instance.info(
                        "ReactStrategy: guardrail WARN for tool '%s' (count=%d) — %s",
                        tool_name, _gc_pre.count, _gc_pre.message,
                        extra={"execution_id": execution_id},
                    )

                try:
                    await stream_callback.on_tool_call(tool_name, current_tool_args)
                except Exception:
                    pass

            elif kind == "on_tool_end":
                tool_output = data.get("output", "")
                tool_name = name or current_tool_name
                output_str = str(tool_output)

                # ── Guardrail post-check ──────────────────────────────
                from app.core.tool_guardrails import classify_tool_failure
                _is_failed, _fail_reason = classify_tool_failure(tool_name, output_str)
                _gc_post = guardrail.after_call(
                    tool_name, current_tool_args, output_str, failed=_is_failed,
                )
                if _gc_post.action in ("warn", "block", "halt"):
                    output_str = append_toolguard_guidance(output_str, _gc_post)
                    logger_instance.warning(
                        "ReactStrategy: guardrail %s after tool '%s' (count=%d) — %s",
                        _gc_post.action.upper(), tool_name, _gc_post.count, _gc_post.message,
                        extra={"execution_id": execution_id},
                    )
                    if _gc_post.action == "halt":
                        try:
                            await stream_callback.on_error(
                                f"Tool '{tool_name}' halted the run: {_gc_post.message}"
                            )
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"Tool '{tool_name}' halted the agent run: {_gc_post.message}"
                        )

                try:
                    await stream_callback.on_tool_result(
                        tool_name, output_str[:2000], failed=_is_failed,
                    )
                except Exception:
                    pass

                # ── /steer injection ──────────────────────────────────
                # Drain any engineer notes queued via POST /steer.
                # Injected as HumanMessages so the LLM sees them before
                # its next reasoning step (at tool boundary, as specified).
                if execution_id and execution_port is not None:
                    from langchain_core.messages import HumanMessage as _HM

                    for note in execution_port.drain_steer_notes(execution_id):
                        steer_msg = _HM(content=f"[Engineer Note] {note}")
                        accumulated_state["messages"] = (
                            accumulated_state.get("messages", []) + [steer_msg]
                        )
                        logger_instance.info(
                            "ReactStrategy: injected steer note at tool boundary "
                            "(execution_id=%s)", execution_id,
                        )

            elif kind == "on_chain_error":
                err_str = str(data.get("error", ""))
                try:
                    await stream_callback.on_error(err_str)
                except Exception:
                    pass

    except asyncio.TimeoutError:
        try:
            await stream_callback.on_error("ReAct agent timed out after 300 seconds.")
        except Exception:
            pass
        raise RuntimeError("ReAct agent timed out after 300 seconds.")

    if accumulated_state.get("messages"):
        return accumulated_state

    messages = list(msg_map.values())
    if messages:
        return {"messages": messages}

    return await invoke_agent(agent, input_state)
