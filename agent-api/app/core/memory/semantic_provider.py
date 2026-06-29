"""Semantic memory provider (Phase 2) — MemoryProvider over SemanticMemoryService.

Registerable into :class:`MemoryManager` as the single external provider. Recall
in the live agent path is repo-scoped via ``context_builder.build_recall_query``
(which knows the active repo); this provider covers the MemoryManager prefetch
path, which only receives a query + session_id, so it recalls the global bank.

Gated by ``settings.semantic_memory_enabled`` — a no-op when disabled.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.config import settings
from app.services.semantic_memory import (
    semantic_memory,
    format_recall_block,
)

logger = logging.getLogger(__name__)


class SemanticMemoryProvider:
    """MemoryProvider backed by the bank-scoped semantic memory store."""

    name = "semantic"

    def system_prompt_block(self) -> str:
        if not settings.semantic_memory_enabled:
            return ""
        return (
            "## Learned memory\n"
            "Findings from past investigations (confirmed root causes, "
            "symptom→fix, operational facts) may be recalled into your context. "
            "Treat them as prior evidence to verify, not as proven fact."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:  # pragma: no cover
        raise ValueError(f"semantic provider exposes no tools (got {tool_name!r})")

    async def prefetch(self, query: str, session_id: str = "") -> str:
        if not settings.semantic_memory_enabled:
            return ""
        try:
            results = await semantic_memory.recall(query, repo=None)
            return format_recall_block(results)
        except Exception as e:  # noqa: BLE001 — recall must never break a run
            logger.warning("semantic provider: prefetch failed (%s)", e)
            return ""

    async def sync_turn(self, user_content: str, assistant_content: str,
                        session_id: str = "") -> None:
        # Investigation-level capture (confirmed root causes etc.) is handled by
        # post-run learning (auto_learn → remember). This hook adds per-turn
        # DURABLE-fact extraction — opt-in and conservative (a couple of short
        # facts per turn), de-duplicated by the store's sha256 index.
        if not settings.memory_fact_extraction_enabled:
            return None
        try:
            from app.core.memory.fact_extractor import extract_and_store
            await extract_and_store(user_content, assistant_content)
        except Exception as e:  # noqa: BLE001 — memory must never break a run
            logger.debug("semantic provider: fact extraction failed (%s)", e)
        return None

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        return None

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        return None

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        # Memory is the first thing dropped on compression (token guardrail):
        # contribute nothing to the preserved set.
        return ""
