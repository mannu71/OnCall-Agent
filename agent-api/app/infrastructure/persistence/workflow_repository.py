"""Workflow repository for workflow data access."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models.db_models import WorkflowModel

logger = logging.getLogger(__name__)


class WorkflowRepository:
    """Repository for workflow data access using PostgreSQL database."""
    
    def __init__(self):
        """Initialize workflow repository."""
        logger.info("WorkflowRepository initialized with database backend")
    
    async def get_by_id(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow by ID - direct query.
        
        Args:
            workflow_id: Workflow ID (integer as string)
            
        Returns:
            Workflow if found, None otherwise
        """
        try:
            workflow_id_int = int(workflow_id)
            workflow = await self.get_workflow_by_id(workflow_id_int)
            return workflow
        except (ValueError, TypeError):
            # If not an integer ID, try by name
            return await self.get_by_name(workflow_id)
    
    async def get_by_name(self, workflow_name: str) -> Optional[Dict[str, Any]]:
        """Get workflow by name.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Workflow if found, None otherwise
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.name == workflow_name)
            )
            workflow = result.scalar_one_or_none()
            return self._workflow_to_dict(workflow) if workflow else None
    
    async def list_all(self) -> List[Dict[str, Any]]:
        """List all workflows.
        
        Returns:
            List of all workflows
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(WorkflowModel))
            workflows = result.scalars().all()
            return [self._workflow_to_dict(w) for w in workflows]
    
    async def save(self, workflow_data: Dict[str, Any], original_name: Optional[str] = None) -> Dict[str, Any]:
        """Save or update workflow.
        
        Args:
            workflow_data: Workflow data to save
            original_name: Original name for rename detection
            
        Returns:
            Saved workflow data
        """
        name = original_name or workflow_data.get("name")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.name == name)
            )
            workflow = result.scalar_one_or_none()
            
            if workflow:
                # Update existing
                for key, value in workflow_data.items():
                    if hasattr(workflow, key) and key not in ('id', 'created_at'):
                        setattr(workflow, key, value)
                workflow.updated_at = datetime.now(timezone.utc)
            else:
                # Create new
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
    
    async def delete(self, workflow_name: str) -> bool:
        """Delete workflow.
        
        Args:
            workflow_name: Name of workflow to delete
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(WorkflowModel).where(WorkflowModel.name == workflow_name).returning(WorkflowModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None
    
    async def exists(self, workflow_name: str) -> bool:
        """Check if workflow exists.
        
        Args:
            workflow_name: Name to check
            
        Returns:
            True if exists
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel.id).where(WorkflowModel.name == workflow_name)
            )
            return result.scalar_one_or_none() is not None
    
    async def get_workflow_by_id(self, workflow_id: int) -> Optional[Dict[str, Any]]:
        """Get workflow by ID.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            Workflow if found, None otherwise
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            workflow = result.scalar_one_or_none()
            return self._workflow_to_dict(workflow) if workflow else None
    
    async def get_sql_files_for_workflow(self, workflow_name: str) -> List[str]:
        """Get SQL files for a workflow.
        
        Args:
            workflow_name: Name of the workflow
            
        Returns:
            List of SQL filenames
        """
        workflow = await self.get_by_name(workflow_name)
        if not workflow or not workflow.get("nodes"):
            return []
        
        sql_files = []
        for node in workflow.get("nodes", []):
            if node.get("type") == "orchestrator":
                node_data = node.get("data", {})
                if "fileName" in node_data:
                    sql_files.append(node_data["fileName"])
        return sql_files
    
    async def is_sql_file_used_by_other_workflows(self, sql_file: str, workflow_name: str) -> bool:
        """Check if SQL file is used by other workflows.
        
        Args:
            sql_file: SQL filename
            workflow_name: Name of current workflow (to exclude)
            
        Returns:
            True if used by other workflows
        """
        all_workflows = await self.list_all()
        for wf in all_workflows:
            if wf["name"] == workflow_name:
                continue
            for node in wf.get("nodes", []):
                if node.get("type") == "orchestrator":
                    node_data = node.get("data", {})
                    if node_data.get("fileName") == sql_file:
                        return True
        return False
    
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