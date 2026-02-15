"""Execution history API routes."""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Query, Depends

from app.repositories import ExecutionRepository
from app.api.deps import get_execution_repo


router = APIRouter(prefix="/executions", tags=["executions"])


def _extract_workflow_output(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the main workflow output from execution results."""
    results = execution.get('results', {})
    
    orchestrator_output = next(
        (v for v in results.values() if isinstance(v, dict) and 'queries_executed' in v),
        None
    )
    
    if orchestrator_output:
        output = {
            'queries_executed': orchestrator_output.get('queries_executed', 0),
            'failures': orchestrator_output.get('failures', 0),
            'results': orchestrator_output.get('results', [])
        }
        if orchestrator_output.get('error'):
            output['error'] = orchestrator_output['error']
        execution['output'] = output
    
    return execution


@router.get("", response_model=List[dict])
async def list_executions(
    workflow_name: Optional[str] = Query(None, description="Filter by workflow name"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of executions to return"),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """List execution history with workflow outputs."""
    if workflow_name:
        executions = await execution_repo.list_by_workflow(workflow_name, limit=limit)
    else:
        executions = await execution_repo.list_all()
        # Apply limit
        executions = executions[:limit]
    
    # Add output field to each execution for easy access
    return [_extract_workflow_output(exec) for exec in executions]


@router.get("/active", response_model=List[str])
async def get_active_workflows():
    """Get list of currently running workflow names."""
    from app.services.visual_workflow_executor import visual_executor
    
    active = [
        exec_data.get('workflow_name') 
        for exec_data in visual_executor.active_executions.values() 
        if exec_data.get('status') == 'running'
    ]
    # Filter out duplicates (if any) and None values
    return list(set(filter(None, active)))


@router.get("/{execution_id}", response_model=dict)
async def get_execution(
    execution_id: str,
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Get a specific execution by ID with workflow output."""
    execution = await execution_repo.get_by_id(execution_id)
    
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution '{execution_id}' not found"
        )
    
    return _extract_workflow_output(execution)


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


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_all_executions(
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Delete all execution history."""
    deleted_count = await execution_repo.delete_all()
    return {"message": f"Deleted {deleted_count} executions", "deleted_count": deleted_count}
