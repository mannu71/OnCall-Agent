"""Execution event types for SSE streaming."""
import json
from datetime import datetime, timezone
from typing import Any, Dict


class ExecutionEvent:
    """Internal execution event for SSE streaming."""

    def __init__(self, event_type: str, data: Dict[str, Any]):
        self.event_type = event_type
        self.data = data
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        # Monotonic per-execution sequence number, assigned by the executor when
        # the event is published. Lets a late-attaching SSE stream replay the
        # buffered backlog and then dedupe live events it already saw.
        self.seq: int = 0

    def dict(self):
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "seq": self.seq,
        }

    def to_sse(self) -> str:
        """Render as an SSE chunk — identical wire format to WorkflowEvent.to_sse
        so the frontend (useWorkflowStream.js) parses both interchangeably.
        """
        payload = json.dumps(self.dict())
        return f"event: {self.event_type}\ndata: {payload}\n\n"
