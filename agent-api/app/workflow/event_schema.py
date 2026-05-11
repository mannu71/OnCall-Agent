"""
Canonical SSE Event Schema for Visual Workflow Execution

This module defines the contract between the backend streaming system and the
frontend React Flow canvas. All workflow execution events are normalized to
this schema before being sent over SSE.

Event Types:
-----------
1. Workflow-level events:
   - workflow_started: Execution begins
   - workflow_completed: Execution finished successfully
   - workflow_failed: Execution failed

2. Node-level events (React Flow compatible):
   - node_start: Node execution begins
   - node_update: Node execution progress update
   - node_done: Node execution completed
   - node_error: Node execution failed

3. Agent streaming events:
   - agent_token: Real-time LLM token output
   - agent_tool_call: Agent invoking a tool
   - agent_tool_result: Tool execution result
   - agent_error: Agent error
   - agent_complete: Agent finished

4. System events:
   - keepalive: Connection keep-alive ping
   - error: System-level error

Schema Structure:
----------------
All events follow this structure:
{
    "event_type": str,      # Event type from above
    "timestamp": str,       # ISO 8601 timestamp
    "data": {               # Event-specific payload
        ...
    }
}

React Flow Node Events:
----------------------
node_start: {
    "nodeId": str,          # React Flow node ID
    "nodeType": str,        # Node type (agent, llm, tool, etc.)
    "label": str            # Human-readable node label
}

node_update: {
    "nodeId": str,
    "status": str,          # "running", "processing", etc.
    "progress": float,      # Optional: 0.0 to 1.0
    "message": str          # Optional: status message
}

node_done: {
    "nodeId": str,
    "status": str,          # "success" or "failed"
    "output": str,          # Node output (truncated)
    "duration": float       # Execution time in seconds
}

node_error: {
    "nodeId": str,
    "error": str,           # Error message
    "duration": float       # Time until error
}
"""
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field


# Event type literals for type safety
WorkflowEventType = Literal[
    "workflow_started",
    "workflow_completed",
    "workflow_failed"
]

NodeEventType = Literal[
    "node_start",
    "node_update",
    "node_done",
    "node_error"
]

AgentEventType = Literal[
    "agent_token",
    "agent_tool_call",
    "agent_tool_result",
    "agent_error",
    "agent_complete"
]

SystemEventType = Literal[
    "keepalive",
    "error"
]

EventType = Literal[
    # Workflow events
    "workflow_started",
    "workflow_completed",
    "workflow_failed",
    # Node events (React Flow compatible)
    "node_start",
    "node_update",
    "node_done",
    "node_error",
    # Agent streaming events
    "agent_token",
    "agent_tool_call",
    "agent_tool_result",
    "agent_error",
    "agent_complete",
    # System events
    "keepalive",
    "error"
]


class WorkflowEvent(BaseModel):
    """Base event model for all workflow execution events."""
    event_type: EventType
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    data: Dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """Convert to SSE format string."""
        import json
        # Use event: prefix for agent streaming events (allows client-side filtering)
        if self.event_type.startswith("agent_"):
            return f"event: {self.event_type}\ndata: {json.dumps(self.model_dump())}\n\n"
        return f"data: {json.dumps(self.model_dump())}\n\n"


# Workflow-level events
class WorkflowStartedEvent(WorkflowEvent):
    """Workflow execution started."""
    event_type: Literal["workflow_started"] = "workflow_started"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: execution_id, workflow_name, workflow_id"
    )


class WorkflowCompletedEvent(WorkflowEvent):
    """Workflow execution completed successfully."""
    event_type: Literal["workflow_completed"] = "workflow_completed"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: execution_id, status, duration, nodes_executed"
    )


class WorkflowFailedEvent(WorkflowEvent):
    """Workflow execution failed."""
    event_type: Literal["workflow_failed"] = "workflow_failed"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: execution_id, error, duration"
    )


# Node-level events (React Flow compatible)
class NodeStartEvent(WorkflowEvent):
    """Node execution started."""
    event_type: Literal["node_start"] = "node_start"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: nodeId, nodeType, label"
    )


class NodeUpdateEvent(WorkflowEvent):
    """Node execution progress update."""
    event_type: Literal["node_update"] = "node_update"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: nodeId, status, progress (optional), message (optional)"
    )


class NodeDoneEvent(WorkflowEvent):
    """Node execution completed."""
    event_type: Literal["node_done"] = "node_done"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: nodeId, status, output, duration"
    )


class NodeErrorEvent(WorkflowEvent):
    """Node execution failed."""
    event_type: Literal["node_error"] = "node_error"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: nodeId, error, duration"
    )


# Agent streaming events
class AgentTokenEvent(WorkflowEvent):
    """Real-time LLM token output."""
    event_type: Literal["agent_token"] = "agent_token"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: token, node_id"
    )


class AgentToolCallEvent(WorkflowEvent):
    """Agent invoking a tool."""
    event_type: Literal["agent_tool_call"] = "agent_tool_call"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: tool, args, node_id, preview (optional)"
    )


class AgentToolResultEvent(WorkflowEvent):
    """Tool execution result."""
    event_type: Literal["agent_tool_result"] = "agent_tool_result"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: tool, result, node_id"
    )


class AgentErrorEvent(WorkflowEvent):
    """Agent error."""
    event_type: Literal["agent_error"] = "agent_error"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: error, node_id, classified (optional)"
    )


class AgentCompleteEvent(WorkflowEvent):
    """Agent finished."""
    event_type: Literal["agent_complete"] = "agent_complete"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: output, node_id, metadata (optional)"
    )


# System events
class KeepaliveEvent(WorkflowEvent):
    """Connection keep-alive ping."""
    event_type: Literal["keepalive"] = "keepalive"
    data: Dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(WorkflowEvent):
    """System-level error."""
    event_type: Literal["error"] = "error"
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contains: error, context (optional)"
    )


# Event factory
def create_event(event_type: EventType, data: Dict[str, Any]) -> WorkflowEvent:
    """Create a typed event from event_type and data.
    
    Args:
        event_type: Event type string
        data: Event payload
        
    Returns:
        Typed WorkflowEvent instance
    """
    event_map = {
        "workflow_started": WorkflowStartedEvent,
        "workflow_completed": WorkflowCompletedEvent,
        "workflow_failed": WorkflowFailedEvent,
        "node_start": NodeStartEvent,
        "node_update": NodeUpdateEvent,
        "node_done": NodeDoneEvent,
        "node_error": NodeErrorEvent,
        "agent_token": AgentTokenEvent,
        "agent_tool_call": AgentToolCallEvent,
        "agent_tool_result": AgentToolResultEvent,
        "agent_error": AgentErrorEvent,
        "agent_complete": AgentCompleteEvent,
        "keepalive": KeepaliveEvent,
        "error": ErrorEvent,
    }
    
    event_class = event_map.get(event_type, WorkflowEvent)
    return event_class(event_type=event_type, data=data)
