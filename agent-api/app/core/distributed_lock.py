"""Postgres advisory-lock helpers for multi-replica coordination.

Provides session-level advisory locks (``pg_try_advisory_lock``) used for leader
election so that, when more than one replica runs, exactly one fires scheduled
workflows / claims singleton work — without introducing Redis or another
dependency. Single-process deployments acquire the lock trivially and behave
exactly as before.

A *session-level* lock is held for the lifetime of the connection it was taken
on, so leader election keeps a dedicated connection open for the duration.
"""
from __future__ import annotations

import logging
import zlib
from typing import Optional

from sqlalchemy import text

from app.core.database import async_engine

logger = logging.getLogger(__name__)


def _lock_key(name: str) -> int:
    """Map a lock name to a stable signed 64-bit int key for pg advisory locks."""
    # crc32 → 32-bit; shift into a deterministic 63-bit positive range.
    return zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF


class LeaderLock:
    """Best-effort leader election via a session-level pg advisory lock.

    Usage::

        leader = LeaderLock("workflow_scheduler")
        if await leader.acquire():
            # this replica is the leader — start the scheduler
        ...
        await leader.release()

    ``acquire`` returns True immediately for a single replica (no contention).
    On any DB error it returns True (fail-open: never let lock infra stop a
    single-node deployment from scheduling).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.key = _lock_key(name)
        self._conn = None

    async def acquire(self) -> bool:
        try:
            self._conn = await async_engine.connect()
            row = await self._conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}
            )
            got = bool(row.scalar())
            if not got:
                await self._conn.close()
                self._conn = None
            return got
        except Exception as exc:  # noqa: BLE001 — fail-open for single-node
            logger.warning(
                "LeaderLock(%s): advisory lock unavailable (%s) — assuming leader",
                self.name, exc,
            )
            return True

    async def release(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": self.key}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LeaderLock(%s): unlock failed (%s)", self.name, exc)
        finally:
            try:
                await self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None


async def try_advisory_lock(name: str) -> Optional[object]:
    """Convenience: acquire a LeaderLock, returning it if held, else None."""
    lock = LeaderLock(name)
    return lock if await lock.acquire() else None
