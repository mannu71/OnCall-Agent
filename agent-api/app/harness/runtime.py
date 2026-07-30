"""Shared LangGraph persistence runtime (durable checkpointer).

One process-wide psycopg3 connection pool feeds an ``AsyncPostgresSaver`` (durable
checkpoints → HITL resume, time-travel). Initialised once at app startup
(``init_persistence``) and closed on shutdown. Best-effort: if Postgres/psycopg is
unavailable the getter returns ``None`` and callers fall back to the in-memory
checkpointer — startup never fails because of this.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_pool: Optional[Any] = None
_saver: Optional[Any] = None
_store: Optional[Any] = None


def _psycopg_url() -> str:
    """psycopg3 wants a plain ``postgresql://`` URL (not the +asyncpg variant)."""
    from app.config import settings
    url = settings.database_url
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def init_persistence() -> None:
    """Open the shared pool and set up the checkpointer. Idempotent, best-effort."""
    global _pool, _saver, _store
    if _saver is not None:
        return
    try:
        from psycopg_pool import AsyncConnectionPool
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        _pool = AsyncConnectionPool(
            conninfo=_psycopg_url(),
            open=False,
            max_size=int(__import__("os").getenv("LG_POOL_MAX", "10")),
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await _pool.open(wait=True)

        # Optional shallow saver: keeps only the latest checkpoint per thread
        # (no time-travel history). HITL resume needs only the latest checkpoint,
        # so pause/resume keeps working. Fall back to the full saver if the class
        # is unavailable in the installed langgraph-checkpoint-postgres.
        _saver_cls = AsyncPostgresSaver
        _saver_label = "AsyncPostgresSaver"
        from app.config import settings as _settings
        if getattr(_settings, "checkpoint_shallow", False):
            try:
                from langgraph.checkpoint.postgres.aio import AsyncShallowPostgresSaver
                _saver_cls = AsyncShallowPostgresSaver
                _saver_label = "AsyncShallowPostgresSaver"
            except Exception as _shallow_exc:  # noqa: BLE001 — fall back to full saver
                logger.warning(
                    "checkpoint_shallow requested but AsyncShallowPostgresSaver "
                    "unavailable (%s) — using full AsyncPostgresSaver", _shallow_exc,
                )

        _saver = _saver_cls(_pool)
        await _saver.setup()
        logger.info("LangGraph persistence ready (%s)", _saver_label)

        # Durable key-value store on the SAME pool, backing the tool-result
        # store (app.harness.tool_result_store). index=None keeps it a plain
        # KV table — no embedding column, no vector index: recall here is by
        # exact handle, never by similarity.
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore
            _store = AsyncPostgresStore(_pool)
            await _store.setup()
            logger.info("LangGraph durable store ready")
        except Exception as _store_exc:  # noqa: BLE001 — store is optional
            _store = None
            logger.warning(
                "LangGraph durable store unavailable (%s) — large tool results "
                "will stay inline-capped and follow-up turns lose their detail",
                _store_exc,
            )
    except Exception as exc:  # noqa: BLE001 — never block startup
        logger.warning(
            "LangGraph persistence unavailable (%s) — falling back to in-memory checkpointer", exc
        )
        _saver = None
        if _pool is not None:
            try:
                await _pool.close()
            except Exception:  # noqa: BLE001
                pass
            _pool = None


async def close_persistence() -> None:
    global _pool, _saver, _store
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:  # noqa: BLE001
            pass
    _pool = _saver = _store = None


def get_saver() -> Optional[Any]:
    """Shared durable checkpointer, or None when Postgres persistence is unavailable."""
    return _saver


def get_store() -> Optional[Any]:
    """Shared durable KV store, or None when Postgres persistence is unavailable.

    Callers must treat ``None`` as "no durable storage" and degrade, never fail.
    """
    return _store
