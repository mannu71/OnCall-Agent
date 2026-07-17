"""Execution history API routes."""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status, Query, Depends, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.infrastructure.persistence import ExecutionRepository
from app.api.deps import get_execution_repo
from app.services.workflow_output_extractor import extract_workflow_output
from app.workflow.event_adapter import execution_event_stream
from app.core.streaming.sse import SSE_HEADERS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/executions", tags=["executions"])


# ─────────────────────────────────────────────────────────────────────────────
# HITL request / response models
# ─────────────────────────────────────────────────────────────────────────────

class HITLApproveRequest(BaseModel):
    request_id: str
    approved: bool = True
    reason: Optional[str] = None
    decided_by: Optional[str] = None
    """Who made the call — defaults to "operator" (human via UI). The Action
    Supervisor sets "supervisor" when it auto-resolves. Recorded in the audit
    trail so supervisor vs human decisions stay distinguishable."""


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
    return [extract_workflow_output(exec) for exec in executions]


@router.get("/active", response_model=List[str])
async def get_active_workflows():
    """Get list of currently running workflow names (DB-backed)."""
    from app.services.execution_state import execution_state

    return await execution_state.list_active_workflow_names()


@router.delete("/active", status_code=status.HTTP_200_OK)
async def clear_active_executions():
    """Clear all stuck/running workflow executions."""
    from app.services.execution_state import execution_state
    from app.services.visual_workflow_executor import visual_executor

    active_before = await execution_state.list_active_records()
    cleared_names = [r.get("workflow_name") for r in active_before]
    cleared_count = await execution_state.cancel_all_active(
        reason="Cleared by operator",
    )

    for execution_id in list(visual_executor.mcp_managers.keys()):
        await visual_executor.cleanup_execution(execution_id)

    return {
        "message": f"Cleared {cleared_count} active executions",
        "cleared_count": cleared_count,
        "cleared_workflows": list(filter(None, cleared_names)),
    }


@router.delete("/active/{workflow_name}", status_code=status.HTTP_200_OK)
async def cancel_workflow_by_name(workflow_name: str):
    """Cancel/clear a specific workflow by name from active executions."""
    from app.services.execution_state import execution_state
    from app.services.visual_workflow_executor import visual_executor

    cancelled_count = await execution_state.cancel_by_workflow_name(workflow_name)
    if cancelled_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active execution found for workflow '{workflow_name}'",
        )

    for execution_id in list(visual_executor.mcp_managers.keys()):
        cached = execution_state.get_cache(execution_id)
        if cached and cached.get("workflow_name") == workflow_name:
            await visual_executor.cleanup_execution(execution_id)

    return {
        "message": f"Cancelled {cancelled_count} execution(s) for workflow '{workflow_name}'",
        "cancelled_count": cancelled_count,
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
    
    return extract_workflow_output(execution)


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

    exec_data = await visual_executor.get_execution_status(execution_id)
    workflow_name = (exec_data or {}).get("workflow_name", execution_id)

    return StreamingResponse(
        execution_event_stream(execution_id, workflow_name),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
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

    exec_data = await visual_executor.get_execution_status(execution_id)
    if exec_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active execution '{execution_id}' found",
        )

    # Tool-approval gates register a per-request_id Future (tool_permissions.py);
    # the supervisor/interrupt path uses the legacy single hitl_queue. Accept
    # either so a single approve call always reaches the right waiter.
    tool_approvals: dict = exec_data.get("tool_approvals") or {}
    hitl_queue: asyncio.Queue | None = exec_data.get("hitl_queue")
    if not tool_approvals and hitl_queue is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution '{execution_id}' is not waiting for HITL approval",
        )

    decision = {
        "request_id": body.request_id,
        "approved": body.approved,
        "reason": body.reason,
        # Human via UI unless the caller (e.g. the Action Supervisor) says otherwise.
        "decided_by": body.decided_by or "operator",
    }
    # Route to the exact gate that published this request_id. With several
    # ask-tool calls pending at once, this guarantees the right one unblocks
    # (and the others keep waiting for their own approval).
    fut = tool_approvals.get(body.request_id)
    if fut is not None and not fut.done():
        fut.set_result(decision)
    elif hitl_queue is not None:
        await hitl_queue.put(decision)
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No pending approval matching request '{body.request_id}'",
        )

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


@router.get("/{execution_id}/approvals")
async def list_tool_approvals(execution_id: str) -> Dict[str, Any]:
    """Return the durable tool-approval audit trail for an execution.

    Reads the ``tool_approvals`` table (written by the permission gate in
    ``tool_permissions.py``) so operators — and the HITL history UI — can see
    every approve / deny / timeout decision, independent of any browser state.
    """
    from app.infrastructure.persistence import tool_approval_repository

    records = await tool_approval_repository.list_for_execution(execution_id)
    return {"execution_id": execution_id, "approvals": records}


@router.get("/approvals/recent")
async def list_recent_tool_approvals(limit: int = 50) -> Dict[str, Any]:
    """Cross-execution supervision audit trail (newest first).

    Surfaces every approve / deny / timeout across executions — including the
    Action Supervisor's own auto-decisions (``decided_by='supervisor'``) and its
    advisory verdicts on human-decided cards — for a single audit view.
    """
    from app.infrastructure.persistence import tool_approval_repository

    records = await tool_approval_repository.list_recent(limit)
    return {"approvals": records, "count": len(records)}


@router.get("/{execution_id}/history")
async def execution_checkpoint_history(execution_id: str) -> Dict[str, Any]:
    """Time-travel: list the LangGraph checkpoints for this execution's thread.

    Backed by the durable ``AsyncPostgresSaver`` (thread_id == execution_id), so
    operators can inspect each step of a run and (later) resume/fork from a given
    checkpoint. Returns newest-first checkpoint metadata + message counts.
    """
    from app.harness.runtime import get_saver

    saver = get_saver()
    if saver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Durable checkpointer unavailable (Postgres persistence not initialised).",
        )
    config = {"configurable": {"thread_id": str(execution_id)}}
    history: List[Dict[str, Any]] = []
    try:
        async for ct in saver.alist(config):
            cp = getattr(ct, "checkpoint", {}) or {}
            meta = getattr(ct, "metadata", {}) or {}
            cfg = getattr(ct, "config", {}) or {}
            msgs = ((cp.get("channel_values") or {}).get("messages")) or []
            history.append({
                "checkpoint_id": (cfg.get("configurable") or {}).get("checkpoint_id"),
                "ts": cp.get("ts"),
                "step": meta.get("step"),
                "source": meta.get("source"),
                "message_count": len(msgs) if isinstance(msgs, list) else None,
            })
    except Exception as exc:  # noqa: BLE001 — history is read-only/diagnostic
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read checkpoint history: {exc}",
        )
    return {"execution_id": execution_id, "checkpoints": history, "count": len(history)}


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

    exec_data = await visual_executor.get_execution_status(execution_id)
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
