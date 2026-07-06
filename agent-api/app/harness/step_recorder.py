"""Step-level trajectory event recorder (offline-RL-export groundwork).

Buffers typed events for one trace (a root run or a subagent span) in
memory and flushes them as a single batch insert — never a per-step DB
round-trip. Recorded ALONGSIDE the existing flat ``executions.trajectory``
JSON blob (``app.services.trajectory_service``), not instead of it.

Gated by ``settings.step_events_enabled`` (default False): callers always go
through :func:`build_step_recorder`, which returns a no-op recorder when the
flag is off or no trace_id is available, so hook sites never need an
``if enabled`` branch of their own.

Event payload shape (typed step record — observation / hidden_state /
action / outcome / reward / meta):

    {"observation": {...}, "hidden_state": {...}, "action": {...},
     "outcome": {...}, "reward": {"immediate": None, "late_bound": []},
     "meta": {"model_id": ..., "session_id": ...}}

``reward.late_bound`` is the only field ever mutated after the event is
written (see ``trajectory_event_repository.append_reward``) — everything
else is immutable once recorded, preserving the causal record.

Wired into BOTH engines: the native turn loop records live
(``app.harness.engine.turn_loop``); the LangGraph engine records post-hoc
over the final serialized message list
(``app.harness.agent_runner._instrument_langgraph_result``).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _new_event_id() -> str:
    return uuid.uuid4().hex[:32]


class StepRecorder:
    """Buffers trajectory events for one trace (run or subagent span)."""

    #: Lets hot-path callers skip cheap-but-not-free prep work (e.g. text
    #: extraction for a model-turn observation) without an isinstance check.
    enabled = True

    def __init__(
        self, trace_id: str, *, span_id: Optional[str] = None, model_id: Optional[str] = None,
    ) -> None:
        self.trace_id = trace_id
        self.span_id = span_id
        self.model_id = model_id
        self._buffer: List[Dict[str, Any]] = []

    def record_model_turn(
        self,
        step_index: int,
        *,
        reason: str,
        text: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        stop_reason: Optional[str] = None,
        tokens: Optional[Dict[str, int]] = None,
    ) -> None:
        action: Dict[str, Any] = {"kind": "model_turn", "reason": reason}
        if tool_calls:
            action["tool_calls"] = [
                {"id": tc.get("id"), "name": tc.get("name")} for tc in tool_calls
            ]
        self._append(step_index, "model_turn", {
            "observation": {"kind": "text", "chars": len(text or "")},
            "action": action,
            "outcome": {"status": "ok", "stop_reason": stop_reason},
            "reward": {"immediate": None, "late_bound": []},
            "meta": self._meta(tokens),
        })

    def record_tool_call(
        self,
        step_index: int,
        *,
        tool_name: str,
        status: str,
        latency_ms: Optional[float] = None,
        error_class: Optional[str] = None,
    ) -> None:
        self._append(step_index, "tool_call", {
            "action": {"kind": "tool_call", "name": tool_name},
            "outcome": {
                "status": status, "error_class": error_class, "latency_ms": latency_ms,
            },
            "reward": {"immediate": None, "late_bound": []},
            "meta": self._meta(None),
        })

    def record_lifecycle(self, step_index: int, event: str, **extra: Any) -> None:
        self._append(step_index, "lifecycle", {
            "action": {"kind": "lifecycle", "event": event, **extra},
            "reward": {"immediate": None, "late_bound": []},
            "meta": self._meta(None),
        })

    def _meta(self, tokens: Optional[Dict[str, int]]) -> Dict[str, Any]:
        meta: Dict[str, Any] = {"model_id": self.model_id, "session_id": self.trace_id}
        if tokens:
            meta["tokens"] = tokens
        return meta

    def _append(self, step_index: int, event_type: str, payload: Dict[str, Any]) -> None:
        self._buffer.append({
            "event_id": _new_event_id(),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "step_index": step_index,
            "type": event_type,
            "payload": payload,
        })

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    async def flush(self) -> None:
        """Persist buffered events as one batch insert; clears the buffer.

        Best-effort: a persistence failure is logged, never raised — step
        recording must never break a run.
        """
        if not self._buffer:
            return
        pending, self._buffer = self._buffer, []
        try:
            from app.infrastructure.persistence.trajectory_event_repository import (
                trajectory_event_repository,
            )
            await trajectory_event_repository.append_batch(pending)
        except Exception:  # noqa: BLE001 — recording must never break a run
            logger.warning(
                "step_recorder: flush failed for trace_id=%s (%d event(s) dropped)",
                self.trace_id, len(pending),
            )


class _NoopStepRecorder:
    """Zero-cost stand-in when step_events_enabled is False (or no trace_id)."""

    enabled = False

    def record_model_turn(self, *args: Any, **kwargs: Any) -> None:
        return None

    def record_tool_call(self, *args: Any, **kwargs: Any) -> None:
        return None

    def record_lifecycle(self, *args: Any, **kwargs: Any) -> None:
        return None

    @property
    def buffered_count(self) -> int:
        return 0

    async def flush(self) -> None:
        return None


def build_step_recorder(
    trace_id: Optional[str], *, span_id: Optional[str] = None, model_id: Optional[str] = None,
) -> Any:
    """Return a live StepRecorder when enabled, else a no-op recorder."""
    try:
        from app.config import settings
        enabled = bool(getattr(settings, "step_events_enabled", False))
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled or not trace_id:
        return _NoopStepRecorder()
    return StepRecorder(trace_id, span_id=span_id, model_id=model_id)


__all__ = ["StepRecorder", "build_step_recorder"]
