"""Anthropic prompt caching layer.

Applies cache_control breakpoints to system prompts and large user messages
to take advantage of Anthropic's prompt cache (up to 90 % cost reduction
on cached tokens, 5-minute TTL per cache entry).

Reference: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

Usage:
    from app.core.prompt_caching import apply_cache_control, strip_cache_control

    # Before sending to Anthropic:
    messages, system = apply_cache_control(messages, system=system_prompt)

    # Before sending to any other provider:
    messages = strip_cache_control(messages)

This module is intentionally provider-agnostic in its interface: callers
check the provider name themselves and route through the right helper.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Anthropic requires ≥ 1024 tokens for caching to engage.
# We estimate with a conservative chars-per-token ratio.
_MIN_CACHE_CHARS = 1024 * 4   # ≈ 1024 tokens at 4 chars/token
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def apply_cache_control(
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """Apply Anthropic cache_control breakpoints to *messages* and *system*.

    Modifies *messages* in place (safe to call repeatedly — idempotent for
    already-annotated messages).

    Cache breakpoints are placed on:
    1. The system prompt when it is long enough to meet Anthropic's minimum.
    2. The most recent user message with substantial content (captures the
       stable tool-schema / knowledge-base prefix that rarely changes).

    Args:
        messages: List of message dicts in Anthropic / LangChain wire format.
        system: Optional system prompt string.

    Returns:
        (messages, annotated_system) — annotated_system is either the original
        string (short prompt) or a list of content blocks with cache_control.
    """
    annotated_system: Any = system

    # Annotate system prompt if long enough
    if system and len(system) >= _MIN_CACHE_CHARS:
        annotated_system = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        logger.debug(
            "Applied cache_control to system prompt (%d chars / ~%d tokens)",
            len(system),
            _estimate_tokens(system),
        )

    # Find and annotate the last substantial user message
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "user":
            continue

        content = msg.get("content", "")
        if not isinstance(content, str):
            break  # Already a list of blocks — skip

        if len(content) < _MIN_CACHE_CHARS:
            break  # Not large enough to benefit

        messages[i] = {
            **msg,
            "content": [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        logger.debug(
            "Applied cache_control to user message [%d] (%d chars / ~%d tokens)",
            i,
            len(content),
            _estimate_tokens(content),
        )
        break

    return messages, annotated_system


def strip_cache_control(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove all cache_control annotations from *messages*.

    Use this before sending messages to non-Anthropic providers that do not
    understand the cache_control block field.

    Returns a new list — does not mutate the input.
    """
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(msg)
            continue

        stripped_blocks = []
        for block in content:
            if isinstance(block, dict):
                block = {k: v for k, v in block.items() if k != "cache_control"}
            stripped_blocks.append(block)

        # Flatten single text-block lists back to a plain string
        if (
            len(stripped_blocks) == 1
            and isinstance(stripped_blocks[0], dict)
            and stripped_blocks[0].get("type") == "text"
        ):
            cleaned.append({**msg, "content": stripped_blocks[0]["text"]})
        else:
            cleaned.append({**msg, "content": stripped_blocks})

    return cleaned


def is_anthropic_provider(provider: str | None) -> bool:
    """Return True when *provider* is an Anthropic-compatible backend."""
    if not provider:
        return False
    return provider.lower() in {"anthropic", "bedrock", "vertex"}
