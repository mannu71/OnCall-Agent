"""ReAct agent execution — invoke, stream, and result parsing."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.config import settings

from app.core.error_classifier import classify_error
from app.core.retry import with_retry
from app.core.telemetry import agent_span, get_current_trace_id
from app.workflow.strategies.react.helpers import compact_input_state, looks_like_midthought
from app.workflow.strategies.react.hitl import emit_hitl_pause
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

    Returns:
        Dict with:
          - final_answer: str — the last AI response
          - messages: list — all messages in the conversation
          - tool_calls: list — summary of tools invoked
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
    from app.core.telemetry import agent_span, get_current_trace_id

    logger_instance.info("ReactStrategy: invoking agent with query: %.100s", user_query)

    # Conversation memory: replay prior chat turns so follow-up questions keep
    # context (claude-code keeps a messages array and replays it). Compaction
    # below trims this when it grows large.
    _prior_messages = []
    for _m in (conversation_history or []):
        try:
            _role = str(_m.get("role") or "").lower()
            _text = _m.get("content") or ""
            if not _text:
                continue
            if _role in ("assistant", "ai", "agent"):
                _prior_messages.append(AIMessage(content=_text))
            elif _role in ("user", "human"):
                _prior_messages.append(HumanMessage(content=_text))
        except Exception:  # noqa: BLE001
            continue
    input_state = {"messages": _prior_messages + [HumanMessage(content=user_query)]}

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

    # Pass thread_id so the checkpointer can persist state across interrupts.
    run_config: Dict[str, Any] = {}
    if thread_id:
        run_config = {"configurable": {"thread_id": thread_id}}

    # Bound the ReAct loop. LangGraph counts a "step" as one node
    # transition; each ReAct iteration is ~2 steps (agent + tool node).
    # recursion_limit=12 ≈ 6 iterations — above that the agent is usually
    # thrashing and burning tokens for marginal value. Override via the
    # AGENT_RECURSION_LIMIT env var if a workflow legitimately needs more.
    run_config["recursion_limit"] = settings.agent_recursion_limit

    # Attach a token-usage callback so we get exact counts regardless of
    # streaming mode or provider (Bedrock, Anthropic, OpenAI).
    from app.core.streaming.callbacks import TokenUsageCallback
    token_cb = TokenUsageCallback()
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
                snap = await agent.aget_state(run_config)
                msgs = _strip_dangling_tool_calls(
                    list((getattr(snap, "values", None) or {}).get("messages", []))
                )
                if msgs:
                    from langchain_core.messages import HumanMessage
                    msgs = msgs + [HumanMessage(content=(
                        "You have gathered enough information and may NOT call any more tools. "
                        "Give your FINAL answer now, citing concrete file:line evidence. If you "
                        "could not fully determine something, say so plainly."
                    ))]
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
            cont_state = await invoke_agent(
                agent,
                {"messages": result_state.get("messages", [])},
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

    # Live token surfacing: push a token_usage_delta SSE event so the chat's
    # token counter updates from the stream (the final sync result carries the
    # same totals). Best-effort — never blocks or fails the run.
    if execution_port is not None and execution_id is not None:
        try:
            await execution_port.publish_token_usage(execution_id, {
                "input_tokens":  total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens":  total_input_tokens + total_output_tokens,
                "cache_read_tokens": getattr(token_cb, "cache_read_tokens", 0),
            })
        except Exception:  # noqa: BLE001
            pass

    result: Dict[str, Any] = {
        "final_answer":   final_answer,
        "messages":       serialized_messages,
        "tool_calls":     tool_calls_summary,
        "input_tokens":   total_input_tokens,
        "output_tokens":  total_output_tokens,
        "total_tokens":   total_input_tokens + total_output_tokens,
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
                    tool_calls_summary.append(
                        {"tool": tc.get("name"), "args_keys": list((tc.get("args") or {}).keys())}
                    )

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
            serialized_messages.append({
                "role": "tool",
                "tool_call_id": getattr(msg, "tool_call_id", ""),
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
) -> Dict[str, Any]:
    """Non-streaming invocation with timeout."""
    try:
        result_state = await asyncio.wait_for(
            agent.ainvoke(input_state, config=run_config or {}),
            timeout=300.0,
        )
    except asyncio.TimeoutError:
        raise RuntimeError("ReAct agent timed out after 300 seconds.")
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
                if content and isinstance(content, str):
                    try:
                        await stream_callback.on_llm_token(content)
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

                try:
                    await stream_callback.on_tool_result(tool_name, output_str[:2000])
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
