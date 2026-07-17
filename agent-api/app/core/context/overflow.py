"""Context window compression for long-running agent sessions.

When the LLM raises a context-overflow error the ReAct strategy calls
``compress()`` to reduce the message history before retrying.

Strategy (multi-phase):
1. **Tool-pair integrity** — remove orphaned ToolMessages / insert stub
   results for orphaned tool-calls so the sequence stays API-valid.
2. **Head / tail protection** — always keep the first N and last N messages.
3. **LLM-assisted summarisation** — summarise the middle section using a
   structured template (Active Task / Completed Actions / Blocked Issues /
   Pending Questions).  Uses the cheapest available model.
4. **Anti-thrashing guard** — skip a compression pass if the previous one
   saved less than 10 % of tokens (prevents infinite compress loops).
5. **Hard-truncation fallback** — when no LLM is available or summarisation
   fails, keep the system prompt plus the last ``_KEEP_FRACTION`` of messages.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────────────
_KEEP_FRACTION        = 0.5    # Hard-truncation: keep this fraction of tail msgs
_MIN_MESSAGES         = 6      # Don't compress if history is this short
_PROTECT_HEAD         = 1      # Always keep first N messages (system / initial human)
_PROTECT_TAIL         = 4      # Always keep last N messages (recent reasoning)
_ANTI_THRASH_THRESHOLD = 0.10  # Skip re-compression if previous pass saved < 10 %
_CHARS_PER_TOKEN      = 4      # Rough token estimator

# Structured summary preamble — explicitly marks as reference
_SUMMARY_PREAMBLE = (
    "[CONVERSATION SUMMARY — earlier turns compressed]\n"
    "NOTE: The following is a reference summary, NOT new user instructions.\n\n"
)

# Structured summary template
_SUMMARY_PROMPT_TEMPLATE = """\
You are compressing an AI agent conversation history.
Produce a structured summary using EXACTLY these four sections.
Be concise. Do not add commentary. Do not reproduce tool outputs verbatim.

## Active Task
<one sentence: what the agent is currently trying to accomplish>

## Completed Actions
<bullet list of tools called and key results found>

## Blocked Issues
<bullet list of anything that failed, timed out, or is unresolved>

## Pending Questions
<bullet list of open questions the agent still needs to answer>

---
CONVERSATION TO SUMMARISE:
{conversation}
"""


def _estimate_tokens(messages: List[BaseMessage]) -> int:
    total = 0
    for m in messages:
        content = m.content
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        total += len(str(content)) // _CHARS_PER_TOKEN
    return max(1, total)


def _serialise(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    role = type(msg).__name__.replace("Message", "").upper()
    return f"[{role}] {content}"


# ─────────────────────────────────────────────────────────────────────────────
# Tool-pair integrity (phase 1)
# ─────────────────────────────────────────────────────────────────────────────

def _fix_tool_pairs(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Ensure every tool-call has a matching ToolMessage and vice-versa.

    Removes orphaned ToolMessages (no matching call) and inserts stub
    ToolMessages for AIMessage tool_calls that have no result, so the
    sequence remains valid for the Anthropic / OpenAI APIs.
    """
    # Collect all tool_call ids present in AIMessages
    call_ids: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if tc.get("id"):
                    call_ids.add(tc["id"])

    result: List[BaseMessage] = []
    seen_results: set[str] = set()

    for m in messages:
        if isinstance(m, ToolMessage):
            tc_id = getattr(m, "tool_call_id", None)
            if tc_id and tc_id not in call_ids:
                # Orphaned ToolMessage — drop it
                logger.debug("context_compression: dropping orphaned ToolMessage id=%s", tc_id)
                continue
            if tc_id:
                seen_results.add(tc_id)
        result.append(m)

    # Insert stub results for un-answered tool calls
    fixed: List[BaseMessage] = []
    for m in result:
        fixed.append(m)
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                tc_id = tc.get("id")
                if tc_id and tc_id not in seen_results:
                    stub = ToolMessage(
                        content="[Result unavailable — context was compressed]",
                        tool_call_id=tc_id,
                    )
                    fixed.append(stub)
                    logger.debug(
                        "context_compression: inserted stub ToolMessage for orphaned call id=%s",
                        tc_id,
                    )

    return fixed


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def compress(
    messages: List[BaseMessage],
    llm: Any | None = None,
    *,
    keep_system: bool = True,
    max_summary_tokens: int = 400,
    previous_token_count: Optional[int] = None,
) -> List[BaseMessage]:
    """Compress *messages* to fit within the LLM context window.

    Args:
        messages:             Current message history from the ReAct loop.
        llm:                  Optional LangChain LLM used for summarisation.
                              When None, falls back to hard truncation.
        keep_system:          Always preserve the first SystemMessage.
        max_summary_tokens:   Token budget hint for the structured summary.
        previous_token_count: Token count from the last compression pass.
                              When provided, enables the anti-thrashing guard.

    Returns:
        Compressed message list (may equal input when anti-thrashing fires).
    """
    if not messages or len(messages) < _MIN_MESSAGES:
        return messages

    # ── Phase 0: Tool-pair integrity ─────────────────────────────────────────
    messages = _fix_tool_pairs(messages)

    # ── Phase 1: Split head / system / tail ──────────────────────────────────
    system_msgs: List[BaseMessage] = []
    non_system:  List[BaseMessage] = []

    for m in messages:
        if keep_system and isinstance(m, SystemMessage):
            system_msgs.append(m)
        else:
            non_system.append(m)

    if len(non_system) < _MIN_MESSAGES:
        return messages

    head = non_system[:_PROTECT_HEAD]
    tail = non_system[-_PROTECT_TAIL:]
    middle = non_system[_PROTECT_HEAD : len(non_system) - _PROTECT_TAIL]

    if not middle:
        return messages  # Nothing in the middle to compress

    # ── Phase 2: Anti-thrashing guard ────────────────────────────────────────
    if previous_token_count is not None and previous_token_count > 0:
        current_tokens = _estimate_tokens(messages)
        saved_fraction = (previous_token_count - current_tokens) / previous_token_count
        if saved_fraction < _ANTI_THRASH_THRESHOLD:
            logger.info(
                "context_compression: anti-thrash — last pass saved only %.1f%% "
                "(< %.0f%% threshold), skipping",
                saved_fraction * 100,
                _ANTI_THRASH_THRESHOLD * 100,
            )
            return messages

    # ── Phase 3: LLM-assisted summarisation ──────────────────────────────────
    if llm is not None:
        try:
            return await _summarise_compress(
                system_msgs, head, middle, tail, llm, max_summary_tokens
            )
        except Exception as exc:
            logger.warning(
                "context_compression: LLM summarisation failed (%s), "
                "falling back to hard truncation",
                exc,
            )

    # ── Phase 4: Hard-truncation fallback ────────────────────────────────────
    return _truncate_compress(system_msgs, non_system)


async def _summarise_compress(
    system_msgs:      List[BaseMessage],
    head:             List[BaseMessage],
    middle:           List[BaseMessage],
    tail:             List[BaseMessage],
    llm:              Any,
    max_summary_tokens: int,
) -> List[BaseMessage]:
    """Structured LLM summarisation of the middle message window."""
    conversation_text = "\n".join(_serialise(m) for m in middle)
    prompt_text = _SUMMARY_PROMPT_TEMPLATE.format(conversation=conversation_text)

    response = await llm.ainvoke([HumanMessage(content=prompt_text)])
    summary_content = (
        response.content if hasattr(response, "content") else str(response)
    )

    summary_msg = AIMessage(
        content=f"{_SUMMARY_PREAMBLE}{summary_content}"
    )

    compressed = list(system_msgs) + head + [summary_msg] + tail

    logger.info(
        "context_compression: LLM-summarised %d middle messages → 1 summary "
        "(total: %d → %d messages)",
        len(middle),
        len(system_msgs) + len(head) + len(middle) + len(tail),
        len(compressed),
    )
    return compressed


def _truncate_compress(
    system_msgs: List[BaseMessage],
    non_system:  List[BaseMessage],
) -> List[BaseMessage]:
    """Hard-truncate: keep system messages + last ``_KEEP_FRACTION`` of msgs."""
    keep = max(_PROTECT_TAIL, int(len(non_system) * _KEEP_FRACTION))
    truncated = non_system[-keep:]
    logger.info(
        "context_compression: hard-truncated %d → %d messages",
        len(non_system),
        len(truncated),
    )
    return list(system_msgs) + truncated
