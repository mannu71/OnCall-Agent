"""Execution history API routes."""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Query, Depends

from app.repositories import ExecutionRepository
from app.api.deps import get_execution_repo


router = APIRouter(prefix="/executions", tags=["executions"])


def _extract_workflow_output(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the main workflow output from execution results."""
    # The infrastructure repo stores node results under 'output'; the app-level
    # repo aliases it to both 'results' and 'result'. Support all three.
    results = execution.get('results') or execution.get('result') or execution.get('output') or {}
    
    orchestrator_output = next(
        (v for v in results.values() if isinstance(v, dict) and 'queries_executed' in v),
        None
    )
    
    if orchestrator_output:
        output = {
            'queries_executed': orchestrator_output.get('queries_executed', 0),
            'failures': orchestrator_output.get('failures', 0),
            'results': orchestrator_output.get('results', []),
            'token_usage': orchestrator_output.get('token_usage'),
            'metadata': orchestrator_output.get('metadata'),
        }
        if orchestrator_output.get('error'):
            output['error'] = orchestrator_output['error']
        execution['output'] = output
    
    if not orchestrator_output:
        cloudwatch_output = next(
            (v for v in results.values() if isinstance(v, dict) and 'analysis_type' in v),
            None
        )
        if cloudwatch_output:
            output = {
                'analysis_type': cloudwatch_output.get('analysis_type'),
                'log_groups_analyzed': cloudwatch_output.get('log_groups_analyzed', []),
                'time_range': cloudwatch_output.get('time_range'),
                'results': cloudwatch_output.get('data', {}),
                'output': cloudwatch_output.get('output'),
                'model': cloudwatch_output.get('model'),
                'token_usage': cloudwatch_output.get('token_usage'),
                'metadata': cloudwatch_output.get('metadata'),
            }
            if cloudwatch_output.get('alerts'):
                output['alerts'] = cloudwatch_output['alerts']
            execution['output'] = output

    if not orchestrator_output and not execution.get('output'):
        react_output = next(
            (v for v in results.values() if isinstance(v, dict) and v.get('type') == 'react'),
            None
        )
        if react_output:
            execution['output'] = {
                'type': 'react',
                'final_answer': react_output.get('final_answer'),
                'user_query': react_output.get('user_query'),
                'message_count': react_output.get('message_count', 0),
                'tool_calls': react_output.get('tool_calls', []),
                'model': react_output.get('model'),
                'provider': react_output.get('provider'),
                'token_usage': react_output.get('token_usage'),
                'metadata': react_output.get('metadata'),
                'recall_hits': react_output.get('recall_hits', 0),
            }

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


@router.delete("/active", status_code=status.HTTP_200_OK)
async def clear_active_executions():
    """Clear all stuck/running workflow executions from memory."""
    from app.services.visual_workflow_executor import visual_executor
    
    cleared_count = len(visual_executor.active_executions)
    cleared_names = [
        exec_data.get('workflow_name') 
        for exec_data in visual_executor.active_executions.values()
    ]
    
    # Clean up all executions
    for execution_id in list(visual_executor.active_executions.keys()):
        await visual_executor.cleanup_execution(execution_id)
    
    return {
        "message": f"Cleared {cleared_count} active executions",
        "cleared_count": cleared_count,
        "cleared_workflows": list(filter(None, cleared_names))
    }


@router.delete("/active/{workflow_name}", status_code=status.HTTP_200_OK)
async def cancel_workflow_by_name(workflow_name: str):
    """Cancel/clear a specific workflow by name from active executions."""
    from app.services.visual_workflow_executor import visual_executor
    
    # Find execution IDs matching the workflow name
    execution_ids_to_clear = [
        exec_id for exec_id, exec_data in visual_executor.active_executions.items()
        if exec_data.get('workflow_name') == workflow_name
    ]
    
    if not execution_ids_to_clear:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active execution found for workflow '{workflow_name}'"
        )
    
    # Clean up all matching executions
    for execution_id in execution_ids_to_clear:
        await visual_executor.cleanup_execution(execution_id)
    
    return {
        "message": f"Cancelled {len(execution_ids_to_clear)} execution(s) for workflow '{workflow_name}'",
        "cancelled_count": len(execution_ids_to_clear)
    }


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
