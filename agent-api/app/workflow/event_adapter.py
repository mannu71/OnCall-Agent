"""
Event Adapter: Normalize LangGraph events to React Flow canvas format

This adapter sits between the LangGraph execution engine and the SSE streaming
endpoint, translating internal execution events to the canonical event schema
that the React Flow frontend expects.

Mapping:
-------
LangGraph Internal Events → Canonical Schema Events

1. node_started → node_start
   - Extracts nodeId, nodeType, label from node data
   
2. node_completed → node_done
   - Adds duration, output, status
   
3. node_failed → node_error
   - Adds error message, duration
   
4. llm_token → agent_token
   - Preserves token and node_id
   
5. tool_call → agent_tool_call
   - Preserves tool, args, node_id
   
6. tool_result → agent_tool_result
   - Preserves tool, result, node_id

Usage:
-----
```python
from app.workflow.event_adapter import EventAdapter

adapter = EventAdapter()

# Normalize internal event to canonical format
internal_event = {
    "event_type": "node_started",
    "data": {"node_id": "agent-1", "node_type": "agent", "label": "AI Agent"}
}

canonical_event = adapter.normalize(internal_event)
# Returns: NodeStartEvent with data: {"nodeId": "agent-1", "nodeType": "agent", "label": "AI Agent"}
```
"""
from typing import Any, Dict
from datetime import datetime, timezone

from app.workflow.event_schema import (
    WorkflowEvent,
    create_event,
    EventType,
)


class EventAdapter:
    """Adapter to normalize internal events to canonical schema."""
    
    def __init__(self):
        """Initialize event adapter."""
        self._event_mapping = {
            # Workflow events (already canonical)
            "workflow_started": self._passthrough,
            "workflow_completed": self._passthrough,
            "workflow_failed": self._passthrough,
            
            # Node events (need normalization)
            "node_started": self._normalize_node_start,
            "node_completed": self._normalize_node_done,
            "node_failed": self._normalize_node_error,
            
            # Agent events (already canonical)
            "llm_token": self._normalize_agent_token,
            "tool_call": self._normalize_agent_tool_call,
            "tool_result": self._normalize_agent_tool_result,
            "agent_error": self._passthrough,
            "agent_complete": self._passthrough,
            
            # System events (already canonical)
            "keepalive": self._passthrough,
            "error": self._passthrough,
        }
    
    def normalize(self, internal_event: Dict[str, Any]) -> WorkflowEvent:
        """Normalize an internal event to canonical schema.
        
        Args:
            internal_event: Internal event dict with event_type and data
            
        Returns:
            Canonical WorkflowEvent instance
        """
        event_type = internal_event.get("event_type")
        data = internal_event.get("data", {})
        timestamp = internal_event.get("timestamp")
        
        # Get normalizer function for this event type
        normalizer = self._event_mapping.get(event_type, self._passthrough)
        
        # Normalize the data
        normalized_data = normalizer(data)
        
        # Create canonical event
        canonical_event = create_event(
            event_type=self._map_event_type(event_type),
            data=normalized_data
        )
        
        # Preserve original timestamp if provided
        if timestamp:
            canonical_event.timestamp = timestamp
        
        return canonical_event
    
    def _map_event_type(self, internal_type: str) -> EventType:
        """Map internal event type to canonical event type.
        
        Args:
            internal_type: Internal event type string
            
        Returns:
            Canonical EventType
        """
        mapping = {
            "node_started": "node_start",
            "node_completed": "node_done",
            "node_failed": "node_error",
            "tool_call": "agent_tool_call",
            "tool_result": "agent_tool_result",
            "llm_token": "agent_token",
        }
        return mapping.get(internal_type, internal_type)
    
    def _passthrough(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Pass through data unchanged (already canonical)."""
        return data
    
    def _normalize_node_start(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize node_started event to React Flow format.
        
        Internal format:
            {"node_id": "agent-1", "node_type": "agent", "label": "AI Agent"}
            
        Canonical format:
            {"nodeId": "agent-1", "nodeType": "agent", "label": "AI Agent"}
        """
        return {
            "nodeId": data.get("node_id"),
            "nodeType": data.get("node_type"),
            "label": data.get("label", data.get("node_type", "Unknown"))
        }
    
    def _normalize_node_done(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize node_completed event to React Flow format.
        
        Internal format:
            {
                "node_id": "agent-1",
                "node_type": "agent",
                "status": "success",
                "duration": 12.5,
                "output": "Task completed"
            }
            
        Canonical format:
            {
                "nodeId": "agent-1",
                "status": "success",
                "output": "Task completed",
                "duration": 12.5
            }
        """
        return {
            "nodeId": data.get("node_id"),
            "status": data.get("status", "success"),
            "output": data.get("output", ""),
            "duration": data.get("duration", 0.0)
        }
    
    def _normalize_node_error(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize node_failed event to React Flow format.
        
        Internal format:
            {
                "node_id": "agent-1",
                "node_type": "agent",
                "error": "Connection timeout",
                "duration": 5.2
            }
            
        Canonical format:
            {
                "nodeId": "agent-1",
                "error": "Connection timeout",
                "duration": 5.2
            }
        """
        return {
            "nodeId": data.get("node_id"),
            "error": data.get("error", "Unknown error"),
            "duration": data.get("duration", 0.0)
        }
    
    def _normalize_agent_token(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize llm_token event (already mostly canonical).
        
        Ensures node_id is present for frontend routing.
        """
        return {
            "token": data.get("token", ""),
            "node_id": data.get("node_id", "unknown")
        }
    
    def _normalize_agent_tool_call(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize tool_call event (already mostly canonical).
        
        Ensures all required fields are present.
        """
        return {
            "tool": data.get("tool", "unknown"),
            "args": data.get("args", {}),
            "node_id": data.get("node_id", "unknown"),
            "preview": data.get("preview")  # Optional
        }
    
    def _normalize_agent_tool_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize tool_result event (already mostly canonical).
        
        Ensures all required fields are present.
        """
        return {
            "tool": data.get("tool", "unknown"),
            "result": data.get("result", ""),
            "node_id": data.get("node_id", "unknown")
        }


# Global adapter instance
event_adapter = EventAdapter()
