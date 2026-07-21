"""Agent engine — compression pipeline.

Orchestrates WHEN to compact; ``ContextCompactionManager`` (and the shared
``app.core.context.compaction.compact()``) stays the HOW (LLM summarize,
deterministic prune, ``memory_summaries`` persistence) — reused unchanged.

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
    from app.core.context.compaction import _msg_token_estimate

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


async def maybe_metamemory_summary(
    messages: List[Any],
    *,
    vfs_session_id: Any,
    compaction_threshold_tokens: int,
    keep_recent_tokens: int,
    estimate_tokens: Any,
    precomputed_total: Any = None,
) -> Any:
    """Shared metamemory-supersedes-compaction pre-check (used by BOTH
    engines — the native CompressionPipeline below and the LangGraph
    pre_model_hook in app.harness.react_agent).

    When the agent maintains a non-empty /context_summary.txt
    (app.harness.metamemory) and the message list has grown past
    ``compaction_threshold_tokens``, the metamemory summary supersedes the
    expensive LLM-summary tier — no extra LLM call, no re-summarizing
    history the agent already distilled itself. Returns None (fall through
    to the caller's normal compaction ladder) when metamemory is
    empty/inactive or the threshold hasn't been reached yet. Best-effort —
    any failure returns None; compaction must never break a run.
    """
    if not vfs_session_id:
        return None
    try:
        total = precomputed_total if precomputed_total is not None else estimate_tokens(messages)
        if total <= compaction_threshold_tokens:
            return None  # not over threshold yet — let the normal ladder decide

        from app.harness import metamemory
        block = await metamemory.read_context_block(vfs_session_id)
        if not block:
            return None  # metamemory inactive or empty — fall through

        from langchain_core.messages import SystemMessage
        _, tail = split_preserved_tail(messages, keep_recent_tokens)
        summary_msg = SystemMessage(
            content=f"[Prior conversation summary — agent-maintained]\n{block}"
        )
        logger.info(
            "compression: metamemory summary supersedes LLM re-summarization "
            "(%d msgs before -> %d after)", len(messages), len(tail) + 1,
        )
        return [summary_msg] + tail
    except Exception as exc:  # noqa: BLE001 — compaction must never break a run
        logger.debug("compression: metamemory pre-check skipped (%s)", exc)
        return None


class CompressionPipeline:
    """Owns the compaction call sites for one agent run.

    Built once per run (or per agent build, for the LangGraph engine's
    ``pre_model_hook``) and reused across every model call — it holds no
    per-call state, and sharing one instance is what lets a single token
    estimate serve both gates in :meth:`maybe_compact`.
    """

    def __init__(self, manager: Any, *, vfs_session_id: Any = None) -> None:
        self._mgr = manager
        self._vfs_session_id = vfs_session_id

    async def maybe_compact(self, messages: List[Any]) -> List[Any]:
        """Proactive compaction before a model call — microcompact then
        threshold autocompact, both via the existing two-tier ladder.

        See :func:`maybe_metamemory_summary` for the pre-check this runs
        first. The manager's threshold/keep-recent properties are only
        touched when a VFS session is actually bound, so a minimal fake
        manager (no metamemory-specific attributes) keeps working exactly
        as before this pre-check existed.
        """
        if self._vfs_session_id:
            # Estimate once and share it with both the metamemory pre-check and
            # the manager's own threshold gate — they walk the identical list.
            total = self._mgr.estimate_tokens(messages)
            metamemory_messages = await maybe_metamemory_summary(
                messages,
                vfs_session_id=self._vfs_session_id,
                compaction_threshold_tokens=self._mgr.compaction_threshold_tokens,
                keep_recent_tokens=self._mgr.keep_recent_tokens,
                estimate_tokens=self._mgr.estimate_tokens,
                precomputed_total=total,
            )
            if metamemory_messages is not None:
                return metamemory_messages
            return await self._mgr.compact_if_needed(messages, precomputed_total=total)
        return await self._mgr.compact_if_needed(messages)

    def estimate_tokens(self, messages: List[Any]) -> int:
        """The manager's token estimate for a message list. Exposed so callers
        (e.g. the token-estimate calibration feed) don't reach into the
        manager through a private attribute."""
        return self._mgr.estimate_tokens(messages)

    async def reactive_compact(self, messages: List[Any]) -> List[Any]:
        """Forced summary regardless of threshold — only on a genuine
        context-overflow error from the model, once per run (the caller is
        responsible for the once-per-run guard)."""
        return await self._mgr.force_compact(messages)


__all__ = ["CompressionPipeline", "split_preserved_tail", "maybe_metamemory_summary"]
