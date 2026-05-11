"""Context window compression for long-running agent sessions.

When the LLM raises a context-overflow error (classified as CONTEXT_OVERFLOW
by app.core.error_classifier) the ReAct strategy should call compress() to
reduce the message history before retrying.

Strategy (in priority order):
1. LLM-assisted summarisation — call the LLM to summarise the middle section
   of the conversation, then replace it with a single AIMessage summary.
   Preserves system prompt, summary, and the last HumanMessage.
2. Hard truncation fallback — when no LLM is available or summarisation fails,
   keep the system prompt plus the last _KEEP_FRACTION of messages.

Reference: AgentScope memory compression strategy.
"""
from __future__ import annotations

import logging
from typing import Any, List

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

logger = logging.getLogger(__name__)

# Fraction of non-system messages to retain in hard-truncation mode
_KEEP_FRACTION = 0.5
# Minimum message count before compression does anything useful
_MIN_MESSAGES_TO_COMPRESS = 6


def _serialise(msg: BaseMessage) -> str:
    """Flatten a LangChain message to a readable string for the summariser."""
    content = msg.content
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    role = type(msg).__name__.replace("Message", "").upper()
    return f"[{role}] {content}"


async def compress(
    messages: List[BaseMessage],
    llm: Any | None = None,
    *,
    keep_system: bool = True,
    max_summary_tokens: int = 400,
) -> List[BaseMessage]:
    """Compress *messages* to fit within the LLM context window.

    Args:
        messages: Current message history from the ReAct loop.
        llm: Optional LangChain LLM instance used for summarisation.
            When None, falls back to hard truncation.
        keep_system: Whether to always preserve the first SystemMessage.
        max_summary_tokens: Approximate token budget for the summary
            (passed as a hint in the prompt — not enforced programmatically).

    Returns:
        Compressed message list — guaranteed shorter than input (unless
        the input was already shorter than _MIN_MESSAGES_TO_COMPRESS).
    """
    if not messages or len(messages) < _MIN_MESSAGES_TO_COMPRESS:
        return messages

    system_msgs: List[BaseMessage] = []
    non_system: List[BaseMessage] = []

    for m in messages:
        if keep_system and isinstance(m, SystemMessage):
            system_msgs.append(m)
        else:
            non_system.append(m)

    if len(non_system) < _MIN_MESSAGES_TO_COMPRESS:
        return messages

    if llm is not None:
        try:
            return await _summarise_compress(
                system_msgs, non_system, llm, max_summary_tokens
            )
        except Exception as exc:
            logger.warning(
                "Context compression via LLM failed, falling back to truncation: %s", exc
            )

    return _truncate_compress(system_msgs, non_system)


async def _summarise_compress(
    system_msgs: List[BaseMessage],
    non_system: List[BaseMessage],
    llm: Any,
    max_summary_tokens: int,
) -> List[BaseMessage]:
    """LLM-assisted: summarise the bulk of the conversation into one message."""
    # Keep the last HumanMessage so the agent remembers what it was just asked
    last_human = next(
        (m for m in reversed(non_system) if isinstance(m, HumanMessage)), None
    )
    to_summarise = non_system[:-1] if last_human else non_system

    if not to_summarise:
        return list(system_msgs) + non_system

    conversation_text = "\n".join(_serialise(m) for m in to_summarise)
    prompt = (
        f"Summarise the following agent conversation in at most {max_summary_tokens} tokens. "
        "Preserve: key findings, tool results, decisions made, and any unresolved issues. "
        "Omit verbose tool output. Do not add commentary.\n\n"
        f"{conversation_text}"
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    summary_content = (
        response.content if hasattr(response, "content") else str(response)
    )

    compressed: List[BaseMessage] = list(system_msgs) + [
        AIMessage(content=f"[CONVERSATION SUMMARY — earlier turns compressed]\n{summary_content}")
    ]
    if last_human:
        compressed.append(last_human)

    logger.info(
        "Context compressed via LLM: %d → %d messages",
        len(non_system),
        len(compressed) - len(system_msgs),
    )
    return compressed


def _truncate_compress(
    system_msgs: List[BaseMessage],
    non_system: List[BaseMessage],
) -> List[BaseMessage]:
    """Hard-truncate: keep system messages + last _KEEP_FRACTION of conversation."""
    keep = max(4, int(len(non_system) * _KEEP_FRACTION))
    truncated = non_system[-keep:]

    logger.info(
        "Context hard-truncated: %d → %d messages (no LLM summariser available)",
        len(non_system),
        len(truncated),
    )
    return list(system_msgs) + truncated
