"""Native turn-loop engine — compression pipeline.

Orchestrates WHEN to compact; ``ContextCompactionManager`` (and the shared
``app.core.memory.compaction.compact()``) stays the HOW (LLM summarize,
deterministic prune, ``memory_summaries`` persistence) — reused unchanged so
both engines produce equivalent summaries.

Order per turn: microcompact -> threshold autocompact (both already inside
``ContextCompactionManager.compact_if_needed``, reused as-is) -> reactive
compact (forced, only on a genuine context-overflow error, once per run).
"""
from __future__ import annotations

import logging
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)


def split_preserved_tail(messages: List[Any], keep_recent_tokens: int) -> Tuple[List[Any], List[Any]]:
    """Split ``messages`` into (head-to-summarize, preserved-tail).

    Walks backward accumulating token estimates until ``keep_recent_tokens``
    is reached (same walk ``compaction.compact()`` does internally), then
    extends the boundary further back so the tail never starts mid-pair: not
    on an orphaned ``ToolMessage``, and not on an ``AIMessage`` whose
    ``tool_calls`` aren't all resolved within the tail. A tail that split a
    tool_use/tool_result pair would make the very retry this exists for fail
    Bedrock's INVALID_CHAT_HISTORY check.
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from app.core.memory.compaction import _msg_token_estimate

    if not messages:
        return [], []

    accumulated = 0
    tail_start_idx = 0
    for i in range(len(messages) - 1, -1, -1):
        accumulated += _msg_token_estimate(messages[i])
        if accumulated >= keep_recent_tokens:
            tail_start_idx = i
            break

    while tail_start_idx > 0:
        candidate = messages[tail_start_idx]
        if isinstance(candidate, ToolMessage):
            tail_start_idx -= 1
            continue
        if isinstance(candidate, AIMessage) and getattr(candidate, "tool_calls", None):
            ids_needed = {
                tc.get("id") for tc in candidate.tool_calls if isinstance(tc, dict)
            }
            ids_in_tail = {
                getattr(m, "tool_call_id", None) for m in messages[tail_start_idx:]
                if isinstance(m, ToolMessage)
            }
            if not ids_needed.issubset(ids_in_tail):
                tail_start_idx -= 1
                continue
        break

    return messages[:tail_start_idx], messages[tail_start_idx:]


class CompressionPipeline:
    """Owns the compaction call sites for one native-engine run."""

    def __init__(self, manager: Any) -> None:
        self._mgr = manager

    async def maybe_compact(self, messages: List[Any]) -> List[Any]:
        """Proactive compaction before a model call — microcompact then
        threshold autocompact, both via the existing two-tier ladder."""
        return await self._mgr.compact_if_needed(messages)

    async def reactive_compact(self, messages: List[Any]) -> List[Any]:
        """Forced summary regardless of threshold — only on a genuine
        context-overflow error from the model, once per run (the caller is
        responsible for the once-per-run guard)."""
        return await self._mgr.force_compact(messages)


__all__ = ["CompressionPipeline", "split_preserved_tail"]
