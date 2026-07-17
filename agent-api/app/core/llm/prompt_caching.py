"""Anthropic prompt caching layer.

"system_and_3" strategy — up to 4 cache breakpoints per request.

Anthropic allows up to 4 cache breakpoints per request.  The strategy is:
  1. System message (1 breakpoint) — stable, rarely changes between turns.
  2. Last 3 non-system messages (3 breakpoints) — capture the stable reasoning
     prefix that persists across the most recent tool-call round-trips.

Total: 4 breakpoints, matching Anthropic's per-request limit.

Cache TTL options:
  - ``"5m"``  → ``{"type": "ephemeral"}``         (5-minute default, always supported)
  - ``"1h"``  → ``{"type": "ephemeral", "ttl": 3600}`` (1-hour, newer API opt-in)

Reference: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

Usage::

    from app.core.llm.prompt_caching import apply_anthropic_cache_control, strip_cache_control

    # Before sending to Anthropic (pass LangChain BaseMessage list):
    cached_messages = apply_anthropic_cache_control(messages, cache_ttl="5m")

    # Before sending to any other provider:
    clean_messages = strip_cache_control(messages)
"""
from __future__ import annotations

import logging
from typing import Any, List, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

# Anthropic requires ≥ 1024 tokens before caching engages.
_MIN_CACHE_CHARS = 1024 * 4   # ≈ 1024 tokens at 4 chars/token
_CHARS_PER_TOKEN = 4

# Maximum breakpoints Anthropic permits per request
_MAX_BREAKPOINTS = 4

CacheTTL = Literal["5m", "1h"]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _make_cache_control(cache_ttl: CacheTTL = "5m") -> dict[str, Any]:
    """Build the cache_control dict for the given TTL.

    Anthropic's 5-minute TTL uses ``{"type": "ephemeral"}``.
    The 1-hour TTL adds a ``"ttl"`` key when supported by the API version.
    """
    if cache_ttl == "1h":
        return {"type": "ephemeral", "ttl": 3600}
    return {"type": "ephemeral"}


def _content_text(msg: BaseMessage) -> str:
    """Extract plain text from a message (handles str and list-of-blocks)."""
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return " ".join(parts)
    return str(content)


def _mark_last_block(msg: BaseMessage, cache_ttl: CacheTTL) -> BaseMessage:
    """Return a copy of *msg* with cache_control on its last content block.

    The breakpoint is placed on the *last* block of each selected message
    so the cache boundary falls at the end of that message.
    """
    content = msg.content
    cc = _make_cache_control(cache_ttl)

    if isinstance(content, str):
        # Wrap the string in a single-block list with cache_control
        new_content: list[dict[str, Any]] = [
            {"type": "text", "text": content, "cache_control": cc}
        ]
    elif isinstance(content, list):
        blocks = list(content)
        if not blocks:
            return msg
        last = blocks[-1]
        if isinstance(last, dict):
            blocks[-1] = {**last, "cache_control": cc}
        else:
            # Non-dict block — wrap it
            blocks[-1] = {"type": "text", "text": str(last), "cache_control": cc}
        new_content = blocks
    else:
        return msg  # Unsupported content shape — leave unchanged

    # Reconstruct the same message type with new content
    try:
        return msg.__class__(content=new_content, **{
            k: getattr(msg, k)
            for k in ("name", "tool_call_id", "tool_calls", "additional_kwargs")
            if hasattr(msg, k) and getattr(msg, k, None) is not None
        })
    except Exception:
        # Fallback: return a shallow copy via dict round-trip
        clone = msg.copy()
        clone.content = new_content  # type: ignore[attr-defined]
        return clone


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def apply_anthropic_cache_control(
    messages: List[BaseMessage],
    *,
    cache_ttl: CacheTTL = "5m",
) -> List[BaseMessage]:
    """Apply the "system_and_3" cache-control strategy to *messages*.

    Breakpoint placement (up to 4 total):
    1. The SystemMessage — always placed first when present and long enough.
    2. The last 3 non-system messages — placed in reverse order until the
       breakpoint budget is exhausted.

    Messages shorter than ``_MIN_CACHE_CHARS`` do not receive a breakpoint
    because Anthropic ignores them anyway.

    Args:
        messages: LangChain ``BaseMessage`` list (may include SystemMessage).
        cache_ttl: ``"5m"`` (default) or ``"1h"`` for one-hour TTL.

    Returns:
        A new list with cache_control annotations applied.
    """
    result: list[BaseMessage] = list(messages)
    budget = _MAX_BREAKPOINTS  # 4 breakpoints total

    # ── Step 1: Annotate the system message ──────────────────────────────────
    for i, msg in enumerate(result):
        if isinstance(msg, SystemMessage):
            text = _content_text(msg)
            if len(text) >= _MIN_CACHE_CHARS:
                result[i] = _mark_last_block(msg, cache_ttl)
                budget -= 1
                logger.debug(
                    "prompt_caching: breakpoint on SystemMessage[%d] "
                    "(%d chars / ~%d tokens)",
                    i, len(text), _estimate_tokens(text),
                )
            break  # Only one system message

    # ── Step 2: Annotate the last 3 non-system messages ──────────────────────
    non_system_indices = [
        i for i, m in enumerate(result) if not isinstance(m, SystemMessage)
    ]
    # Take the last min(3, budget) indices
    candidates = non_system_indices[-(min(3, budget)):]

    for i in candidates:
        if budget <= 0:
            break
        msg = result[i]
        text = _content_text(msg)
        if len(text) < _MIN_CACHE_CHARS:
            continue
        result[i] = _mark_last_block(msg, cache_ttl)
        budget -= 1
        logger.debug(
            "prompt_caching: breakpoint on %s[%d] (%d chars / ~%d tokens)",
            type(msg).__name__, i, len(text), _estimate_tokens(text),
        )

    placed = _MAX_BREAKPOINTS - budget
    logger.info("prompt_caching: placed %d/%d cache breakpoints (ttl=%s)",
                placed, _MAX_BREAKPOINTS, cache_ttl)
    return result


def strip_cache_control(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Remove all cache_control annotations from *messages*.

    Use this before sending to non-Anthropic providers that do not understand
    the cache_control block field.

    Returns a new list — does not mutate the input.
    """
    cleaned: list[BaseMessage] = []
    for msg in messages:
        content = msg.content
        if not isinstance(content, list):
            cleaned.append(msg)
            continue

        stripped: list[Any] = []
        for block in content:
            if isinstance(block, dict) and "cache_control" in block:
                block = {k: v for k, v in block.items() if k != "cache_control"}
            stripped.append(block)

        # Flatten single-text-block lists back to a plain string
        if (
            len(stripped) == 1
            and isinstance(stripped[0], dict)
            and stripped[0].get("type") == "text"
            and len(stripped[0]) == 2  # only "type" + "text"
        ):
            new_content: Any = stripped[0]["text"]
        else:
            new_content = stripped

        try:
            clone = msg.__class__(content=new_content, **{
                k: getattr(msg, k)
                for k in ("name", "tool_call_id", "tool_calls", "additional_kwargs")
                if hasattr(msg, k) and getattr(msg, k, None) is not None
            })
        except Exception:
            clone = msg.copy()
            clone.content = new_content  # type: ignore[attr-defined]
        cleaned.append(clone)

    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# Legacy shim — keep callers using the old apply_cache_control signature working
# ─────────────────────────────────────────────────────────────────────────────

def apply_cache_control(
    messages: list[dict[str, Any]],
    system: str | None = None,
    *,
    cache_ttl: CacheTTL = "5m",
) -> tuple[list[dict[str, Any]], Any]:
    """Legacy interface for raw dict message lists (Anthropic wire format).

    Converts to BaseMessage, applies the system_and_3 strategy, then converts
    back to dicts.  New code should use ``apply_anthropic_cache_control``
    directly with LangChain BaseMessages.

    Returns:
        (annotated_messages, annotated_system) — annotated_system is either
        the original string (short/absent) or a list of content blocks with
        cache_control.
    """
    cc = _make_cache_control(cache_ttl)
    annotated_system: Any = system

    if system and len(system) >= _MIN_CACHE_CHARS:
        annotated_system = [{"type": "text", "text": system, "cache_control": cc}]
        logger.debug(
            "prompt_caching (legacy): breakpoint on system prompt "
            "(%d chars / ~%d tokens)",
            len(system), _estimate_tokens(system),
        )

    budget = _MAX_BREAKPOINTS - (1 if annotated_system is not system else 0)

    # Find the last 3 user/assistant messages large enough to cache
    candidates = [
        i for i, m in enumerate(messages)
        if m.get("role") in {"user", "assistant"}
    ][-(min(3, budget)):]

    for i in candidates:
        msg = messages[i]
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) >= _MIN_CACHE_CHARS:
            messages[i] = {
                **msg,
                "content": [{"type": "text", "text": content, "cache_control": cc}],
            }
            logger.debug(
                "prompt_caching (legacy): breakpoint on messages[%d] role=%s",
                i, msg.get("role"),
            )

    return messages, annotated_system


def is_anthropic_provider(provider: str | None) -> bool:
    """Return True when *provider* is an Anthropic-compatible backend."""
    if not provider:
        return False
    return provider.lower() in {"anthropic", "bedrock", "vertex"}


# ─────────────────────────────────────────────────────────────────────────────
# §2.7 hygiene helpers — stable-head / volatile-tail split, first-user anchor
# ─────────────────────────────────────────────────────────────────────────────

# Separator the assembler uses between the cache-stable head (project,
# role, safety, tool descriptions) and the per-task volatile tail.
# Callers that build their system prompt can insert this delimiter so
# this module can split deterministically; absent the delimiter we
# fall back to "treat the whole prompt as stable head".
SYSTEM_PROMPT_VOLATILE_DELIMITER = "\n<!--VOLATILE-->\n"


def split_system_prompt(system: str | None) -> tuple[str, str]:
    """Split *system* into (stable_head, volatile_tail) on the delimiter.

    The stable head is what we want to cache aggressively — it is the
    same across every turn of a long-running session. The volatile tail
    (task vars, current timestamp, retry context) changes every turn
    and must **not** be inside the cache boundary or the cache hit-rate
    collapses.
    """
    if not system:
        return "", ""
    if SYSTEM_PROMPT_VOLATILE_DELIMITER in system:
        head, tail = system.split(SYSTEM_PROMPT_VOLATILE_DELIMITER, 1)
        return head, tail
    # No delimiter — assume the caller hasn't adopted the split yet;
    # treat the whole prompt as stable so we still get *some* caching.
    return system, ""


def apply_anthropic_cache_control_split(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    cache_ttl: CacheTTL = "5m",
) -> tuple[list[dict[str, Any]], Any]:
    """Hygiene-aware wire-format variant of ``apply_cache_control``.

    Differences vs. the legacy ``apply_cache_control``:

    - System prompt is split into stable head + volatile tail; only the
      head gets a cache breakpoint. The tail is concatenated after it
      with no cache_control marker so prompt-cache hits do not flush
      every time the task-specific suffix changes.
    - One of the three message breakpoints is anchored at the **first**
      ``user`` message (long-lived task anchor) instead of the third-
      to-last message. This keeps the long task brief in cache even as
      the tool-call tail churns.

    Returns the same tuple shape as ``apply_cache_control`` so callers
    that already destructure ``(messages, system)`` can swap drop-in.
    """
    cc = _make_cache_control(cache_ttl)

    head, tail = split_system_prompt(system)
    annotated_system: Any = system  # fallback to original on short prompts

    if head and len(head) >= _MIN_CACHE_CHARS:
        head_block: dict[str, Any] = {
            "type": "text", "text": head, "cache_control": cc,
        }
        blocks: list[dict[str, Any]] = [head_block]
        if tail:
            blocks.append({"type": "text", "text": tail})
        annotated_system = blocks
        logger.debug(
            "prompt_caching.split: head=%d chars (cached), tail=%d chars (volatile)",
            len(head), len(tail),
        )
        budget = _MAX_BREAKPOINTS - 1
    else:
        budget = _MAX_BREAKPOINTS

    # First-user anchor (one slot) + last 2 user/assistant messages (two slots)
    user_indices = [
        i for i, m in enumerate(messages) if m.get("role") == "user"
    ]
    ua_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") in {"user", "assistant"}
    ]

    anchor_targets: list[int] = []
    if user_indices and budget > 0:
        anchor_targets.append(user_indices[0])
        budget -= 1
    if budget > 0:
        # Last min(budget, 2) user/assistant messages, excluding the
        # first-user slot we already pinned above.
        tail_anchors = [
            i for i in ua_indices[-min(budget, 2):]
            if i not in anchor_targets
        ]
        anchor_targets.extend(tail_anchors)

    for i in anchor_targets:
        msg = messages[i]
        content = msg.get("content", "")
        if isinstance(content, str) and len(content) >= _MIN_CACHE_CHARS:
            messages[i] = {
                **msg,
                "content": [{"type": "text", "text": content, "cache_control": cc}],
            }
            logger.debug(
                "prompt_caching.split: breakpoint on messages[%d] role=%s",
                i, msg.get("role"),
            )

    return messages, annotated_system
