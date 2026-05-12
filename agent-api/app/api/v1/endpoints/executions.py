"""Execution history API routes."""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status, Query, Depends, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.repositories import ExecutionRepository
from app.api.deps import get_execution_repo
from app.workflow.event_adapter import execution_event_stream

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/executions", tags=["executions"])


# ─────────────────────────────────────────────────────────────────────────────
# HITL request / response models
# ─────────────────────────────────────────────────────────────────────────────

class HITLApproveRequest(BaseModel):
    request_id: str
    approved: bool = True
    reason: Optional[str] = None


class HITLApproveResponse(BaseModel):
    execution_id: str
    request_id: str
    approved: bool
    message: str


class SteerRequest(BaseModel):
    note: str
    """Engineer note to inject into the running agent at the next tool boundary."""


class SteerResponse(BaseModel):
    execution_id: str
    queued: bool
    message: str


def _extract_workflow_output(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the main workflow output from execution results."""
    results = execution.get('results') or {}
    
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
                            User Query
                    │
                    ▼
                KB Recall (knowledge_base.search_known_issues / search_similar_patterns)
                    │  prepends matching past resolutions as <memory-context> to the query
                    ▼
                _extract_cloudwatch_config()
                    │  BFS over workflow edges — finds all cloudwatchAnalyzer nodes
                    │  reachable from an agent node, merges their logGroups/region/profile
                    ▼
                _setup_tools()  (MCP tools)
                    │  connects to MCP servers → LangChain BaseTool list
                    ▼
                build_cloudwatch_agent_tools()  [only if CW node is wired up]
                    │  resolves AWS credentials via resolve_aws_credentials()
                    │  wraps 5 async functions as LangChain StructuredTools
                    │  appends them to the tool list
                    ▼
                cloudwatch_context injection (if upstream CW analysis already ran)
                    │  prepends [Pre-computed CloudWatch Analysis] JSON into augmented query
                    ▼
                _build_agent()  → LangGraph create_react_agent StateGraph
                    │  LLM + all tools → ReAct graph
                    ▼
                _execute_agent()  → agent.astream_events()
                    │  ReAct loop: Thought → Tool Call → Observation → repeat → Final Answer
                    ▼
                _auto_learn()  → knowledge_base.record_analysis / add_known_issue    'output': cloudwatch_output.get('output'),
                'model': cloudwatch_output.get('model'),
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


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{execution_id}/stream")
async def stream_execution_events(execution_id: str):
    """Stream live execution events as Server-Sent Events.

    Returns:
        SSE stream of WorkflowEvent objects. Closes after workflow_completed /
        workflow_failed or after the 10-minute hard timeout.
    """
    from app.services.visual_workflow_executor import visual_executor

    exec_data = visual_executor.get_execution_status(execution_id)
    workflow_name = (exec_data or {}).get("workflow_name", execution_id)

    return StreamingResponse(
        execution_event_stream(execution_id, workflow_name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# HITL approve / reject
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{execution_id}/approve", response_model=HITLApproveResponse)
async def approve_hitl_request(
    execution_id: str,
    body: HITLApproveRequest = Body(...),
):
    """Resume a paused HITL execution.

    When the execution engine emits a ``hitl_pause`` event the workflow is
    suspended waiting for human approval.  POST here with ``approved=true``
    (or false to reject) to resume.

    The payload is placed on the execution's HITL queue so the running
    LangGraph coroutine can pick it up and continue (or abort) the graph.
    """
    from app.services.visual_workflow_executor import visual_executor
    from app.workflow.event_schema import hitl_approved, hitl_rejected

    exec_data = visual_executor.get_execution_status(execution_id)
    if exec_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active execution '{execution_id}' found",
        )

    hitl_queue: asyncio.Queue | None = exec_data.get("hitl_queue")
    if hitl_queue is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution '{execution_id}' is not waiting for HITL approval",
        )

    decision = {
        "request_id": body.request_id,
        "approved": body.approved,
        "reason": body.reason,
    }
    await hitl_queue.put(decision)

    # Publish SSE event so the frontend updates immediately
    event = (
        hitl_approved(execution_id, body.request_id)
        if body.approved
        else hitl_rejected(execution_id, body.request_id, reason=body.reason or "")
    )
    await visual_executor._publish_event(execution_id, event.event_type, event.data)

    logger.info(
        "HITL %s for execution %s (request_id=%s)",
        "approved" if body.approved else "rejected",
        execution_id,
        body.request_id,
    )

    return HITLApproveResponse(
        execution_id=execution_id,
        request_id=body.request_id,
        approved=body.approved,
        message="Decision recorded — execution will resume shortly."
        if body.approved
        else "Execution rejected — workflow will be stopped.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /steer — mid-run engineer note injection
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{execution_id}/steer", response_model=SteerResponse)
async def steer_execution(
    execution_id: str,
    body: SteerRequest = Body(...),
):
    """Inject an engineer note into a running execution at the next tool boundary.

    Unlike the approve endpoint (which resumes a *paused* execution), steer
    queues a note for a **running** execution.  The ReAct agent picks it up
    as a HumanMessage at the next tool boundary without stopping or restarting
    the investigation.

    This is the in-context correction channel for running investigations.
    """
    from app.services.visual_workflow_executor import visual_executor

    exec_data = visual_executor.get_execution_status(execution_id)
    if exec_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active execution '{execution_id}' found",
        )

    if exec_data.get("status") != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution '{execution_id}' is not currently running (status={exec_data.get('status')})",
        )

    # Append to the steer_notes list — consumed by ReactStrategy._execute_agent_stream
    steer_notes: list = exec_data.setdefault("steer_notes", [])
    steer_notes.append(body.note)

    # Notify the frontend via SSE so the operator sees the note was registered.
    await visual_executor._publish_event(
        execution_id,
        "steer_queued",
        {"note": body.note[:500], "queue_depth": len(steer_notes)},
    )

    logger.info(
        "Steer note queued for execution %s (queue_depth=%d)",
        execution_id,
        len(steer_notes),
    )

    return SteerResponse(
        execution_id=execution_id,
        queued=True,
        message=f"Note queued — will be injected at the next tool boundary (queue depth: {len(steer_notes)}).",
    )
