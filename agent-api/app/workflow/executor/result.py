"""Workflow result construction and persistence helpers.

Extracted from VisualWorkflowExecutor. ``build_result`` is pure; ``persist_execution``
takes an explicit ``execution_repo`` and the events list rather than reaching
into executor state, so the call site stays in the executor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_result(
    execution_id,
    status,
    start_time,
    nodes_executed: int = 0,
    sanitized_results: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a workflow execution result dict."""
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    result: Dict[str, Any] = {
        "execution_id": execution_id,
        "status": status,
        "start_time": start_time.isoformat().replace('+00:00', 'Z'),
        "end_time": end_time.isoformat().replace('+00:00', 'Z'),
        "duration": duration,
    }
    # Always include nodes_executed and per-node results — the UI uses
    # results[node_id].status to show the node badge regardless of overall
    # workflow status (failed workflows should still show per-node status).
    result["nodes_executed"] = nodes_executed
    if sanitized_results:
        result["results"] = sanitized_results
    if error:
        result["error"] = error
    return result


async def persist_execution(
    execution_repo,
    result: Dict[str, Any],
    workflow: Dict[str, Any],
    events: List[Any],
) -> None:
    """Save execution result to storage, accumulating trajectory + token usage."""
    try:
        # Collect the full message trajectory from any agent node results so
        # it can be stored for later analysis, insight queries, and training.
        trajectory: list = []
        node_results = result.get("results") or {}
        if isinstance(node_results, dict):
            for node_result in node_results.values():
                if isinstance(node_result, dict) and node_result.get("messages"):
                    trajectory.extend(node_result["messages"])

        # Accumulate token usage from all node results (agent nodes carry
        # input_tokens / output_tokens / cache_read_tokens set by
        # ReactStrategy._execute_agent). cache_read is the slice of input
        # served from the prompt cache (the provider sums it INTO input_tokens),
        # so it lets the UI back out the true non-cached cost.
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        for node_result in (node_results.values() if isinstance(node_results, dict) else []):
            if isinstance(node_result, dict):
                input_tokens += node_result.get("input_tokens", 0) or 0
                output_tokens += node_result.get("output_tokens", 0) or 0
                cache_read_tokens += node_result.get("cache_read_tokens", 0) or 0

        await execution_repo.save({
            **result,
            "workflow_name": workflow.get('name'),
            "workflow_id": workflow.get('id'),
            "events": events,
            "trajectory": trajectory or None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_read_tokens": cache_read_tokens,
        })
    except Exception as e:
        logger.error(f"Failed to save execution to storage: {e}")
