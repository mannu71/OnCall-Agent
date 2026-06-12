"""Durable background-job store (repo indexing today).

Replaces the fire-and-forget asyncio task as the *observable* state of
background work. A job moves queued → running → completed/failed, carries live
``progress``/``total``/``detail`` plus a ``heartbeat_at`` so a stalled worker is
detectable, and records ``claimed_by`` so a future multi-replica deployment can
claim queued jobs with ``FOR UPDATE SKIP LOCKED`` without double-running them.

All writes are best-effort: a job-store failure must never break the actual
indexing work, so callers wrap nothing and these methods swallow+log.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal
from app.models.db_models import BackgroundJobModel

logger = logging.getLogger(__name__)


def _worker_id() -> str:
    """Stable-ish id for this process (host:pid) — used for job claiming."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundJobStore:
    """CRUD + lifecycle for ``background_jobs``."""

    async def create(
        self,
        job_type: str,
        target: str,
        *,
        total: int = 0,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Insert a queued job; returns its id (or None on failure)."""
        try:
            async with AsyncSessionLocal() as session:
                job = BackgroundJobModel(
                    job_type=job_type, target=target, status="queued",
                    total=total, payload=payload, created_at=_now(),
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
                return job.id
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackgroundJobStore.create failed (%s/%s): %s", job_type, target, exc)
            return None

    async def mark_running(self, job_id: Optional[int]) -> None:
        if job_id is None:
            return
        await self._update(job_id, status="running", started_at=_now(),
                           claimed_by=_worker_id(), heartbeat_at=_now())

    async def update_progress(
        self, job_id: Optional[int], *, progress: int, detail: Optional[str] = None
    ) -> None:
        """Bump progress + refresh the heartbeat (called per unit of work)."""
        if job_id is None:
            return
        fields: Dict[str, Any] = {"progress": progress, "heartbeat_at": _now()}
        if detail is not None:
            fields["detail"] = detail
        await self._update(job_id, **fields)

    async def mark_completed(self, job_id: Optional[int], *, detail: Optional[str] = None) -> None:
        if job_id is None:
            return
        await self._update(job_id, status="completed", ended_at=_now(),
                           heartbeat_at=_now(), detail=detail)

    async def mark_failed(self, job_id: Optional[int], *, error: str) -> None:
        if job_id is None:
            return
        await self._update(job_id, status="failed", ended_at=_now(),
                           heartbeat_at=_now(), error=error[:4000])

    async def list_active(self) -> List[Dict[str, Any]]:
        """Return queued/running jobs (newest first)."""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(BackgroundJobModel)
                    .where(BackgroundJobModel.status.in_(("queued", "running")))
                    .order_by(BackgroundJobModel.created_at.desc())
                )
                return [self._to_dict(r) for r in result.scalars().all()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackgroundJobStore.list_active failed: %s", exc)
            return []

    async def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(BackgroundJobModel)
                    .order_by(BackgroundJobModel.created_at.desc())
                    .limit(limit)
                )
                return [self._to_dict(r) for r in result.scalars().all()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackgroundJobStore.list_recent failed: %s", exc)
            return []

    async def reap_orphans(self) -> int:
        """Mark still-'running' jobs from a previous process as failed on startup.

        A fire-and-forget worker that died leaves its job stuck in 'running'.
        Since state is per-process today, any 'running' row at startup is an
        orphan. (Multi-replica: replace with a heartbeat-age check.)
        """
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    update(BackgroundJobModel)
                    .where(BackgroundJobModel.status == "running")
                    .values(status="failed", ended_at=_now(),
                            error="Worker process restarted before completion")
                    .returning(BackgroundJobModel.id)
                )
                await session.commit()
                ids = list(result.scalars().all())
                if ids:
                    logger.info("BackgroundJobStore.reap_orphans: reaped %d orphaned job(s)", len(ids))
                return len(ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackgroundJobStore.reap_orphans failed: %s", exc)
            return 0

    async def claim_next(self, job_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Atomically claim the oldest queued job (multi-replica safe).

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent workers/replicas
        never claim the same row. Returns the claimed job dict (now 'running')
        or None when the queue is empty. This is the building block for a
        multi-replica worker loop; today indexing runs inline, so it is unused in
        the default single-process path but ready for horizontal scaling.
        """
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(BackgroundJobModel)
                    .where(BackgroundJobModel.status == "queued")
                )
                if job_type is not None:
                    stmt = stmt.where(BackgroundJobModel.job_type == job_type)
                stmt = (
                    stmt.order_by(BackgroundJobModel.created_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is None:
                    return None
                row.status = "running"
                row.started_at = _now()
                row.claimed_by = _worker_id()
                row.heartbeat_at = _now()
                await session.commit()
                await session.refresh(row)
                return self._to_dict(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackgroundJobStore.claim_next failed: %s", exc)
            return None

    async def _update(self, job_id: int, **fields: Any) -> None:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(BackgroundJobModel)
                    .where(BackgroundJobModel.id == job_id)
                    .values(**fields)
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackgroundJobStore update failed (job=%s): %s", job_id, exc)

    @staticmethod
    def _to_dict(row: BackgroundJobModel) -> Dict[str, Any]:
        return {
            "id": row.id,
            "job_type": row.job_type,
            "target": row.target,
            "status": row.status,
            "progress": row.progress,
            "total": row.total,
            "detail": row.detail,
            "error": row.error,
            "claimed_by": row.claimed_by,
            "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        }


# Module-level singleton.
background_job_store = BackgroundJobStore()
