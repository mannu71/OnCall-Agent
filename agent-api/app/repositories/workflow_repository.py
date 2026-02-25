"""Workflow repository for data access with database backend."""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from app.repositories.db_repository import db_repository

logger = logging.getLogger(__name__)


class WorkflowRepository:
    """Repository for workflow data access using PostgreSQL database."""
    
    def __init__(self):
        """Initialize workflow repository."""
        self.db = db_repository
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
            workflow = await self.db.get_workflow_by_id(workflow_id_int)
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
        return await self.db.get_workflow(workflow_name)
    
    async def list_all(self) -> List[Dict[str, Any]]:
        """List all workflows.
        
        Returns:
            List of all workflows
        """
        return await self.db.list_workflows()
    
    async def save(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Save or update a workflow.
        
        Args:
            workflow: Workflow data
            
        Returns:
            Saved workflow
            
        Raises:
            ValueError: If workflow is missing required fields
        """
        if 'name' not in workflow:
            raise ValueError("Workflow must have a 'name' field")
        
        workflow_name = workflow['name']
        existing = await self.db.get_workflow(workflow_name)
        
        if existing:
            # Update existing workflow
            updated = await self.db.update_workflow(workflow_name, workflow)
            logger.info(f"Updated workflow: {workflow_name}")
            return updated
        else:
            # Create new workflow
            created = await self.db.create_workflow(workflow)
            logger.info(f"Created workflow: {workflow_name}")
            return created
    
    async def delete(self, workflow_id: str) -> bool:
        """Delete a workflow by ID or name.
        
        Args:
            workflow_id: Workflow ID or name
            
        Returns:
            True if deleted, False if not found
        """
        # Try to find by name first
        workflow = await self.db.get_workflow(workflow_id)
        
        # If not found, try by ID
        if not workflow:
            try:
                workflow_id_int = int(workflow_id)
                workflow = await self.db.get_workflow_by_id(workflow_id_int)
            except (ValueError, TypeError):
                pass
        
        if not workflow:
            return False
        
        return await self.db.delete_workflow(workflow['name'])
    
    async def exists(self, workflow_id: str) -> bool:
        """Check if a workflow exists by ID or name - direct query.
        
        Args:
            workflow_id: Workflow ID or name
            
        Returns:
            True if exists, False otherwise
        """
        # Try by name first
        workflow = await self.db.get_workflow(workflow_id)
        if workflow:
            return True
        
        # Try by ID
        try:
            workflow_id_int = int(workflow_id)
            workflow = await self.db.get_workflow_by_id(workflow_id_int)
            return workflow is not None
        except (ValueError, TypeError):
            return False
