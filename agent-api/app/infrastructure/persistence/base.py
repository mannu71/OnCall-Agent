"""Shared async-repository helpers.

Every repository previously repeated the same ``async with AsyncSessionLocal()``
open/execute/scalar/commit dance per method. :class:`BaseAsyncRepository`
centralises that so repos keep only their *unique* queries. Method signatures,
the module singletons, and the ``Depends(get_*_repo)`` injection are unchanged —
this is an internal de-duplication, not an API change.

Helpers:
  * ``_one(stmt)``    — execute a SELECT, return the first scalar or ``None``.
  * ``_all(stmt)``    — execute a SELECT, return a list of scalars.
  * ``_rows(stmt)``   — execute a SELECT, return the raw Result for custom mapping.
  * ``_run(work)``    — open a session, await ``work(session)``, commit, return its
                        value (for inserts/updates/upserts that need the session).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, List, Optional, TypeVar

from sqlalchemy import Select

from app.core.database import AsyncSessionLocal

T = TypeVar("T")


class BaseAsyncRepository:
    """Mix-in providing session-scoped query/commit helpers."""

    async def _one(self, stmt: Select) -> Optional[Any]:
        """Return the first scalar of *stmt*, or ``None``."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def _first(self, stmt: Select) -> Optional[Any]:
        """Return the first scalar of *stmt* (tolerates multiple rows), or ``None``.

        Use for ``ORDER BY … LIMIT 1``-style "preferred row" lookups where more
        than one row may match (e.g. multi-credential providers); ``_one`` would
        raise on multiple.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(stmt)
            return result.scalars().first()

    async def _all(self, stmt: Select) -> List[Any]:
        """Return all scalars of *stmt* as a list."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def _run(self, work: Callable[[Any], Awaitable[T]]) -> T:
        """Open a session, run ``work(session)``, commit, and return its result.

        ``work`` does the mutation (add/assign) and may return a value; the
        commit is handled here. On error the context manager rolls back.
        """
        async with AsyncSessionLocal() as session:
            value = await work(session)
            await session.commit()
            return value
