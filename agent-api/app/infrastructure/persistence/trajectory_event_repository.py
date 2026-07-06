"""Typed, step-granular trajectory events (migration 030).

Backs ``app.harness.step_recorder`` when ``settings.step_events_enabled`` is
on. Recorded alongside the existing flat ``executions.trajectory`` JSON blob
(``app.services.trajectory_service``) — one row per model turn / tool call,
with ``payload.reward.late_bound`` appendable after the step (supervisor
score, eval judge, ...).
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import TrajectoryEventModel

logger = logging.getLogger(__name__)


class TrajectoryEventRepository(BaseAsyncRepository):
    """Repository for step-level trajectory events."""

    async def append_batch(self, events: List[Dict[str, Any]]) -> None:
        """Bulk-insert events (the step recorder buffers a run, flushes once).

        Each dict must carry: event_id, trace_id, step_index, type, payload,
        and optionally span_id, ts. Best-effort per-batch: a malformed event
        is skipped (logged), it never breaks the flush of the rest.
        """
        if not events:
            return

        async def _work(session):
            for ev in events:
                try:
                    session.add(TrajectoryEventModel(
                        event_id=ev["event_id"],
                        trace_id=ev["trace_id"],
                        span_id=ev.get("span_id"),
                        step_index=ev["step_index"],
                        type=ev["type"],
                        payload=ev["payload"],
                        **({"ts": ev["ts"]} if ev.get("ts") else {}),
                    ))
                except KeyError as exc:  # noqa: PERF203 — logged, not fatal
                    logger.warning("trajectory_event: skipping malformed event (%s)", exc)
        await self._run(_work)

    async def list_for_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Return all events for a trace, ordered by step_index."""
        rows = await self._all(
            select(TrajectoryEventModel)
            .where(TrajectoryEventModel.trace_id == trace_id)
            .order_by(TrajectoryEventModel.step_index.asc())
        )
        return [
            {
                "event_id": r.event_id,
                "trace_id": r.trace_id,
                "span_id": r.span_id,
                "step_index": r.step_index,
                "ts": r.ts,
                "type": r.type,
                "payload": r.payload,
            }
            for r in rows
        ]

    async def append_reward(
        self, event_id: str, source: str, value: Any,
    ) -> bool:
        """Append a late-bound reward signal to an existing event's payload.

        Original causal fields are never mutated — only
        ``payload["reward"]["late_bound"]`` is appended to, preserving
        immutability of the record's action/outcome/observation. Returns
        False (no-op) if the event doesn't exist.
        """
        from datetime import datetime, timezone

        async def _work(session):
            row = (await session.execute(
                select(TrajectoryEventModel).where(
                    TrajectoryEventModel.event_id == event_id,
                )
            )).scalar_one_or_none()
            if row is None:
                return False
            payload = dict(row.payload or {})
            reward = dict(payload.get("reward") or {})
            late_bound = list(reward.get("late_bound") or [])
            late_bound.append({
                "source": source, "value": value,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            reward["late_bound"] = late_bound
            payload["reward"] = reward
            row.payload = payload
            return True
        return await self._run(_work)

    async def append_reward_for_trace(
        self, trace_id: str, source: str, value: Any,
    ) -> bool:
        """Attach a run-level late-bound reward (e.g. a supervisor verdict
        score) to the most recent event of ``trace_id``.

        A supervisor verdict judges the whole run, not one step, so there is
        no single natural ``event_id`` for it — the last event (highest
        ``step_index``) is the closest proxy for "the decision this verdict
        is about". Returns False (no-op) if the trace has no events yet.
        """
        row = (await self._all(
            select(TrajectoryEventModel)
            .where(TrajectoryEventModel.trace_id == trace_id)
            .order_by(TrajectoryEventModel.step_index.desc())
            .limit(1)
        ))
        if not row:
            return False
        return await self.append_reward(row[0].event_id, source, value)


trajectory_event_repository = TrajectoryEventRepository()
