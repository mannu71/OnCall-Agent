"""Database-backed repository for workflows, schedules, and executions."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    WorkflowModel,
    ExecutionModel,
    ScheduleModel,
    LLMConfigModel,
    MCPServerModel
)

logger = logging.getLogger(__name__)


class DatabaseRepository:
    """Database-backed repository for all data access."""

    # ============================================
    # WORKFLOW OPERATIONS
    # ============================================

    async def create_workflow(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new workflow.
        
        Args:
            workflow_data: Workflow data including name, nodes, edges, etc.
            
        Returns:
            Created workflow data
        """
        async with AsyncSessionLocal() as session:
            workflow = WorkflowModel(
                name=workflow_data["name"],
                description=workflow_data.get("description"),
                nodes=workflow_data.get("nodes", []),
                edges=workflow_data.get("edges", []),
                viewport=workflow_data.get("viewport"),
                enabled=workflow_data.get("enabled", True),
                schedule=workflow_data.get("schedule"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            return self._workflow_to_dict(workflow)

    async def get_workflow(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a workflow by name.
        
        Args:
            name: Workflow name
            
        Returns:
            Workflow data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.name == name)
            )
            workflow = result.scalar_one_or_none()
            return self._workflow_to_dict(workflow) if workflow else None

    async def get_workflow_by_id(self, workflow_id: int) -> Optional[Dict[str, Any]]:
        """Get a workflow by ID - direct query.
        
        Args:
            workflow_id: Workflow ID (integer)
            
        Returns:
            Workflow data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            workflow = result.scalar_one_or_none()
            return self._workflow_to_dict(workflow) if workflow else None

    async def list_workflows(self) -> List[Dict[str, Any]]:
        """List all workflows.
        
        Returns:
            List of workflow data
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(WorkflowModel))
            workflows = result.scalars().all()
            return [self._workflow_to_dict(w) for w in workflows]

    async def update_workflow(self, name: str, workflow_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a workflow.
        
        Args:
            name: Workflow name
            workflow_data: Updated workflow data
            
        Returns:
            Updated workflow data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.name == name)
            )
            workflow = result.scalar_one_or_none()
            if not workflow:
                return None

            # Update fields - skip timestamp and id fields that should not be manually set
            skip_fields = {'id', 'created_at', 'updated_at', 'createdAt', 'updatedAt'}
            for key, value in workflow_data.items():
                if key in skip_fields:
                    continue
                if hasattr(workflow, key):
                    setattr(workflow, key, value)
            workflow.updated_at = datetime.now(timezone.utc)
            
            await session.commit()
            await session.refresh(workflow)
            return self._workflow_to_dict(workflow)

    async def delete_workflow(self, name: str) -> bool:
        """Delete a workflow.
        
        Args:
            name: Workflow name
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(WorkflowModel).where(WorkflowModel.name == name).returning(WorkflowModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None

    def _workflow_to_dict(self, workflow: WorkflowModel) -> Dict[str, Any]:
        """Convert workflow model to dictionary."""
        return {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "nodes": workflow.nodes or [],
            "edges": workflow.edges or [],
            "viewport": workflow.viewport,
            "enabled": workflow.enabled,
            "schedule": workflow.schedule,
            "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
            "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None
        }

    # ============================================
    # EXECUTION OPERATIONS
    # ============================================

    async def create_execution(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an execution record.
        
        Args:
            execution_data: Execution data
            
        Returns:
            Created execution data
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

    async def update_execution(self, execution_id: int, execution_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an execution record.
        
        Args:
            execution_id: Execution ID
            execution_data: Updated execution data
            
        Returns:
            Updated execution data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExecutionModel).where(ExecutionModel.id == execution_id)
            )
            execution = result.scalar_one_or_none()
            if not execution:
                return None

            for key, value in execution_data.items():
                if hasattr(execution, key):
                    setattr(execution, key, value)
            
            await session.commit()
            await session.refresh(execution)
            return self._execution_to_dict(execution)

    async def get_execution_by_id(self, execution_id: int) -> Optional[Dict[str, Any]]:
        """Get an execution by ID - direct query.
        
        Args:
            execution_id: Execution ID (integer)
            
        Returns:
            Execution data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExecutionModel).where(ExecutionModel.id == execution_id)
            )
            execution = result.scalar_one_or_none()
            return self._execution_to_dict(execution) if execution else None

    async def list_executions(self, workflow_name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """List executions.
        
        Args:
            workflow_name: Filter by workflow name (optional)
            limit: Maximum number of results
            
        Returns:
            List of execution data
        """
        async with AsyncSessionLocal() as session:
            query = select(ExecutionModel).order_by(ExecutionModel.started_at.desc()).limit(limit)
            if workflow_name:
                query = query.where(ExecutionModel.workflow_name == workflow_name)
            
            result = await session.execute(query)
            executions = result.scalars().all()
            return [self._execution_to_dict(e) for e in executions]

    async def delete_execution(self, execution_id: int) -> bool:
        """Delete an execution by ID.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ExecutionModel).where(ExecutionModel.id == execution_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def delete_all_executions(self) -> int:
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
        return {
            "id": execution.id,
            "workflow_id": execution.workflow_id,
            "workflow_name": execution.workflow_name,
            "status": execution.status,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "duration_ms": execution.duration_ms,
            "input": execution.input,
            "output": execution.output,
            "error": execution.error,
            "logs": execution.logs
        }

    # ============================================
    # SCHEDULE OPERATIONS
    # ============================================

    async def create_schedule(self, schedule_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a schedule.
        
        Args:
            schedule_data: Schedule data
            
        Returns:
            Created schedule data
        """
        async with AsyncSessionLocal() as session:
            schedule = ScheduleModel(
                name=schedule_data["name"],
                workflow_name=schedule_data["workflow_name"],
                cron_expression=schedule_data["cron_expression"],
                timezone=schedule_data.get("timezone", "UTC"),
                enabled=schedule_data.get("enabled", True),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(schedule)
            await session.commit()
            await session.refresh(schedule)
            return self._schedule_to_dict(schedule)

    async def list_schedules(self) -> List[Dict[str, Any]]:
        """List all schedules.
        
        Returns:
            List of schedule data
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ScheduleModel))
            schedules = result.scalars().all()
            return [self._schedule_to_dict(s) for s in schedules]

    async def update_schedule(self, name: str, schedule_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a schedule.
        
        Args:
            name: Schedule name
            schedule_data: Updated schedule data
            
        Returns:
            Updated schedule data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScheduleModel).where(ScheduleModel.name == name)
            )
            schedule = result.scalar_one_or_none()
            if not schedule:
                return None

            for key, value in schedule_data.items():
                if hasattr(schedule, key):
                    setattr(schedule, key, value)
            schedule.updated_at = datetime.now(timezone.utc)
            
            await session.commit()
            await session.refresh(schedule)
            return self._schedule_to_dict(schedule)

    async def delete_schedule(self, name: str) -> bool:
        """Delete a schedule.
        
        Args:
            name: Schedule name
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ScheduleModel).where(ScheduleModel.name == name).returning(ScheduleModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None

    def _schedule_to_dict(self, schedule: ScheduleModel) -> Dict[str, Any]:
        """Convert schedule model to dictionary."""
        return {
            "id": schedule.id,
            "name": schedule.name,
            "workflow_name": schedule.workflow_name,
            "cron_expression": schedule.cron_expression,
            "timezone": schedule.timezone,
            "enabled": schedule.enabled,
            "last_run": schedule.last_run.isoformat() if schedule.last_run else None,
            "next_run": schedule.next_run.isoformat() if schedule.next_run else None,
            "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
            "updated_at": schedule.updated_at.isoformat() if schedule.updated_at else None
        }

    # ============================================
    # LLM CONFIG OPERATIONS
    # ============================================

    async def list_llm_configs(self) -> Dict[str, Dict[str, Any]]:
        """List all LLM configurations.
        
        Returns:
            Dictionary of LLM configurations keyed by name
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(LLMConfigModel))
            configs = result.scalars().all()
            return {
                config.name: {
                    "provider": config.provider,
                    "model": config.model,
                    "endpoint": config.endpoint,
                    "base_url": config.base_url,
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                    "region": config.region,
                    "icon": config.icon,
                    "description": config.description
                }
                for config in configs
            }

    # ============================================
    # MCP SERVER OPERATIONS
    # ============================================

    async def list_mcp_servers(self) -> List[Dict[str, Any]]:
        """List all MCP servers.
        
        Returns:
            List of MCP server configurations
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(MCPServerModel).where(MCPServerModel.enabled == True))
            servers = result.scalars().all()
            return [
                {
                    "name": server.name,
                    "command": server.command,
                    "args": server.args or [],
                    "env": server.env or {},
                    "description": server.description
                }
                for server in servers
            ]


# Singleton instance
db_repository = DatabaseRepository()
