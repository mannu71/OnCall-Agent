"""API dependencies for dependency injection."""
from typing import Generator
from fastapi import Depends, HTTPException, status

from app.repositories import WorkflowRepository, ExecutionRepository
from app.core.dependencies import get_workflow_repository, get_execution_repository
from app.workflow.engine import WorkflowEngine
from app.core.scheduler import workflow_scheduler


# Repository dependencies
def get_workflow_repo() -> WorkflowRepository:
    """Get workflow repository dependency.
    
    Returns:
        WorkflowRepository instance
    """
    return get_workflow_repository()


def get_execution_repo() -> ExecutionRepository:
    """Get execution repository dependency.
    
    Returns:
        ExecutionRepository instance
    """
    return get_execution_repository()


# Service dependencies
def get_workflow_engine() -> WorkflowEngine:
    """Get workflow engine instance.
    
    Returns:
        WorkflowEngine instance
    """
    return WorkflowEngine()


def get_scheduler():
    """Get scheduler instance.
    
    Returns:
        Workflow scheduler instance
    """
    return workflow_scheduler


# Common dependencies
async def verify_workflow_exists(
    workflow_name: str,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
) -> str:
    """Verify that a workflow exists.
    
    Args:
        workflow_name: Name of the workflow
        workflow_repo: Workflow repository
        
    Returns:
        Workflow name if exists
        
    Raises:
        HTTPException: If workflow not found
    """
    exists = await workflow_repo.exists(workflow_name)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_name}' not found"
        )
    return workflow_name
