"""Investigation trajectory management.

Records and retrieves the full agent message trace from execution records.
Trajectories are stored in ExecutionModel.trajectory (JSON column) and can
be replayed via TrajectoryReplay.jsx in the frontend.

Atropos format is also supported for future RL fine-tuning data pipelines.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc

from app.core.database import AsyncSessionLocal
from app.models.db_models import ExecutionModel

logger = logging.getLogger(__name__)


class TrajectoryService:
    """Stores and retrieves agent message trajectories from execution records."""

    # ──────────────────────────────────────────────────────────────────────
    # Write
    # ──────────────────────────────────────────────────────────────────────

    async def save_trajectory(
        self,
        execution_id: str,
        trajectory: List[Dict[str, Any]],
    ) -> None:
        """Persist *trajectory* for an existing execution.

        Args:
            execution_id: The execution's string ID (uuid).
            trajectory: List of LangChain message dicts produced by ReactStrategy.
        """
        # UUID strings (generated during in-memory execution before the row is
        # committed) cannot be cast to integer. Skip — the trajectory will be
        # saved correctly once persist_execution writes the DB row and calls us
        # again with the real integer ID.
        if not execution_id.isdigit():
            logger.debug(
                "save_trajectory: execution_id '%s' is not a numeric DB id — "
                "skipping (trajectory will be saved by persist_execution).",
                execution_id,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExecutionModel).where(ExecutionModel.id == int(execution_id))
            )
            record = result.scalar_one_or_none()
            if record is None:
                logger.warning(
                    "trajectory_service: execution '%s' not found — cannot save trajectory",
                    execution_id,
                )
                return

            record.trajectory = trajectory
            await session.commit()
            logger.debug(
                "Saved trajectory: %d messages for execution %s",
                len(trajectory),
                execution_id,
            )

    # ──────────────────────────────────────────────────────────────────────
    # Read
    # ──────────────────────────────────────────────────────────────────────

    async def get_trajectory(
        self,
        execution_id: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Return the stored trajectory for *execution_id*, or None."""
        if not execution_id.isdigit():
            logger.warning(
                "get_trajectory: non-numeric execution_id '%s' — returning None.",
                execution_id,
            )
            return None

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExecutionModel.trajectory).where(
                    ExecutionModel.id == int(execution_id)
                )
            )
            row = result.one_or_none()
            return row[0] if row else None

    async def list_trajectories(
        self,
        workflow_name: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return summary records for executions that have a trajectory.

        Args:
            workflow_name: Optional filter — only return executions for this workflow.
            limit: Maximum number of records to return.

        Returns:
            List of summary dicts (no raw trajectory data — use get_trajectory for that).
        """
        async with AsyncSessionLocal() as session:
            query = (
                select(
                    ExecutionModel.id,
                    ExecutionModel.workflow_name,
                    ExecutionModel.status,
                    ExecutionModel.started_at,
                    ExecutionModel.completed_at,
                    ExecutionModel.trajectory,
                )
                .where(ExecutionModel.trajectory.isnot(None))
                .order_by(desc(ExecutionModel.started_at))
                .limit(limit)
            )

            if workflow_name:
                query = query.where(ExecutionModel.workflow_name == workflow_name)

            rows = (await session.execute(query)).all()

            return [
                {
                    "execution_id": str(row.id),
                    "workflow_name": row.workflow_name,
                    "status": row.status,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": (
                        row.completed_at.isoformat() if row.completed_at else None
                    ),
                    "message_count": len(row.trajectory) if row.trajectory else 0,
                }
                for row in rows
            ]

    # ──────────────────────────────────────────────────────────────────────
    # Atropos format (RL fine-tuning data pipeline)
    # ──────────────────────────────────────────────────────────────────────

    async def get_atropos_format(
        self,
        execution_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the trajectory in Atropos structured format.

        Format:
            {
                "execution_id": "...",
                "turns": [
                    {"role": "human|assistant|tool", "content": "..."},
                    ...
                ]
            }

        Used by future RL fine-tuning pipelines to score agent trajectories
        and train improved models from resolved investigations.
        """
        trajectory = await self.get_trajectory(execution_id)
        if trajectory is None:
            return None

        turns: List[Dict[str, str]] = []
        for msg in trajectory:
            # Support both LangChain serialised format and raw dicts
            role = msg.get("type") or msg.get("role", "unknown")
            content = msg.get("content") or msg.get("text", "")

            # LangChain messages can carry list content (tool-use blocks)
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )

            turns.append({"role": str(role), "content": str(content)})

        return {"execution_id": execution_id, "turns": turns}

    # ──────────────────────────────────────────────────────────────────────
    # Step-level trajectory events (offline-RL export)
    # ──────────────────────────────────────────────────────────────────────

    async def export_step_events(self, execution_id: str) -> List[Dict[str, Any]]:
        """Return this run's step-level ``trajectory_events`` rows, ordered
        by ``step_index``, as JSONL-able dicts.

        These are the typed step-level events written by
        ``app.harness.step_recorder`` (see migration 030) when
        ``settings.step_events_enabled`` is on — a finer-grained sibling of
        :meth:`get_atropos_format`'s flat message-role view, carrying
        per-step observation/action/outcome/reward. Returns an empty list
        (never raises) when step events are off or the trace has none —
        callers don't need to guard.
        """
        try:
            from app.infrastructure.persistence import trajectory_event_repository
            return await trajectory_event_repository.list_for_trace(execution_id)
        except Exception as exc:  # noqa: BLE001 — export must never raise
            logger.debug("trajectory_service.export_step_events: %s", exc)
            return []


# Singleton instance
trajectory_service = TrajectoryService()
