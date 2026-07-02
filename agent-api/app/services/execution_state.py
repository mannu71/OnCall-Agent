"""Execution runtime state — DB is source of truth, in-memory cache is ephemeral.

Persistent fields (status, timestamps, output) live in PostgreSQL via
``ExecutionRepository``.  Process-local data (SSE queues, HITL queues,
steer notes, full workflow dict, event buffers) lives in ``runtime_cache``
only for the lifetime of an in-process run.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.infrastructure.persistence import ExecutionRepository

# Executions older than this that are still marked "running" are considered
# crashed/orphaned and are auto-expired so they don't permanently block re-runs.
_STALE_THRESHOLD = timedelta(hours=2)

logger = logging.getLogger(__name__)


class ExecutionStateService:
    """Coordinates durable execution records with ephemeral runtime caches."""

    def __init__(self, repo: Optional[ExecutionRepository] = None) -> None:
        self.repo = repo or ExecutionRepository()
        # Ephemeral per-process state keyed by execution_id (DB id as string).
        self.runtime_cache: Dict[str, Dict[str, Any]] = {}
        # The asyncio.Task actually running this execution (foreground /execute
        # path wraps run_workflow() in create_task() — see workflows.py — and
        # visual_workflow_executor registers it here right after the execution_id
        # is minted). Cancelling THIS task is what makes "Stop" real: without it,
        # cancel_execution only marked the DB row cancelled while the coroutine
        # kept running orphaned (see prior incident — the run never actually
        # stopped, so the workflow stayed "running" and rejected the next
        # message with already_running).
        self._run_tasks: Dict[str, "asyncio.Task"] = {}

    async def start_execution(
        self,
        workflow_name: str,
        *,
        workflow_id: Optional[Any] = None,
        inputs: Optional[Dict[str, Any]] = None,
        workflow: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a durable ``running`` row and initialise the runtime cache."""
        wid: Optional[int] = None
        if workflow_id is not None:
            try:
                wid = int(workflow_id)
            except (TypeError, ValueError):
                wid = None

        record = await self.repo.create_running(
            workflow_name,
            workflow_id=wid,
            inputs=inputs,
        )
        execution_id = str(record["execution_id"])

        self.runtime_cache[execution_id] = {
            "id": execution_id,
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "workflow_id": workflow_id,
            "workflow": workflow or {},
            "status": "running",
            "start_time": record.get("started_at") or record.get("start_time"),
            "nodes_completed": [],
            "events": [],
            "inputs": inputs or {},
        }
        return execution_id

    async def find_running_id(self, workflow_name: str) -> Optional[str]:
        """Return execution id if *workflow_name* has a running DB record.

        Stale records (started more than _STALE_THRESHOLD ago with no heartbeat)
        are auto-cancelled so a crashed workflow never permanently blocks re-runs.
        """
        record = await self.repo.find_running_by_workflow(workflow_name)
        if record:
            exec_id = str(record["execution_id"])
            started_raw = record.get("started_at")
            if started_raw:
                try:
                    started = datetime.fromisoformat(started_raw)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - started > _STALE_THRESHOLD:
                        logger.warning(
                            "Auto-expiring stale execution %s for '%s' (started %s)",
                            exec_id, workflow_name, started_raw,
                        )
                        await self.repo.mark_cancelled(exec_id, reason="auto-expired: stale")
                        self.runtime_cache.pop(exec_id, None)
                        return None
                except (ValueError, TypeError):
                    pass
            return exec_id

        for exec_id, data in self.runtime_cache.items():
            if (
                data.get("workflow_name") == workflow_name
                and data.get("status") == "running"
            ):
                return exec_id
        return None

    async def list_active_workflow_names(self) -> List[str]:
        """Workflow names with at least one running execution in the DB."""
        records = await self.repo.list_active()
        return list({r["workflow_name"] for r in records if r.get("workflow_name")})

    async def list_active_records(self) -> List[Dict[str, Any]]:
        """All running execution records from the DB."""
        return await self.repo.list_active()

    def get_cache(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Return ephemeral runtime cache entry (same-process only)."""
        return self.runtime_cache.get(execution_id)

    async def get_execution_status(
        self, execution_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return cache entry when present, otherwise a DB snapshot for running rows."""
        cached = self.runtime_cache.get(execution_id)
        if cached is not None:
            return cached

        record = await self.repo.get_by_id(execution_id)
        if record and record.get("status") in ("running", "pending"):
            return record
        return None

    def mirror_status(self, execution_id: str, status: str, **fields: Any) -> None:
        """Update mirrored status fields in the runtime cache."""
        cached = self.runtime_cache.get(execution_id)
        if cached is None:
            return
        cached["status"] = status
        cached.update(fields)

    def cleanup_cache(self, execution_id: str) -> None:
        """Drop ephemeral runtime state after a run finishes."""
        self.runtime_cache.pop(execution_id, None)

    def register_task(self, execution_id: str, task: "asyncio.Task") -> None:
        """Record the asyncio.Task actually executing *execution_id*.

        Call this once, right after the execution_id is minted, from INSIDE the
        task itself (``asyncio.current_task()``) — never a task handle captured
        from an outer scope, since only the task that will actually keep running
        the ReAct loop can be meaningfully cancelled.
        """
        self._run_tasks[execution_id] = task

    def unregister_task(self, execution_id: str) -> None:
        """Drop the task handle once a run finishes (success, failure, or cancel)."""
        self._run_tasks.pop(execution_id, None)

    def _cancel_task(self, execution_id: str) -> bool:
        """Best-effort ``task.cancel()`` for a registered run. Returns True if found."""
        task = self._run_tasks.pop(execution_id, None)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def cancel_execution(
        self,
        execution_id: str,
        *,
        reason: str = "Cancelled by operator",
    ) -> bool:
        """Mark an execution cancelled in the DB, cancel its task, and drop its cache."""
        ok = await self.repo.mark_cancelled(execution_id, reason=reason)
        self._cancel_task(execution_id)
        self.runtime_cache.pop(execution_id, None)
        return ok

    async def cancel_by_workflow_name(
        self,
        workflow_name: str,
        *,
        reason: str = "Cancelled by operator",
    ) -> int:
        """Cancel all active executions for a workflow."""
        count = await self.repo.cancel_all_active(
            workflow_name=workflow_name,
            reason=reason,
        )
        to_drop = [
            eid
            for eid, data in self.runtime_cache.items()
            if data.get("workflow_name") == workflow_name
        ]
        for eid in to_drop:
            self._cancel_task(eid)
            self.runtime_cache.pop(eid, None)
        return count

    async def cancel_all_active(
        self,
        *,
        reason: str = "Cleared by operator",
    ) -> int:
        """Cancel every active execution and clear runtime caches."""
        count = await self.repo.cancel_all_active(reason=reason)
        for eid in list(self._run_tasks.keys()):
            self._cancel_task(eid)
        self.runtime_cache.clear()
        return count


execution_state = ExecutionStateService()
