"""Chat session repository — persistent, resumable conversations (migration 022).

Two-table split: ``chat_sessions`` holds metadata (cheap to list), ``chat_messages``
holds the turns (hydrated only when a session is opened). We keep NO process-local
session cache — this API runs under multiple workers, so a per-process dict would be
incoherent; the DB is the single source of truth.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update

from app.core.database import AsyncSessionLocal
from app.core.observability.cache_metrics import cache_hit_rate
from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import ChatMessageModel, ChatSessionModel

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionRepository(BaseAsyncRepository):
    """Data access for chat sessions and their messages."""

    def __init__(self) -> None:
        logger.info("SessionRepository initialized")

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        title: str = "New chat",
        workflow_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a new (empty) session and return it."""
        now = _utcnow()
        sid = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            row = ChatSessionModel(
                id=sid,
                title=(title or "New chat")[:255],
                workflow_name=workflow_name,
                model=model,
                archived=False,
                is_important=False,
                message_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._session_to_dict(row)

    async def list_sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List session metadata (no messages), most-recently-active first.

        Only non-empty, non-archived sessions by default, capped — so the list
        stays cheap regardless of table size.
        """
        stmt = select(ChatSessionModel)
        if not include_archived:
            stmt = stmt.where(ChatSessionModel.archived.is_(False))
        stmt = (
            stmt.where(ChatSessionModel.message_count > 0)
            .order_by(
                ChatSessionModel.last_message_at.desc().nullslast(),
                ChatSessionModel.created_at.desc(),
            )
            .limit(limit)
        )
        rows = await self._all(stmt)
        return [self._session_to_dict(r) for r in rows]

    async def get_session(
        self, session_id: str, *, include_messages: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Return one session's metadata (+ hydrated messages), or None.

        ``include_messages=False`` skips the second ``chat_messages`` query — for
        callers that only mutate/return metadata (e.g. the PATCH rename/pin
        endpoint), where hydrating the full turn history is pure waste.
        """
        row = await self._one(
            select(ChatSessionModel).where(ChatSessionModel.id == session_id)
        )
        if row is None:
            return None
        data = self._session_to_dict(row)
        data["messages"] = await self.get_messages(session_id) if include_messages else []
        return data

    async def rename(self, session_id: str, title: str) -> bool:
        return await self._update_fields(session_id, title=title[:255])

    async def set_archived(self, session_id: str, archived: bool) -> bool:
        return await self._update_fields(session_id, archived=archived)

    async def set_important(self, session_id: str, important: bool) -> bool:
        return await self._update_fields(session_id, is_important=important)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session; messages cascade via the FK."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ChatSessionModel).where(ChatSessionModel.id == session_id)
            )
            await session.commit()
            deleted = result.rowcount > 0

        # Stored tool results live in the LangGraph KV store, not this schema, so
        # no FK cascades them. They are written WITHOUT a TTL — their lifetime is
        # the session — which makes this purge the thing that keeps that promise
        # true. Skipping it would leave raw production rows at rest indefinitely.
        if deleted:
            try:
                from app.harness.tool_result_store import purge_session
                await purge_session(session_id)
            except Exception as exc:  # noqa: BLE001 — never fail a delete over cleanup
                logger.warning(
                    "session_repository: stored tool results for session %s were "
                    "not purged (%s) — they will outlive the session",
                    session_id, exc,
                )
        return deleted

    async def cleanup_empty(
        self,
        *,
        min_age_hours: int = 24,
        auto_archive_days: int = 30,
    ) -> Dict[str, int]:
        """Delete stale empty sessions and auto-archive idle ones.

        Skips ``is_important`` sessions (the pin protects from cleanup).
        """
        now = _utcnow()
        empty_cutoff = now - timedelta(hours=min_age_hours)
        idle_cutoff = now - timedelta(days=auto_archive_days)
        async with AsyncSessionLocal() as session:
            del_result = await session.execute(
                delete(ChatSessionModel).where(
                    ChatSessionModel.is_important.is_(False),
                    ChatSessionModel.message_count == 0,
                    ChatSessionModel.created_at < empty_cutoff,
                )
            )
            arch_result = await session.execute(
                update(ChatSessionModel)
                .where(
                    ChatSessionModel.is_important.is_(False),
                    ChatSessionModel.archived.is_(False),
                    ChatSessionModel.last_message_at.isnot(None),
                    ChatSessionModel.last_message_at < idle_cutoff,
                )
                .values(archived=True, updated_at=now)
            )
            await session.commit()
            return {
                "deleted": del_result.rowcount or 0,
                "archived": arch_result.rowcount or 0,
            }

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Return a session's messages, oldest first (lazy hydration path)."""
        rows = await self._all(
            select(ChatMessageModel)
            .where(ChatMessageModel.session_id == session_id)
            .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.id.asc())
        )
        return [self._message_to_dict(r) for r in rows]

    async def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Append one message and bump the session's counters in one txn.

        When ``metadata`` carries token counts (assistant turns), the parent
        session's cumulative totals are incremented in the same transaction —
        this is the ONLY place session-level token rollup happens, mirroring
        how ``message_count`` already accumulates here.

        Returns None when the parent session no longer exists — guards against
        the "a stream/tool callback outlives a session delete" race
        (never orphan a chat_messages row).
        """
        now = _utcnow()
        async with AsyncSessionLocal() as session:
            parent = await session.get(ChatSessionModel, session_id)
            if parent is None:
                logger.debug(
                    "append_message: session %s gone — skipping orphan write", session_id
                )
                return None
            msg = ChatMessageModel(
                session_id=session_id,
                role=role,
                content=content or "",
                meta=metadata,
                created_at=now,
            )
            session.add(msg)
            parent.message_count = (parent.message_count or 0) + 1
            parent.last_message_at = now
            parent.updated_at = now
            if metadata:
                parent.total_input_tokens = (parent.total_input_tokens or 0) + (metadata.get("input_tokens", 0) or 0)
                parent.total_output_tokens = (parent.total_output_tokens or 0) + (metadata.get("output_tokens", 0) or 0)
                parent.total_cache_read_tokens = (parent.total_cache_read_tokens or 0) + (metadata.get("cache_read_tokens", 0) or 0)
                parent.total_cache_creation_tokens = (parent.total_cache_creation_tokens or 0) + (metadata.get("cache_creation_tokens", 0) or 0)
            # First user turn names the chat from its text.
            if parent.message_count == 1 and role == "user" and content:
                parent.title = content.strip().splitlines()[0][:60] or parent.title
            await session.commit()
            await session.refresh(msg)
            return self._message_to_dict(msg)

    async def replace_with_summary(
        self,
        session_id: str,
        *,
        summary: str,
        keep_recent: int,
    ) -> Optional[Dict[str, Any]]:
        """Collapse old messages into a single system summary (compaction).

        Deletes all but the most recent ``keep_recent`` messages, prepends a
        system message holding *summary*, and recomputes ``message_count``.
        Returns the new message total, or None if the session is gone.
        """
        async with AsyncSessionLocal() as session:
            parent = await session.get(ChatSessionModel, session_id)
            if parent is None:
                return None
            rows = (
                (
                    await session.execute(
                        select(ChatMessageModel)
                        .where(ChatMessageModel.session_id == session_id)
                        .order_by(
                            ChatMessageModel.created_at.asc(),
                            ChatMessageModel.id.asc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) <= keep_recent:
                return {"message_count": len(rows), "compacted": 0}

            to_remove = rows[:-keep_recent] if keep_recent > 0 else rows
            oldest_ts = to_remove[0].created_at
            removed = len(to_remove)
            for r in to_remove:
                await session.delete(r)
            # Summary sits before the kept tail (timestamp just before the oldest
            # removed message so ordering is preserved).
            session.add(
                ChatMessageModel(
                    session_id=session_id,
                    role="system",
                    content=summary,
                    meta={"compaction": True, "summarized_messages": removed},
                    created_at=oldest_ts,
                )
            )
            parent.message_count = (len(rows) - removed) + 1
            parent.updated_at = _utcnow()
            await session.commit()
            return {"message_count": parent.message_count, "compacted": removed}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _update_fields(self, session_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        fields["updated_at"] = _utcnow()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(ChatSessionModel)
                .where(ChatSessionModel.id == session_id)
                .values(**fields)
            )
            await session.commit()
            return result.rowcount > 0

    @staticmethod
    def _session_to_dict(row: ChatSessionModel) -> Dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "workflow_name": row.workflow_name,
            "model": row.model,
            "archived": bool(row.archived),
            "is_important": bool(row.is_important),
            "message_count": row.message_count or 0,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
            "total_input_tokens": row.total_input_tokens or 0,
            "total_output_tokens": row.total_output_tokens or 0,
            "total_cache_read_tokens": row.total_cache_read_tokens or 0,
            "total_cache_creation_tokens": row.total_cache_creation_tokens or 0,
            # Lifetime prompt-cache efficiency for this session. Computed on
            # read rather than stored so it can never drift from the counters
            # it summarizes. Pure function — no WARNING here; the tripwire
            # belongs on the run path (workflow.executor.result), not on every
            # session list request.
            "cache_hit_rate": cache_hit_rate(
                row.total_input_tokens or 0,
                row.total_cache_read_tokens or 0,
            ),
        }

    @staticmethod
    def _message_to_dict(row: ChatMessageModel) -> Dict[str, Any]:
        return {
            "id": row.id,
            "session_id": row.session_id,
            "role": row.role,
            "content": row.content,
            "metadata": row.meta or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
