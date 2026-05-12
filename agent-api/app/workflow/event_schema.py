"""Canonical event type definitions for the workflow execution pipeline.

Both backend emitters (VisualWorkflowExecutor, ReactStrategy) and frontend
consumers (useWorkflowStream.js) reference these types.  The frontend SSE
event names are the string values of EventType constants.

Wire format (SSE):
    event: <event_type>
    data: {"event_type": "...", "data": {...}, "timestamp": "...Z"}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Event type constants
# ─────────────────────────────────────────────────────────────────────────────

class EventType:
    # Workflow lifecycle
    WORKFLOW_STARTED   = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED    = "workflow_failed"

    # Node lifecycle
    NODE_STARTED   = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED    = "node_failed"

    # Agent streaming (consumed by useWorkflowStream.js)
    LLM_TOKEN      = "llm_token"
    TOOL_CALL      = "tool_call"
    TOOL_RESULT    = "tool_result"
    AGENT_ERROR    = "agent_error"
    AGENT_COMPLETE = "agent_complete"

    # Human-in-the-loop
    HITL_PAUSE    = "hitl_pause"
    HITL_APPROVED = "hitl_approved"
    HITL_REJECTED = "hitl_rejected"

    # Cost tracking
    COST_UPDATE = "cost_update"

    # Stream meta
    STREAM_END = "stream_end"
    HEARTBEAT  = "heartbeat"


# ─────────────────────────────────────────────────────────────────────────────
# Wire-format event model
# ─────────────────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class WorkflowEvent:
    """A single event emitted by the execution pipeline."""

    event_type: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=_utc_now)

    def to_sse(self) -> str:
        """Render as an SSE chunk (event + data lines with double newline)."""
        payload = json.dumps(
            {"event_type": self.event_type, "data": self.data, "timestamp": self.timestamp}
        )
        return f"event: {self.event_type}\ndata: {payload}\n\n"

    def dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Typed constructors — one per event type
# ─────────────────────────────────────────────────────────────────────────────

def workflow_started(
    execution_id: str,
    workflow_name: str,
    workflow_id: Optional[int] = None,
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.WORKFLOW_STARTED,
        {"execution_id": execution_id, "workflow_name": workflow_name, "workflow_id": workflow_id},
    )


def workflow_completed(
    execution_id: str,
    duration: float,
    nodes_executed: int,
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.WORKFLOW_COMPLETED,
        {
            "execution_id": execution_id,
            "status": "success",
            "duration": duration,
            "nodes_executed": nodes_executed,
        },
    )


def workflow_failed(execution_id: str, error: str, duration: float) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.WORKFLOW_FAILED,
        {"execution_id": execution_id, "error": error, "duration": duration},
    )


def node_started(node_id: str, node_type: str, label: str) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.NODE_STARTED,
        {"node_id": node_id, "node_type": node_type, "label": label},
    )


def node_completed(
    node_id: str,
    node_type: str,
    status: str,
    duration: float,
    output: str = "",
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.NODE_COMPLETED,
        {
            "node_id": node_id,
            "node_type": node_type,
            "status": status,
            "duration": duration,
            "output": output,
        },
    )


def node_failed(
    node_id: str, node_type: str, error: str, duration: float
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.NODE_FAILED,
        {"node_id": node_id, "node_type": node_type, "error": error, "duration": duration},
    )


def llm_token(token: str, node_id: str) -> WorkflowEvent:
    return WorkflowEvent(EventType.LLM_TOKEN, {"token": token, "node_id": node_id})


def tool_call_event(
    tool_name: str, args: Dict[str, Any], node_id: str
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.TOOL_CALL, {"tool": tool_name, "args": args, "node_id": node_id}
    )


def tool_result_event(tool_name: str, result: str, node_id: str) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.TOOL_RESULT, {"tool": tool_name, "result": result, "node_id": node_id}
    )


def agent_error(error: str, node_id: str) -> WorkflowEvent:
    return WorkflowEvent(EventType.AGENT_ERROR, {"error": error, "node_id": node_id})


def agent_complete(output: str, node_id: str) -> WorkflowEvent:
    return WorkflowEvent(EventType.AGENT_COMPLETE, {"output": output, "node_id": node_id})


def hitl_pause(
    execution_id: str,
    request_id: str,
    root_cause: str,
    suggestions: List[str],
    tool_name: Optional[str] = None,
    tool_params: Optional[Dict[str, Any]] = None,
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.HITL_PAUSE,
        {
            "execution_id": execution_id,
            "request_id": request_id,
            "root_cause": root_cause,
            "suggestions": suggestions,
            "tool_name": tool_name,
            "tool_params": tool_params,
        },
    )


def hitl_approved(execution_id: str, request_id: str) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.HITL_APPROVED,
        {"execution_id": execution_id, "request_id": request_id},
    )


def hitl_rejected(execution_id: str, request_id: str, reason: str = "") -> WorkflowEvent:
    return WorkflowEvent(
        EventType.HITL_REJECTED,
        {"execution_id": execution_id, "request_id": request_id, "reason": reason},
    )


def cost_update(
    node_id: str, cost_usd: float, total_cost_usd: float, model: str
) -> WorkflowEvent:
    return WorkflowEvent(
        EventType.COST_UPDATE,
        {
            "node_id": node_id,
            "cost_usd": cost_usd,
            "total_cost_usd": total_cost_usd,
            "model": model,
        },
    )
