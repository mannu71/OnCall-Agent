"""Execution-scoped scratch store (opt-in Postgres backend for todos + VFS).

Backs the "postgres" option of ``settings.scratch_store_backend`` — see
migration ``029_execution_scratch_store.sql``. One row per (execution_id,
store); the whole store's content is a JSON blob, matching how the default
in-memory backends (``planning_tools._store``, ``VFSBackend._files``) already
represent one session's data as a single Python object.
"""
import logging
from typing import Any, Optional

from sqlalchemy import delete, select

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import ExecutionScratchModel

logger = logging.getLogger(__name__)


class ExecutionScratchRepository(BaseAsyncRepository):
    """Repository for the opt-in Postgres-backed session-scratch store."""

    async def get(self, execution_id: str, store: str) -> Optional[Any]:
        """Return the stored JSON value for (execution_id, store), or None."""
        row = await self._one(
            select(ExecutionScratchModel).where(
                ExecutionScratchModel.execution_id == execution_id,
                ExecutionScratchModel.store == store,
            )
        )
        return row.value if row is not None else None

    async def set(self, execution_id: str, store: str, value: Any) -> None:
        """Upsert the whole blob for (execution_id, store)."""
        async def _work(session):
            row = (await session.execute(
                select(ExecutionScratchModel).where(
                    ExecutionScratchModel.execution_id == execution_id,
                    ExecutionScratchModel.store == store,
                )
            )).scalar_one_or_none()
            if row is None:
                session.add(ExecutionScratchModel(
                    execution_id=execution_id, store=store, value=value,
                ))
            else:
                row.value = value
        await self._run(_work)

    async def delete(self, execution_id: str, store: str) -> None:
        """Drop the row for (execution_id, store), if any."""
        async def _work(session):
            await session.execute(
                delete(ExecutionScratchModel).where(
                    ExecutionScratchModel.execution_id == execution_id,
                    ExecutionScratchModel.store == store,
                )
            )
        await self._run(_work)


execution_scratch_repository = ExecutionScratchRepository()
