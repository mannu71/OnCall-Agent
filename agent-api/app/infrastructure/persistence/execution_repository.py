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
        """Save execution record.
        
        Args:
            execution_data: Execution data to save
            
        Returns:
            Saved execution data
        """
        async with AsyncSessionLocal() as session:
            execution = ExecutionModel(
                workflow_id=execution_data.get("workflow_id"),
                workflow_name=execution_data["workflow_name"],
                status=execution_data.get("status", "pending"),
                started_at=execution_data.get("started_at", datetime.now(timezone.utc)),
                completed_at=execution_data.get("completed_at"),
                duration_ms=execution_data.get("duration_ms"),
                input=execution_data.get("input"),
                output=execution_data.get("output"),
                error=execution_data.get("error"),
                logs=execution_data.get("logs")
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            return self._execution_to_dict(execution)
    
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
            "logs": execution.logs
        }