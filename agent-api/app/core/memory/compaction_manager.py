"""
ContextCompactionManager — session-scoped context-window compaction for the agent loop.

Responsibilities
----------------
1. Track accumulated messages per session.
2. Call ``compact()`` when the estimated token count approaches the context
   window budget.
3. Persist the StructuredSummary to the Postgres ``memory_summaries`` table
   so it survives container restarts (with an in-process LRU fallback when
   the database is unavailable — typical in unit tests).

Note on naming
--------------
This class is named ``ContextCompactionManager`` to avoid collision with
``app.core.memory.manager.MemoryManager``, which is the (unrelated)
MemoryProvider orchestrator for built-in/external memory backends. The
two managers solve different problems:

  * MemoryManager (manager.py)            — persistent agent knowledge providers
  * ContextCompactionManager (this file)  — bounded context window via compaction
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage

from app.core.memory.compaction import (
    StructuredSummary,
    _msg_token_estimate,
    compact,
)

logger = logging.getLogger(__name__)

# In-process fallback store — used when Postgres is unreachable (CI / tests)
# so the compaction code path doesn't crash. Production reads/writes go to
# the ``memory_summaries`` table created in Task #2 DDL.
_summary_store: Dict[str, StructuredSummary] = {}


async def _db_load_summary(session_id: str) -> Optional[StructuredSummary]:
    """Read the ``memory_summaries`` row for *session_id* from Postgres."""
    try:
        from sqlalchemy import text
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT summary FROM memory_summaries "
                    "WHERE session_id = :sid LIMIT 1"
                ),
                {"sid": session_id},
            )
            row = result.first()
            if row is None or row[0] is None:
                return None
            payload = row[0]
            # Postgres JSONB comes back as a dict already; tolerate string too.
            if isinstance(payload, str):
                import json as _json
                payload = _json.loads(payload)
            return StructuredSummary.from_dict(payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "ContextCompactionManager: _db_load_summary failed (%s) — "
            "falling back to in-process store", exc,
        )
        return None


async def _db_save_summary(session_id: str, summary: StructuredSummary) -> bool:
    """Upsert the ``memory_summaries`` row for *session_id*.

    Returns True if the row was written to Postgres, False if the DB
    was unavailable (caller may want to keep the in-process copy in sync).
    """
    try:
        import json as _json
        from sqlalchemy import text
        from app.core.database import AsyncSessionLocal

        payload = _json.dumps(summary.to_dict())
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO memory_summaries (session_id, summary, updated_at)
                    VALUES (:sid, CAST(:payload AS JSONB), NOW())
                    ON CONFLICT (session_id) DO UPDATE SET
                        summary    = EXCLUDED.summary,
                        updated_at = NOW()
                """),
                {"sid": session_id, "payload": payload},
            )
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ContextCompactionManager: _db_save_summary failed (%s) — "
            "summary survives in-process only, lost on restart", exc,
        )
        return False


class ContextCompactionManager:
    """Session-scoped context-window manager for the agent loop.

    Parameters
    ----------
    transport:
        Any transport object with an async ``complete()`` method.  Used for
        the LLM summarization call inside ``compact()``.  Inject a
        FakeTransport in tests.
    session_id:
        Unique identifier for the current session.  Summaries are bucketed
        by this key in the in-process store.
    window_size:
        Provider context window in tokens (default 200_000).
    reserve_tokens:
        Tokens reserved for the model response (default 16_384).
    keep_recent_tokens:
        Minimum recent history to keep outside the summary (default 20_000).
    summarization_model:
        Model id for the LLM summarization call.
    compaction_threshold_fraction:
        Fraction of ``window_size - reserve_tokens`` at which compaction
        triggers (default 0.85 — compact when 85% of the budget is used).
    """

    def __init__(
        self,
        transport: Any,
        *,
        session_id: str = "default",
        window_size: int = 200_000,
        reserve_tokens: int = 16_384,
        keep_recent_tokens: int = 20_000,
        summarization_model: str = "anthropic/claude-3-5-haiku-latest",
        compaction_threshold_fraction: float = 0.85,
    ) -> None:
        self._transport = transport
        self._session_id = session_id
        self._window_size = window_size
        self._reserve_tokens = reserve_tokens
        self._keep_recent_tokens = keep_recent_tokens
        self._summarization_model = summarization_model
        self._compaction_threshold = int(
            (window_size - reserve_tokens) * compaction_threshold_fraction
        )

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def estimate_tokens(self, messages: List[BaseMessage]) -> int:
        """Estimate total tokens for a message list (chars/4 heuristic)."""
        return sum(_msg_token_estimate(m) for m in messages)

    async def compact_if_needed(
        self,
        messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        """Compact the message list if the token estimate exceeds the threshold.

        Returns either the original list (under threshold) or a compacted
        list starting with a SystemMessage summary.
        """
        total = self.estimate_tokens(messages)
        if total <= self._compaction_threshold:
            logger.debug(
                "ContextCompactionManager[%s]: no compaction needed (%d <= %d tokens)",
                self._session_id, total, self._compaction_threshold,
            )
            return messages

        logger.info(
            "ContextCompactionManager[%s]: compacting %d messages (%d tokens > threshold %d)",
            self._session_id, len(messages), total, self._compaction_threshold,
        )

        prior_summary = await self._load_summary()

        compacted, new_summary = await compact(
            messages,
            transport=self._transport,
            window_size=self._window_size,
            reserve_tokens=self._reserve_tokens,
            keep_recent_tokens=self._keep_recent_tokens,
            prior_summary=prior_summary,
            summarization_model=self._summarization_model,
        )

        await self._save_summary(new_summary)

        logger.info(
            "ContextCompactionManager[%s]: compaction complete. "
            "Before=%d msgs, after=%d msgs. summary_id=%s",
            self._session_id, len(messages), len(compacted),
            getattr(new_summary, "summary_id", None),
        )

        return compacted

    def get_current_summary(self) -> Optional[StructuredSummary]:
        """Return the persisted summary for this session, if any."""
        return _summary_store.get(self._session_id)

    def clear_session(self) -> None:
        """Remove the persisted summary for this session (e.g. on session end)."""
        _summary_store.pop(self._session_id, None)

    # ──────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────

    async def _load_summary(self) -> Optional[StructuredSummary]:
        """Read this session's summary, preferring Postgres over the in-process cache.

        On a Postgres miss we still consult the in-process fallback so a
        summary that was written during a DB outage isn't lost mid-session.
        """
        db_summary = await _db_load_summary(self._session_id)
        if db_summary is not None:
            _summary_store[self._session_id] = db_summary   # keep cache warm
            return db_summary
        return _summary_store.get(self._session_id)

    async def _save_summary(self, summary: StructuredSummary) -> None:
        """Persist this session's summary to Postgres + the in-process cache.

        The in-process write happens unconditionally so the same instance
        can read it back even if the DB write fails (typical in dev /
        unit-test environments).
        """
        _summary_store[self._session_id] = summary
        await _db_save_summary(self._session_id, summary)
