"""Workflow API routes."""
import asyncio
import json
from datetime import datetime
from typing import List
from fastapi import APIRouter, HTTPException, status, Query, Depends
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from app.models.workflow import (
    Workflow,
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowResponse,
    WorkflowExecution
)
from app.repositories import WorkflowRepository, ExecutionRepository
from app.api.deps import get_workflow_repo, get_execution_repo, verify_workflow_exists
from app.core.scheduler import workflow_scheduler
from app.services.visual_workflow_executor import visual_executor
from app.core.exceptions import NotFoundException, ValidationException


router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """List all workflows."""
    workflows = await workflow_repo.list_all()
    
    # Convert to response model
    response = []
    for workflow in workflows:
        response.append(WorkflowResponse(**workflow))
    
    return response


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
    # Check if workflow already exists
    exists = await workflow_repo.exists(workflow_data.name)
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow '{workflow_data.name}' already exists"
        )
    
    # Set timestamps
    now = datetime.utcnow().isoformat() + 'Z'
    workflow_dict = workflow_data.dict()
    
    # Extract cron schedule from scheduler nodes (for scheduler system)
    if 'nodes' in workflow_dict and workflow_dict['nodes']:
        scheduler_node = next((n for n in workflow_dict['nodes'] if n.get('type') == 'scheduler'), None)
        if scheduler_node and scheduler_node.get('data', {}).get('cronExpression'):
            workflow_dict['schedule'] = scheduler_node['data']['cronExpression']
            # Set enabled based on scheduler node if not explicitly set
            if 'enabled' not in workflow_dict or workflow_dict['enabled'] is None:
                workflow_dict['enabled'] = scheduler_node['data'].get('enabled', True)
    
    # Handle both timestamp formats
    if 'createdAt' in workflow_dict:
        workflow_dict['created_at'] = workflow_dict.get('createdAt', now)
        workflow_dict['updated_at'] = workflow_dict.get('updatedAt', now)
    else:
        workflow_dict['created_at'] = now
        workflow_dict['updated_at'] = now
    
    # Save to repository
    saved_workflow = await workflow_repo.save(workflow_dict)
    
    return WorkflowResponse(**saved_workflow)


@router.put("/{workflow_name}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_name: str,
    workflow_update: WorkflowUpdate,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Update an existing workflow."""
    # Load existing workflow
    existing_workflow = await workflow_repo.get_by_name(workflow_name)
    
    if not existing_workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    # Update fields
    update_data = workflow_update.dict(exclude_unset=True)
    workflow_dict = existing_workflow.copy()
    workflow_dict.update(update_data)
    
    # Extract cron schedule from scheduler nodes (for scheduler system)
    if 'nodes' in workflow_dict and workflow_dict['nodes']:
        scheduler_node = next((n for n in workflow_dict['nodes'] if n.get('type') == 'scheduler'), None)
        if scheduler_node and scheduler_node.get('data', {}).get('cronExpression'):
            workflow_dict['schedule'] = scheduler_node['data']['cronExpression']
            # Update enabled based on scheduler node if not explicitly set in update
            if 'enabled' not in update_data:
                workflow_dict['enabled'] = scheduler_node['data'].get('enabled', True)
        elif 'nodes' in update_data:
            # If nodes were updated but no scheduler found, clear schedule
            workflow_dict['schedule'] = None
    
    # Update timestamp
    now = datetime.utcnow().isoformat() + 'Z'
    workflow_dict['updated_at'] = now
    if 'updatedAt' in workflow_dict:
        workflow_dict['updatedAt'] = now
    
    # Preserve created timestamp
    if 'created_at' in existing_workflow:
        workflow_dict['created_at'] = existing_workflow['created_at']
    if 'createdAt' in existing_workflow:
        workflow_dict['createdAt'] = existing_workflow['createdAt']
    
    # Save to repository
    saved_workflow = await workflow_repo.save(workflow_dict)
    
    return WorkflowResponse(**saved_workflow)


@router.delete("/{workflow_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_name: str,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Delete a workflow."""
    # Check if exists
    exists = await workflow_repo.exists(workflow_name)
    if not exists:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    # Delete from repository
    success = await workflow_repo.delete(workflow_name)
    
    if not success:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    return None


@router.post("/{workflow_name}/execute", response_model=dict)
async def execute_workflow(
    workflow_name: str,
    background: bool = False,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Manually execute a workflow."""
    # Check if workflow exists
    workflow = await workflow_repo.get_by_name(workflow_name)
    
    if not workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    if background:
        # Execute in background
        asyncio.create_task(visual_executor.execute_workflow(workflow))
        return {
            "status": "started",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' execution started in background"
        }
    else:
        # Execute and wait for completion
        result = await visual_executor.execute_workflow(workflow)
        return result


@router.get("/{workflow_name}/stream")
async def stream_workflow_execution(
    workflow_name: str,
    execution_id: str = None,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Stream workflow execution events via SSE."""
    # Check if workflow exists
    exists = await workflow_repo.exists(workflow_name)
    if not exists:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    async def event_generator():
        # Get workflow
        workflow = await workflow_repo.get_by_name(workflow_name)
        
        # Start execution
        execution_task = asyncio.create_task(visual_executor.execute_workflow(workflow))
        
        # Get the execution_id from the executor
        await asyncio.sleep(0.1)  # Small delay to let execution start
        
        # Find the most recent execution for this workflow
        recent_execution_id = None
        for exec_id, exec_data in visual_executor.active_executions.items():
            if exec_data.get('workflow_name') == workflow_name:
                recent_execution_id = exec_id
                break
        
        if not recent_execution_id:
            error_msg = json.dumps({'error': 'Could not find execution'})
            yield f"data: {error_msg}\n\n"
            return
        
        # Subscribe to events
        queue = visual_executor.subscribe_to_events(recent_execution_id)
        
        try:
            # Send initial connection message
            connected_msg = json.dumps({'event': 'connected', 'execution_id': recent_execution_id})
            yield f"data: {connected_msg}\n\n"
            
            while True:
                # Wait for event with timeout
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    event_data = json.dumps(event.dict())
                    yield f"data: {event_data}\n\n"
                    
                    # Close stream on workflow complete or failed
                    if event.event_type in ["workflow_completed", "workflow_failed"]:
                        break
                
                except asyncio.TimeoutError:
                    # Send keepalive
                    keepalive_msg = json.dumps({'event': 'keepalive', 'timestamp': datetime.utcnow().isoformat()})
                    yield f"data: {keepalive_msg}\n\n"
        
        finally:
            # Unsubscribe on disconnect
            visual_executor.unsubscribe_from_events(recent_execution_id, queue)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{workflow_name}/executions", response_model=List[dict])
async def get_workflow_executions(
    workflow_name: str,
    limit: int = Query(50, ge=1, le=500, description="Maximum number of executions to return"),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Get execution history for a workflow."""
    # Check if workflow exists
    exists = await workflow_repo.exists(workflow_name)
    if not exists:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    # Get execution logs from execution repository
    executions = await execution_repo.list_by_workflow(workflow_name, limit=limit)
    
    return executions
