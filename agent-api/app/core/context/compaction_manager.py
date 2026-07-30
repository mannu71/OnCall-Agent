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

This class is named ``ContextCompactionManager`` (rather than plain
``MemoryManager``) because it solves ONLY the bounded-context-window problem —
walk-back-to-budget summarization of the running message list. Persistent
knowledge recall/capture lives elsewhere (``context_builder.build_recall_query``
+ ``semantic_memory``), not behind a provider abstraction.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage

from app.core.context.compaction import (
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
        Model id for the LLM summarization call. When None (default), it is
        resolved lazily from the user's configured model (Settings → LLM Configs,
        via the DB) — no model is hardcoded.
    compaction_threshold_fraction:
        Fraction of ``window_size - reserve_tokens`` at which compaction
        triggers (default 0.85 — compact when 85% of the budget is used).
    """

    def __init__(
        self,
        transport: Any,
        *,
        session_id: str = "default",
        window_size: Optional[int] = None,
        model: Optional[str] = None,
        reserve_tokens: int = 16_384,
        keep_recent_tokens: int = 20_000,
        summarization_model: Optional[str] = None,
        compaction_threshold_fraction: float = 0.85,
        microcompact_threshold_fraction: float = 0.70,
    ) -> None:
        self._transport = transport
        self._session_id = session_id
        # Model-aware window: an explicit window_size wins; otherwise derive from
        # *model* (best-effort, offline); otherwise the historical 200K default.
        if window_size is None:
            if model:
                from app.core.llm.model_metadata import window_size_for_model
                window_size = window_size_for_model(model)
            else:
                window_size = 200_000
        self._window_size = window_size
        self._reserve_tokens = reserve_tokens
        self._keep_recent_tokens = keep_recent_tokens
        self._summarization_model = summarization_model
        self._compaction_threshold = int(
            (window_size - reserve_tokens) * compaction_threshold_fraction
        )
        # Cheap deterministic tier (drop stale tool results, no LLM call) tried
        # BEFORE the expensive LLM-summary tier — a microcompact vs full-compact
        # split. Fires earlier (lower fraction) than the hard threshold so the
        # common case never needs a summary call.
        self._microcompact_threshold = int(
            (window_size - reserve_tokens) * microcompact_threshold_fraction
        )

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def estimate_tokens(self, messages: List[BaseMessage]) -> int:
        """Estimate total tokens for a message list (chars/4 heuristic)."""
        return sum(_msg_token_estimate(m) for m in messages)

    @property
    def compaction_threshold_tokens(self) -> int:
        """The hard threshold (default 85% of budget) that triggers the
        expensive LLM-summary tier. Read-only; purely additive so external
        callers (e.g. app.harness.engine.compression's metamemory pre-check)
        can mirror compact_if_needed's own gate without duplicating the
        window/reserve/fraction math."""
        return self._compaction_threshold

    @property
    def keep_recent_tokens(self) -> int:
        return self._keep_recent_tokens

    async def compact_if_needed(
        self,
        messages: List[BaseMessage],
        precomputed_total: Optional[int] = None,
    ) -> List[BaseMessage]:
        """Compact the message list if the token estimate exceeds a threshold.

        Two tiers, cheapest first:
          1. Below the microcompact threshold (default 70%) — no-op.
          2. Above it — try the deterministic microcompact (drop stale
             ToolMessages, no LLM call). If that alone brings the estimate
             back under the hard threshold (default 85%), return it — the
             common case never pays for a summary call.
          3. Still over the hard threshold after microcompact — fall through
             to the existing LLM-summary ``compact()``, run over the
             already-microcompacted (smaller) message list.

        Returns either the original list (under threshold), the
        microcompacted list, or a compacted list starting with a
        SystemMessage summary.
        """
        # ``precomputed_total`` lets a caller that already estimated this exact
        # message list (e.g. the engine's metamemory pre-check) hand the value
        # in, skipping a redundant O(n) walk. Defaults to computing it here, so
        # every other caller is unchanged.
        total = precomputed_total if precomputed_total is not None else self.estimate_tokens(messages)
        if total <= self._microcompact_threshold:
            logger.debug(
                "ContextCompactionManager[%s]: no compaction needed (%d <= %d tokens)",
                self._session_id, total, self._microcompact_threshold,
            )
            return messages

        from app.harness.helpers import compact_input_state
        micro_messages = compact_input_state({"messages": messages})["messages"]
        micro_total = self.estimate_tokens(micro_messages) if micro_messages is not messages else total

        if micro_total <= self._compaction_threshold:
            if len(micro_messages) != len(messages):
                logger.info(
                    "ContextCompactionManager[%s]: microcompact sufficient "
                    "(%d → %d tokens, %d → %d messages) — skipping LLM summary",
                    self._session_id, total, micro_total, len(messages), len(micro_messages),
                )
            return micro_messages

        messages = micro_messages
        total = micro_total

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
            summarization_model=await self._resolve_summarization_model(),
        )

        await self._save_summary(new_summary)

        logger.info(
            "ContextCompactionManager[%s]: compaction complete. "
            "Before=%d msgs, after=%d msgs. summary_id=%s",
            self._session_id, len(messages), len(compacted),
            getattr(new_summary, "summary_id", None),
        )

        return compacted

    async def force_compact(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """Force a summary regardless of the threshold gate.

        Used by the reactive-compact rung: reached only after
        a genuine context-overflow error from the model, so waiting for
        ``compact_if_needed``'s threshold check (which the overflowing
        request already exceeded) would be pointless.

        This used to pre-compute a pairing-safe tail and hand its token count
        to ``compact()`` as ``keep_recent_tokens``, because ``compact()``'s own
        walk could otherwise split a tool_use/tool_result pair and make the
        retry fail the same way. ``compact()`` now lands on that boundary
        itself, for every caller — so this is a plain forced compaction.
        """
        if not messages:
            return messages

        prior_summary = await self._load_summary()
        compacted, new_summary = await compact(
            messages,
            transport=self._transport,
            window_size=self._window_size,
            reserve_tokens=self._reserve_tokens,
            keep_recent_tokens=self._keep_recent_tokens,
            prior_summary=prior_summary,
            summarization_model=await self._resolve_summarization_model(),
        )
        await self._save_summary(new_summary)

        logger.info(
            "ContextCompactionManager[%s]: reactive compaction complete. "
            "Before=%d msgs, after=%d msgs. summary_id=%s",
            self._session_id, len(messages), len(compacted),
            getattr(new_summary, "summary_id", None),
        )
        return compacted

    async def _resolve_summarization_model(self) -> str:
        """Resolve the summarization model from user config (no hardcoded model).

        Uses an explicitly-provided model if given; otherwise resolves the user's
        configured model from the DB (Settings → LLM Configs), with the same
        Bedrock inference-profile remapping the agent uses. Resolved once, cached.
        """
        if self._summarization_model:
            return self._summarization_model
        try:
            from app.core.llm.call_llm import _resolve_llm_config
            cfg = await _resolve_llm_config()
            self._summarization_model = cfg["model"]
        except Exception as exc:  # noqa: BLE001 — never break compaction
            logger.warning(
                "ContextCompactionManager[%s]: could not resolve summarization "
                "model from config (%s)", self._session_id, exc,
            )
            # Last resort: the configured crawler/default model id from settings.
            from app.config import settings as _settings
            self._summarization_model = _settings.crawler_model
        return self._summarization_model

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


# ---------------------------------------------------------------------------
# Per-chat-session compaction (distinct from per-execution compaction above).
# ---------------------------------------------------------------------------
#
# Chat.jsx replays a session's last ~12 messages every turn (see the `history`
# builder), and each can carry a full multi-KB investigation report. Without
# this, a long conversation keeps resending every prior report in full. This
# reuses the exact same ContextCompactionManager (two-tier compaction,
# DB-configured summarization model, Postgres-persisted StructuredSummary) —
# just keyed by the chat session's stable id instead of a per-turn execution
# id, and with a much smaller budget appropriate for chat replay rather than
# the model's full context window.

def _chat_dicts_to_messages(rows: list) -> list:
    """Convert chat_messages rows ({role, content, ...}) to LangChain messages."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    role_map = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    out = []
    for row in rows:
        cls = role_map.get(row.get("role"))
        if cls and row.get("content"):
            out.append(cls(content=row["content"]))
    return out


async def compact_chat_session_if_needed(session_id: str) -> Optional[Dict[str, Any]]:
    """Collapse a chat session's older messages into one summary if it has
    grown past ``settings.chat_session_compaction_tokens``.

    Best-effort — a failure here must never break the chat turn that just
    completed; it only degrades to "no compaction this turn". Returns the
    ``replace_with_summary`` result dict, or ``None`` when no compaction was
    needed/possible.
    """
    try:
        from app.config import settings
        from app.core.transport import get_transport
        from app.infrastructure.persistence import session_repository
        from langchain_core.messages import SystemMessage

        rows = await session_repository.get_messages(session_id)
        if len(rows) < 4:  # too short to be worth summarizing
            return None

        messages = _chat_dicts_to_messages(rows)
        mgr = ContextCompactionManager(
            transport=get_transport(),
            session_id=f"chat:{session_id}",
            window_size=settings.chat_session_compaction_tokens,
            reserve_tokens=0,
            keep_recent_tokens=settings.chat_session_keep_recent_tokens,
        )
        result = await mgr.compact_if_needed(messages)

        # compact_if_needed only prepends a SystemMessage summary when the
        # (expensive) LLM-summary tier actually ran — the no-op and
        # microcompact-only paths return without one. Chat messages are never
        # ToolMessages, so microcompact never has anything to prune here; only
        # the summary tier can shrink a chat session's replay.
        if not result or not isinstance(result[0], SystemMessage):
            return None

        summary_text = result[0].content
        keep_recent = len(result) - 1
        outcome = await session_repository.replace_with_summary(
            session_id, summary=summary_text, keep_recent=keep_recent,
        )
        if outcome:
            logger.info(
                "compact_chat_session_if_needed[%s]: collapsed %d messages, kept %d recent",
                session_id, outcome.get("compacted", 0), keep_recent,
            )
        return outcome
    except Exception as exc:  # noqa: BLE001 — best-effort, never break a chat turn
        logger.warning(
            "compact_chat_session_if_needed[%s]: skipped (%s)", session_id, exc,
        )
        return None
