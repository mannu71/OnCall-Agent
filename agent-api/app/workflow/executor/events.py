"""Execution event types for SSE streaming."""
from datetime import datetime, timezone
from typing import Any, Dict


class ExecutionEvent:
    """Internal execution event for SSE streaming."""

    def __init__(self, event_type: str, data: Dict[str, Any]):
        self.event_type = event_type
        self.data = data
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    def dict(self):
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
