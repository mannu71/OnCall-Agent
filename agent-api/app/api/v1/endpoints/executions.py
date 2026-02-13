"""Execution history API routes."""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status, Query, Depends

from app.repositories import ExecutionRepository
from app.api.deps import get_execution_repo


router = APIRouter(prefix="/executions", tags=["executions"])


@router.get("", response_model=List[dict])
async def list_executions(
    workflow_name: Optional[str] = Query(None, description="Filter by workflow name"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of executions to return"),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """List execution history."""
    if workflow_name:
        executions = await execution_repo.list_by_workflow(workflow_name, limit=limit)
    else:
        executions = await execution_repo.list_all()
        # Apply limit
        executions = executions[:limit]
    
    return executions


@router.get("/{execution_id}", response_model=dict)
async def get_execution(
    execution_id: str,
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Get a specific execution by ID."""
    execution = await execution_repo.get_by_id(execution_id)
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found"
        )
    
    return execution


@router.delete("/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_execution(
    execution_id: str,
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Delete an execution by ID."""
    success = await execution_repo.delete(execution_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found"
        )
    
    return None
