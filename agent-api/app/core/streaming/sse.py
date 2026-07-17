"""Shared Server-Sent Events settings for HTTP streaming endpoints."""
from __future__ import annotations

from app.config import settings

STREAM_TIMEOUT_SECONDS = settings.sse_stream_timeout_seconds
HEARTBEAT_INTERVAL_SECONDS = settings.sse_heartbeat_interval_seconds

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
