"""Runtime execution accessors for workflow strategies.

Strategies receive an :class:`ExecutionPort` via ``strategy_context`` instead of
importing the ``visual_executor`` singleton directly.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

PublishFn = Callable[[str, str, Dict[str, Any]], Awaitable[None]]


class ExecutionPort:
    """Thin facade over executor runtime state and SSE publishing."""

    def __init__(
        self,
        runtime_cache: Dict[str, Dict[str, Any]],
        publish_event: Optional[PublishFn] = None,
    ) -> None:
        self._cache = runtime_cache
        self._publish = publish_event

    def get_runtime(self, execution_id: str) -> Dict[str, Any]:
        return self._cache.get(execution_id, {})

    def set_trace_id(self, execution_id: Optional[str], trace_id: str) -> None:
        if not execution_id:
            return
        entry = self._cache.get(execution_id)
        if entry is not None:
            entry["trace_id"] = trace_id

    def drain_steer_notes(self, execution_id: Optional[str]) -> List[str]:
        if not execution_id:
            return []
        notes: list = self._cache.get(execution_id, {}).get("steer_notes", [])
        drained: List[str] = []
        while notes:
            drained.append(notes.pop(0))
        return drained

    async def publish_hitl_pause(
        self,
        execution_id: Optional[str],
        interrupt_data: Dict[str, Any],
    ) -> None:
        if not execution_id:
            return

        payload = {
            "execution_id": execution_id,
            "request_id": interrupt_data.get("request_id", ""),
            "draft_answer": interrupt_data.get("draft_answer", ""),
            "message": interrupt_data.get(
                "message", "Engineer approval required.",
            ),
        }

        if self._publish is not None:
            from app.workflow.event_schema import EventType

            await self._publish(execution_id, EventType.HITL_PAUSE, payload)
            return

        queue = self.get_runtime(execution_id).get("event_queue")
        if queue is not None:
            from app.workflow.event_schema import EventType, WorkflowEvent

            event = WorkflowEvent(event_type=EventType.HITL_PAUSE, data=payload)
            await queue.put(event.to_sse())
