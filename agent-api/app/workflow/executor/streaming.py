"""SSE streaming helpers extracted from VisualWorkflowExecutor.

Owns the dropped-event counter and the agent streaming callback adapter so the
counter is shared via a single module-level variable rather than duplicated.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from app.config import settings
from app.core.redact import redact

logger = logging.getLogger(__name__)

# SSE event queue backpressure settings
_SSE_QUEUE_MAXSIZE = settings.sse_queue_maxsize
_DROPPED_EVENTS = 0


def _safe_put(queue: asyncio.Queue, event) -> None:
    """Non-blocking enqueue; drops event and increments counter on QueueFull."""
    global _DROPPED_EVENTS
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        _DROPPED_EVENTS += 1
        logger.warning("SSE queue full, dropped event. total_dropped=%d", _DROPPED_EVENTS)


def get_dropped_event_count() -> int:
    """Return the count of SSE events dropped due to queue backpressure."""
    return _DROPPED_EVENTS


if TYPE_CHECKING:  # pragma: no cover
    from app.services.visual_workflow_executor import VisualWorkflowExecutor


class _AgentStreamCallback:
    """Adapts StreamCallback protocol events to VisualWorkflowExecutor._publish_event."""

    def __init__(
        self,
        executor: "VisualWorkflowExecutor",
        execution_id: str,
        node_id: str,
        agent_name: str = "agent",
    ):
        self._executor = executor
        self._execution_id = execution_id
        self._node_id = node_id
        # Default attribution for tool events emitted directly by the main agent.
        # A delegated child overrides it per-event via the ``agent`` kwarg (see
        # subagent_factory._ChildStreamCallback) so the chat can badge who ran a
        # tool (main agent vs. a named squad/subagent).
        self._agent_name = agent_name
        # The main agent's resolved model id, set by preflight once known, so the
        # chat can show which model ran each call. Subagents override per-event.
        self.model_name = ""

    async def on_llm_token(self, token: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "llm_token",
            {"token": token, "node_id": self._node_id},
        )

    async def on_tool_call(
        self, tool_name: str, args: dict, agent: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        await self._executor._publish_event(
            self._execution_id, "tool_call",
            {"tool": tool_name, "args": args, "node_id": self._node_id,
             "agent": agent or self._agent_name, "model": model or self.model_name},
        )

    async def on_tool_result(
        self, tool_name: str, result: str, failed: bool = False, agent: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        await self._executor._publish_event(
            self._execution_id, "tool_result",
            {
                "tool": tool_name, "result": redact(result)[:2000], "node_id": self._node_id,
                "failed": failed, "agent": agent or self._agent_name,
                "model": model or self.model_name,
            },
        )

    async def on_error(self, error: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "agent_error",
            {"error": redact(error), "node_id": self._node_id},
        )

    async def on_complete(self, output: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "agent_complete",
            {"output": redact(output)[:500], "node_id": self._node_id},
        )
