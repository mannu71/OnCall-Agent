"""HITL and checkpoint helpers for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.workflow.execution_port import ExecutionPort

logger = logging.getLogger(__name__)

async def make_checkpointer() -> Any:
    """Create an AsyncPostgresSaver connected to the app database.

    Returns None (gracefully) if the dependency is not installed or the
    connection fails — the agent still runs without crash-safe state in
    that case.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.config import settings

        # psycopg connection string (not +asyncpg variant)
        db_url = settings.database_url
        if "+asyncpg" in db_url:
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        checkpointer = AsyncPostgresSaver.from_conn_string(db_url)
        await checkpointer.setup()
        return checkpointer
    except Exception as exc:
        logger.warning(
            "ReactStrategy: AsyncPostgresSaver unavailable — running without checkpointer: %s",
            exc,
        )
        return None

# ------------------------------------------------------------------
# HITL pause helper
# ------------------------------------------------------------------

async def emit_hitl_pause(
    execution_id: Optional[str],
    interrupt_data: Dict[str, Any],
    *,
    execution_port: Optional[ExecutionPort] = None,
) -> None:
    """Publish a hitl_pause SSE event into the execution's event queue."""
    if not execution_id:
        return
    try:
        port = execution_port
        if port is None:
            from app.core.dependencies import get_container

            executor = get_container().get_visual_executor()
            port = ExecutionPort(
                executor.active_executions,
                publish_event=executor._publish_event,
            )
        await port.publish_hitl_pause(execution_id, interrupt_data)
    except Exception as exc:
        logger.warning("ReactStrategy: could not emit hitl_pause event: %s", exc)
