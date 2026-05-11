"""API dependencies for dependency injection."""
from typing import Generator
from fastapi import Depends, HTTPException, status

from app.infrastructure.persistence import (
    WorkflowRepository,
    ExecutionRepository,
    LLMConfigRepository,
    MCPConfigRepository,
    ModelKeyRepository,
)
from app.workflow.engine import WorkflowEngine
from app.core.scheduler import workflow_scheduler


# --------------------------------------------------------------------------- #
# Repository singletons (kept in-memory for the process lifetime)
# --------------------------------------------------------------------------- #

_workflow_repo: WorkflowRepository | None = None
_execution_repo: ExecutionRepository | None = None
_llm_config_repo: LLMConfigRepository | None = None
_mcp_config_repo: MCPConfigRepository | None = None
_model_key_repo: ModelKeyRepository | None = None


# --------------------------------------------------------------------------- #
# Repository dependencies
# --------------------------------------------------------------------------- #

def get_workflow_repo() -> WorkflowRepository:
    """Get workflow repository dependency."""
    global _workflow_repo
    if _workflow_repo is None:
        _workflow_repo = WorkflowRepository()
    return _workflow_repo


def get_execution_repo() -> ExecutionRepository:
    """Get execution repository dependency."""
    global _execution_repo
    if _execution_repo is None:
        _execution_repo = ExecutionRepository()
    return _execution_repo


def get_llm_config_repo() -> LLMConfigRepository:
    """Get LLM config repository dependency."""
    global _llm_config_repo
    if _llm_config_repo is None:
        _llm_config_repo = LLMConfigRepository()
    return _llm_config_repo


def get_mcp_config_repo() -> MCPConfigRepository:
    """Get MCP config repository dependency."""
    global _mcp_config_repo
    if _mcp_config_repo is None:
        _mcp_config_repo = MCPConfigRepository()
    return _mcp_config_repo


def get_model_key_repo() -> ModelKeyRepository:
    """Get model key repository dependency."""
    global _model_key_repo
    if _model_key_repo is None:
        _model_key_repo = ModelKeyRepository()
    return _model_key_repo


# --------------------------------------------------------------------------- #
# Service dependencies
# --------------------------------------------------------------------------- #

def get_workflow_engine() -> WorkflowEngine:
    """Get workflow engine instance."""
    return WorkflowEngine()


def get_scheduler():
    """Get scheduler instance."""
    return workflow_scheduler


# --------------------------------------------------------------------------- #
# Common dependencies
# --------------------------------------------------------------------------- #

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