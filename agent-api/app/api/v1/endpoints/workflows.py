"""Workflow API routes."""
import asyncio
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Query, Depends
from fastapi.responses import StreamingResponse

from app.models.workflow import (
    Workflow,
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowResponse
)
from app.repositories import WorkflowRepository, ExecutionRepository
from app.api.deps import get_workflow_repo, get_execution_repo, verify_workflow_exists
from app.core.scheduler import workflow_scheduler
from app.services.visual_workflow_executor import visual_executor
from app.core.exceptions import NotFoundException

router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger(__name__)


def _get_now_timestamp() -> str:
    """Get current UTC timestamp in ISO format with Z suffix."""
    return datetime.utcnow().isoformat() + 'Z'


def _sync_scheduler_node(workflow_dict: Dict[str, Any], update_data: Optional[Dict[str, Any]] = None) -> None:
    """
    Ensure the workflow level schedule/enabled fields and the scheduler node stay in sync.
    
    If update_data is provided, it first updates the scheduler node from update_data,
    then updates the workflow_dict from the scheduler node.
    """
    if 'nodes' not in workflow_dict or not workflow_dict['nodes']:
        if update_data and 'nodes' in update_data:
            workflow_dict['schedule'] = None
            logger.info("[SYNC] No scheduler node found in new nodes, cleared schedule")
        return

    scheduler_node = next((n for n in workflow_dict['nodes'] if n.get('type') == 'scheduler'), None)
    if not scheduler_node:
        if update_data and 'nodes' in update_data:
            workflow_dict['schedule'] = None
            logger.info("[SYNC] No scheduler node found, cleared schedule")
        return

    if 'data' not in scheduler_node:
        scheduler_node['data'] = {}

    # If this is an update, sync from update_data to scheduler node first
    if update_data:
        if 'schedule' in update_data and update_data['schedule']:
            scheduler_node['data']['cronExpression'] = update_data['schedule']
        if 'enabled' in update_data:
            scheduler_node['data']['enabled'] = update_data['enabled']
        if 'startTime' in update_data and update_data['startTime']:
            scheduler_node['data']['startTime'] = update_data['startTime']
        if 'recurrence' in update_data and update_data['recurrence']:
            scheduler_node['data']['recurrence'] = update_data['recurrence']

    # Sync workflow level fields from scheduler node
    cron_expression = scheduler_node.get('data', {}).get('cronExpression')
    if cron_expression:
        workflow_dict['schedule'] = cron_expression
        workflow_dict['enabled'] = scheduler_node.get('data', {}).get('enabled', True)


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """List all workflows."""
    return await workflow_repo.list_all()


@router.get("/{workflow_name}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_name: str,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Get a specific workflow."""
    workflow = await workflow_repo.get_by_name(workflow_name)
    if not workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    return WorkflowResponse(**workflow)


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    workflow_data: WorkflowCreate,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Create a new workflow."""
    if await workflow_repo.exists(workflow_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow '{workflow_data.name}' already exists"
        )
    
    now = _get_now_timestamp()
    workflow_dict = workflow_data.dict()
    
    # Sync scheduler node and fields
    _sync_scheduler_node(workflow_dict)
    
    # Set timestamps
    workflow_dict['created_at'] = workflow_dict.get('createdAt', now)
    workflow_dict['updated_at'] = workflow_dict.get('updatedAt', now)
    
    saved_workflow = await workflow_repo.save(workflow_dict)
    await workflow_scheduler.reload_workflows()
    
    return WorkflowResponse(**saved_workflow)


@router.put("/{workflow_name}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_name: str,
    workflow_update: WorkflowUpdate,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Update an existing workflow."""
    existing_workflow = await workflow_repo.get_by_name(workflow_name)
    if not existing_workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    update_data = workflow_update.dict(exclude_unset=True)
    workflow_dict = {**existing_workflow, **update_data}
    
    logger.info(f"Updating workflow '{workflow_name}'")
    
    # Sync scheduler node and fields
    _sync_scheduler_node(workflow_dict, update_data)
    
    # Update timestamps
    now = _get_now_timestamp()
    workflow_dict['updated_at'] = now
    if 'updatedAt' in workflow_dict:
        workflow_dict['updatedAt'] = now
    
    # Preserve created timestamps
    for field in ['created_at', 'createdAt']:
        if field in existing_workflow:
            workflow_dict[field] = existing_workflow[field]
    
    saved_workflow = await workflow_repo.save(workflow_dict)
    await workflow_scheduler.reload_workflows()
    
    return WorkflowResponse(**saved_workflow)


@router.delete("/{workflow_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_name: str,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Delete a workflow."""
    if not await workflow_repo.delete(workflow_name):
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    await workflow_scheduler.reload_workflows()
    return None


@router.post("/{workflow_name}/execute", response_model=dict)
async def execute_workflow(
    workflow_name: str,
    background: bool = False,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Manually execute a workflow."""
    workflow = await workflow_repo.get_by_name(workflow_name)
    if not workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    if background:
        asyncio.create_task(visual_executor.execute_workflow(workflow))
        return {
            "status": "started",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' execution started in background"
        }
    
    return await visual_executor.execute_workflow(workflow)


@router.get("/{workflow_name}/stream")
async def stream_workflow_execution(
    workflow_name: str = Depends(verify_workflow_exists),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Stream workflow execution events via SSE."""
    
    async def event_generator():
        workflow = await workflow_repo.get_by_name(workflow_name)
        
        # Start execution and wait slightly for it to register
        asyncio.create_task(visual_executor.execute_workflow(workflow))
        await asyncio.sleep(0.1)
        
        # Find the execution ID
        exec_id = next((eid for eid, data in visual_executor.active_executions.items() 
                       if data.get('workflow_name') == workflow_name), None)
        
        if not exec_id:
            yield f"data: {json.dumps({'error': 'Could not find execution'})}\n\n"
            return
        
        queue = visual_executor.subscribe_to_events(exec_id)
        try:
            yield f"data: {json.dumps({'event': 'connected', 'execution_id': exec_id})}\n\n"
            
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    yield f"data: {json.dumps(event.dict())}\n\n"
                    
                    if event.event_type in ["workflow_completed", "workflow_failed"]:
                        break
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'keepalive', 'timestamp': _get_now_timestamp()})}\n\n"
        finally:
            visual_executor.unsubscribe_from_events(exec_id, queue)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/executions/all", response_model=List[dict])
async def get_all_executions(
    limit: int = Query(50, ge=1, le=500, description="Maximum number of executions to return"),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Get execution history across all workflows."""
    executions = await execution_repo.list_all()
    return executions[:limit]


@router.get("/{workflow_name}/executions", response_model=List[dict])
async def get_workflow_executions(
    limit: int = Query(50, ge=1, le=500, description="Maximum number of executions to return"),
    workflow_name: str = Depends(verify_workflow_exists),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Get execution history for a workflow."""
    return await execution_repo.list_by_workflow(workflow_name, limit=limit)


@router.get("/executions/active", response_model=List[str])
async def get_active_workflows():
    """Get list of currently running workflow names."""
    active = [
        exec_data.get('workflow_name') 
        for exec_data in visual_executor.active_executions.values() 
        if exec_data.get('status') == 'running'
    ]
    # Filter out duplicates (if any) and None values
    return list(set(filter(None, active)))
