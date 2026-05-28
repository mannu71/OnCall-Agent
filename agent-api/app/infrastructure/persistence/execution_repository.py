"""Execution repository for execution history data access."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models.db_models import ExecutionModel

logger = logging.getLogger(__name__)


class ExecutionRepository:
    """Repository for execution history data access."""
    
    def __init__(self):
        """Initialize execution repository."""
        logger.info("ExecutionRepository initialized")
    
    async def get_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get execution by ID.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            Execution if found, None otherwise
        """
        try:
            execution_id_int = int(execution_id)
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ExecutionModel).where(ExecutionModel.id == execution_id_int)
                )
                execution = result.scalar_one_or_none()
                return self._execution_to_dict(execution) if execution else None
        except (ValueError, TypeError):
            logger.error(f"Invalid execution ID: {execution_id}")
            return None
    
    async def list_by_workflow(self, workflow_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """List executions for a workflow.
        
        Args:
            workflow_name: Workflow name to filter
            limit: Maximum number to return
            
        Returns:
            List of executions
        """
        async with AsyncSessionLocal() as session:
            query = select(ExecutionModel).where(
                ExecutionModel.workflow_name == workflow_name
            ).order_by(ExecutionModel.started_at.desc()).limit(limit)
            
            result = await session.execute(query)
            executions = result.scalars().all()
            return [self._execution_to_dict(e) for e in executions]
    
    async def list_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List all executions.
        
        Args:
            limit: Maximum number to return
            
        Returns:
            List of executions
        """
        async with AsyncSessionLocal() as session:
            query = select(ExecutionModel).order_by(
                ExecutionModel.started_at.desc()
            ).limit(limit)
            
            result = await session.execute(query)
            executions = result.scalars().all()
            return [self._execution_to_dict(e) for e in executions]
    
    async def save(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save (insert or update) an execution record.

        Accepts both the canonical field names (``started_at``,
        ``completed_at``, ``output``) and the legacy aliases used by older
        callers (``start_time``, ``end_time``, ``duration`` in seconds,
        ``result``/``results``). Token usage fields are persisted when
        present so cost telemetry is preserved.
        """
        execution_id = execution_data.get("execution_id") or execution_data.get("id")

        started_at = (
            execution_data.get("started_at")
            or self._parse_datetime(execution_data.get("start_time"))
            or datetime.now(timezone.utc)
        )
        completed_at = (
            execution_data.get("completed_at")
            or self._parse_datetime(execution_data.get("end_time"))
        )
        duration_ms = execution_data.get("duration_ms")
        if duration_ms is None and execution_data.get("duration") is not None:
            duration_ms = int(execution_data["duration"] * 1000)

        output = (
            execution_data.get("output")
            or execution_data.get("result")
            or execution_data.get("results")
        )

        fields = dict(
            workflow_id=execution_data.get("workflow_id"),
            workflow_name=execution_data["workflow_name"],
            status=execution_data.get("status", "pending"),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            input=execution_data.get("input"),
            output=output,
            error=execution_data.get("error"),
            logs=execution_data.get("logs"),
            input_tokens=execution_data.get("input_tokens", 0) or 0,
            output_tokens=execution_data.get("output_tokens", 0) or 0,
            total_tokens=execution_data.get("total_tokens", 0) or 0,
            trajectory=execution_data.get("trajectory"),
        )

        async with AsyncSessionLocal() as session:
            if execution_id is not None:
                try:
                    eid = int(execution_id)
                except (TypeError, ValueError):
                    eid = None
                if eid is not None:
                    result = await session.execute(
                        select(ExecutionModel).where(ExecutionModel.id == eid)
                    )
                    existing = result.scalar_one_or_none()
                    if existing is not None:
                        for key, value in fields.items():
                            if hasattr(existing, key) and value is not None:
                                setattr(existing, key, value)
                        await session.commit()
                        await session.refresh(existing)
                        return self._execution_to_dict(existing)

            execution = ExecutionModel(**fields)
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            return self._execution_to_dict(execution)

    async def save_batch(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Persist many execution rows in a single transaction (plan §2.9).

        Replaces N per-call ``save()`` round-trips with one. Each item is
        normalised through the same alias-tolerant logic ``save`` uses
        (legacy ``start_time`` / ``end_time`` / ``duration`` are accepted).
        Items containing an existing integer ``execution_id`` update the
        existing row; new rows are inserted via ``ExecutionModel(**fields)``.

        Returns the list of persisted dicts in input order.
        """
        if not items:
            return []

        normalised: List[Dict[str, Any]] = []
        for d in items:
            started_at = (
                d.get("started_at")
                or self._parse_datetime(d.get("start_time"))
                or datetime.now(timezone.utc)
            )
            completed_at = (
                d.get("completed_at")
                or self._parse_datetime(d.get("end_time"))
            )
            duration_ms = d.get("duration_ms")
            if duration_ms is None and d.get("duration") is not None:
                duration_ms = int(d["duration"] * 1000)
            output = d.get("output") or d.get("result") or d.get("results")
            normalised.append({
                "execution_id": d.get("execution_id") or d.get("id"),
                "fields": dict(
                    workflow_id=d.get("workflow_id"),
                    workflow_name=d["workflow_name"],
                    status=d.get("status", "pending"),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    input=d.get("input"),
                    output=output,
                    error=d.get("error"),
                    logs=d.get("logs"),
                    input_tokens=d.get("input_tokens", 0) or 0,
                    output_tokens=d.get("output_tokens", 0) or 0,
                    total_tokens=d.get("total_tokens", 0) or 0,
                    trajectory=d.get("trajectory"),
                ),
            })

        out: List[Dict[str, Any]] = []
        async with AsyncSessionLocal() as session:
            for n in normalised:
                eid_raw = n["execution_id"]
                eid: Optional[int] = None
                if eid_raw is not None:
                    try:
                        eid = int(eid_raw)
                    except (TypeError, ValueError):
                        eid = None

                existing = None
                if eid is not None:
                    result = await session.execute(
                        select(ExecutionModel).where(ExecutionModel.id == eid)
                    )
                    existing = result.scalar_one_or_none()

                if existing is not None:
                    for key, value in n["fields"].items():
                        if hasattr(existing, key) and value is not None:
                            setattr(existing, key, value)
                else:
                    session.add(ExecutionModel(**n["fields"]))

            # Single commit for the whole batch — one round-trip instead of N.
            await session.commit()

            # Reload to populate generated ids / refreshed timestamps for
            # the caller. Two-stage commit keeps the batch atomic.
            for n in normalised:
                eid_raw = n["execution_id"]
                if eid_raw is None:
                    # New insert — we cannot reliably reload without id;
                    # callers that need round-tripped rows for new inserts
                    # should still use ``save()`` per-item.
                    out.append({**n["fields"]})
                    continue
                try:
                    eid = int(eid_raw)
                except (TypeError, ValueError):
                    out.append({**n["fields"]})
                    continue
                result = await session.execute(
                    select(ExecutionModel).where(ExecutionModel.id == eid)
                )
                row = result.scalar_one_or_none()
                if row is not None:
                    out.append(self._execution_to_dict(row))
                else:
                    out.append({**n["fields"]})

        return out

    async def get_by_workflow_and_id(
        self, workflow_name: str, execution_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get an execution by workflow name and execution ID."""
        execution = await self.get_by_id(execution_id)
        if execution and execution.get("workflow_name") == workflow_name:
            return execution
        return None

    async def delete_by_workflow_and_id(
        self, workflow_name: str, execution_id: str
    ) -> bool:
        """Delete an execution by workflow name and execution ID."""
        execution = await self.get_by_workflow_and_id(workflow_name, execution_id)
        if not execution:
            return False
        return await self.delete(execution_id)

    async def exists(self, execution_id: str) -> bool:
        """Check if an execution exists by ID."""
        return (await self.get_by_id(execution_id)) is not None

    @staticmethod
    def _parse_datetime(value):
        """Parse an ISO-8601 datetime string (accepts trailing 'Z')."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            if isinstance(value, str) and value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    
    async def delete(self, execution_id: str) -> bool:
        """Delete execution.
        
        Args:
            execution_id: Execution ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        try:
            execution_id_int = int(execution_id)
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    delete(ExecutionModel).where(ExecutionModel.id == execution_id_int)
                )
                await session.commit()
                return result.rowcount > 0
        except (ValueError, TypeError):
            logger.error(f"Invalid execution ID: {execution_id}")
            return False
    
    async def delete_by_workflow(self, workflow_name: str) -> int:
        """Delete all executions for a workflow.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Number of executions deleted
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ExecutionModel).where(ExecutionModel.workflow_name == workflow_name)
            )
            await session.commit()
            return result.rowcount
    
    async def delete_all(self) -> int:
        """Delete all executions.
        
        Returns:
            Number of executions deleted
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(delete(ExecutionModel))
            await session.commit()
            return result.rowcount
    
    def _execution_to_dict(self, execution: ExecutionModel) -> Dict[str, Any]:
        """Convert execution model to dictionary."""
        started = execution.started_at.isoformat() if execution.started_at else None
        completed = execution.completed_at.isoformat() if execution.completed_at else None
        duration_secs = (execution.duration_ms / 1000) if execution.duration_ms else None
        return {
            "id": execution.id,
            "execution_id": str(execution.id),      # alias for Dashboard
            "workflow_id": execution.workflow_id,
            "workflow_name": execution.workflow_name,
            "status": execution.status,
            # Both naming conventions for timestamps
            "started_at": started,
            "start_time": started,                   # alias used by Dashboard
            "completed_at": completed,
            "end_time": completed,                    # alias used by Dashboard
            "duration_ms": execution.duration_ms,
            "duration": duration_secs,               # seconds, used by Dashboard
            "input": execution.input,
            # Expose node results under both 'output' (DB name) and 'results'/'result'
            "output": execution.output,
            "results": execution.output,             # alias for _extract_workflow_output
            "result": execution.output,              # alias for Dashboard detail view
            "error": execution.error,
            "logs": execution.logs,
            # Token telemetry — preserved so the UI token panel keeps working
            "input_tokens": getattr(execution, "input_tokens", 0) or 0,
            "output_tokens": getattr(execution, "output_tokens", 0) or 0,
            "total_tokens": getattr(execution, "total_tokens", 0) or 0,
        }