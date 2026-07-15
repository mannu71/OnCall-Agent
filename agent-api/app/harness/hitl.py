"""HITL and checkpoint helpers for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.workflow.execution_port import ExecutionPort

logger = logging.getLogger(__name__)

async def make_checkpointer() -> Any:
    """Create a LangGraph checkpointer for the agent graph.

    Prefers a crash-safe ``AsyncPostgresSaver``; if that package is unavailable
    or fails to connect, falls back to an in-process ``InMemorySaver`` rather
    than ``None``. A checkpointer is required for HITL resume AND for recovering
    the partial conversation when the ReAct loop hits its recursion limit, so we
    always provide one (single-worker deployment → in-memory state is fine).
    """
    try:
        # Prefer the process-wide pooled saver set up at startup by
        # runtime.init_persistence(). Building a fresh AsyncPostgresSaver here via
        # from_conn_string() is a bug: in langgraph-checkpoint-postgres>=3.1.0 that
        # is an async context manager, so calling .setup() on it raises and we
        # silently fall through to InMemorySaver on every run.
        from app.harness.runtime import get_saver

        saver = get_saver()
        if saver is not None:
            return saver
    except Exception as exc:
        logger.warning(
            "ReactStrategy: shared AsyncPostgresSaver unavailable (%s) — falling back to "
            "in-memory checkpointer", exc,
        )
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    except Exception:  # pragma: no cover — very old langgraph
        try:
            from langgraph.checkpoint.memory import MemorySaver
            return MemorySaver()
        except Exception as mem_exc:  # noqa: BLE001
            logger.warning(
                "ReactStrategy: in-memory checkpointer also unavailable (%s) — "
                "running without checkpointer", mem_exc,
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
