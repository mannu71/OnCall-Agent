"""API dependencies for dependency injection."""
import warnings
from typing import Generator
from fastapi import Depends, HTTPException, status

from app.infrastructure.persistence import WorkflowRepository, ExecutionRepository
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


def get_llm_config_repo():
    """Get LLM configuration repository dependency.

    Returns:
        Shared ``LLMConfigRepository`` singleton.
    """
    from app.infrastructure.persistence import llm_config_repository
    return llm_config_repository


# Service dependencies
def get_workflow_engine() -> WorkflowEngine:
    """Get workflow engine instance.

    .. deprecated::
        Do not use for visual workflows. Use ``app.workflow.routing.execute_workflow``
        or ``visual_executor`` instead. ``WorkflowEngine`` rejects workflows with
        ``nodes`` and is retained only for legacy internal use.

    Returns:
        WorkflowEngine instance
    """
    warnings.warn(
        "get_workflow_engine() is deprecated for visual workflows. "
        "Use app.workflow.routing.execute_workflow() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
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
