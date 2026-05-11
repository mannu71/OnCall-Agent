"""SSE stream adapter — converts execution queue events to Server-Sent Events.

Usage (from a FastAPI endpoint):

    from fastapi.responses import StreamingResponse
    from app.workflow.event_adapter import execution_event_stream

    @router.get("/{execution_id}/stream")
    async def stream_execution(execution_id: str):
        return StreamingResponse(
            execution_event_stream(execution_id, workflow_name),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_STREAM_TIMEOUT_SECONDS = 600   # hard ceiling — prevents zombie SSE connections
_HEARTBEAT_INTERVAL_SECONDS = 15  # keep-alive ping cadence
_TERMINAL_EVENTS = frozenset({"workflow_completed", "workflow_failed"})


async def execution_event_stream(
    execution_id: str,
    workflow_name: str,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted strings for *execution_id*.

    - Subscribes to the execution's asyncio.Queue via VisualWorkflowExecutor.
    - Emits events as ``event: <type>\\ndata: <json>\\n\\n`` chunks.
    - Sends ``: heartbeat`` comment lines every _HEARTBEAT_INTERVAL_SECONDS
      to prevent proxy / browser timeouts.
    - Closes the stream after a terminal event (workflow_completed /
      workflow_failed) or after _STREAM_TIMEOUT_SECONDS.
    - Always unsubscribes from the queue in the finally block.
    """
    from app.services.visual_workflow_executor import visual_executor

    queue = visual_executor.subscribe_to_events(execution_id)
    loop = asyncio.get_event_loop()

    try:
        # Initial handshake event
        yield (
            f"event: connected\n"
            f"data: {json.dumps({'execution_id': execution_id, 'workflow_name': workflow_name})}\n\n"
        )

        deadline = loop.time() + _STREAM_TIMEOUT_SECONDS
        last_heartbeat = loop.time()

        while True:
            now = loop.time()

            if now >= deadline:
                logger.warning(
                    "SSE stream timed out after %ds for execution %s",
                    _STREAM_TIMEOUT_SECONDS,
                    execution_id,
                )
                break

            # Heartbeat so the connection stays alive through idle stretches
            if now - last_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                continue  # loop around → emit heartbeat if needed

            yield event.to_sse()

            if event.event_type in _TERMINAL_EVENTS:
                break

    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled for execution %s", execution_id)

    finally:
        visual_executor.unsubscribe_from_events(execution_id, queue)
        yield (
            f"event: stream_end\n"
            f"data: {json.dumps({'execution_id': execution_id})}\n\n"
        )


async def workflow_name_event_stream(
    workflow_name: str,
) -> AsyncGenerator[str, None]:
    """Stream events for the most-recently-started execution of *workflow_name*.

    Used by the legacy ``/workflows/{name}/stream`` endpoint that the frontend
    useWorkflowStream hook opens by workflow name rather than execution ID.
    Polls active_executions until a matching execution appears (up to 10 s),
    then delegates to execution_event_stream.
    """
    from app.services.visual_workflow_executor import visual_executor

    # Wait up to 10 s for the execution to be registered
    for _ in range(20):
        for exec_id, exec_data in visual_executor.active_executions.items():
            if exec_data.get("workflow_name") == workflow_name:
                async for chunk in execution_event_stream(exec_id, workflow_name):
                    yield chunk
                return
        await asyncio.sleep(0.5)

    # No execution found — emit a single error event and close
    yield (
        f"event: agent_error\n"
        f"data: {json.dumps({'error': f'No active execution found for workflow {workflow_name!r}', 'node_id': 'system'})}\n\n"
    )
    yield f"event: stream_end\ndata: {json.dumps({'workflow_name': workflow_name})}\n\n"
