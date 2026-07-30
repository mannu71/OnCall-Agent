"""Workflow result construction and persistence helpers.

Extracted from VisualWorkflowExecutor. ``build_result`` is pure; ``persist_execution``
takes an explicit ``execution_repo`` and the events list rather than reaching
into executor state, so the call site stays in the executor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.observability.cache_metrics import annotate_cache_metrics

logger = logging.getLogger(__name__)


def _sum_node_tokens(sanitized_results: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Sum input/output/cache token fields across every node result.

    Node results carry these fields when they ran an agent (ReactStrategy) —
    see app/workflow/executor/handlers/agent.py. Non-agent nodes simply
    contribute 0. Shared by ``build_result`` (the dict returned to the HTTP
    caller / used for chat-session token rollup) so callers never see a
    result missing top-level tokens (previously only ``persist_execution``'s
    own, separate DB-write summation had these numbers).
    """
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    for node_result in (sanitized_results or {}).values():
        if isinstance(node_result, dict):
            for key in totals:
                totals[key] += node_result.get(key, 0) or 0
    return totals


def _context_usage(sanitized_results: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Context-window fullness for the Chat UI's context bar.

    Unlike tokens, this is NOT summed across nodes — it's "how full is the
    context window right now", so we take the agent node with the largest
    window (the one an interactive chat turn actually talked to).
    """
    best = {"context_window_size": 0, "context_used_tokens": 0, "context_used_pct": 0}
    for node_result in (sanitized_results or {}).values():
        if isinstance(node_result, dict) and node_result.get("context_window_size"):
            if node_result.get("context_used_pct", 0) >= best["context_used_pct"]:
                best = {
                    "context_window_size": node_result.get("context_window_size", 0) or 0,
                    "context_used_tokens": node_result.get("context_used_tokens", 0) or 0,
                    "context_used_pct": node_result.get("context_used_pct", 0) or 0,
                }
    return best


def build_result(
    execution_id,
    status,
    start_time,
    nodes_executed: int = 0,
    sanitized_results: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    chat_session_id: Optional[str] = None,
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

    # Promote token totals to the top level — the HTTP caller (chat
    # persistence, session token rollup) reads these directly rather than
    # reaching into results[node_id]. Included even on failure so partial
    # token usage before a node failed is still visible.
    totals = _sum_node_tokens(sanitized_results)
    result["input_tokens"] = totals["input_tokens"]
    result["output_tokens"] = totals["output_tokens"]
    result["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    result["cache_read_tokens"] = totals["cache_read_tokens"]
    result["cache_creation_tokens"] = totals["cache_creation_tokens"]
    # Prompt-cache efficiency. The raw counters have always been here; the
    # ratio is what makes a busted cached prefix visible (and it WARNs when the
    # rate collapses). See app.core.observability.cache_metrics.
    annotate_cache_metrics(
        result,
        totals["input_tokens"],
        totals["cache_read_tokens"],
        context=f"execution_id={execution_id}",
    )
    result.update(_context_usage(sanitized_results))

    # Set only when this execution was triggered by a chat turn (migration
    # 028) — lets the Dashboard exclude ad-hoc chat turns from real
    # workflow-run stats (``chat_session_id IS NULL`` = a real workflow run).
    if chat_session_id:
        result["chat_session_id"] = chat_session_id

    return result


async def persist_execution(
    execution_repo,
    result: Dict[str, Any],
    workflow: Dict[str, Any],
    events: List[Any],
) -> None:
    """Save execution result to storage, accumulating trajectory + token usage.

    Trajectory/token collection is best-effort (a malformed node result must
    never block the save); the ``execution_repo.save()`` write itself is NOT
    swallowed here — it propagates to the caller. That write is what flips
    the row's status out of "running" (the "already running" duplicate-run
    guard is a direct read of that column), so silently absorbing its
    failure here would leave the workflow permanently locked with no signal
    that anything went wrong. The caller (VisualWorkflowExecutor) catches
    this and falls back to a minimal status-only write.
    """
    # Collect the full message trajectory from any agent node results so
    # it can be stored for later analysis, insight queries, and training.
    trajectory: list = []
    node_results = result.get("results") or {}
    try:
        if isinstance(node_results, dict):
            for node_result in node_results.values():
                if isinstance(node_result, dict) and node_result.get("messages"):
                    trajectory.extend(node_result["messages"])
    except Exception as e:  # noqa: BLE001 — trajectory collection is best-effort
        logger.warning(f"Trajectory collection failed (non-fatal): {e}")
        trajectory = []

    # Token usage: build_result() already sums these across node_results
    # onto the top level (see _sum_node_tokens) — prefer that single
    # source of truth. Fall back to re-summing here only for a result
    # dict that didn't go through build_result. cache_read / cache_creation
    # are a BREAKDOWN of input_tokens, not counters to add to it (see
    # TokenUsageCallback and app.core.observability.cache_metrics — this
    # comment asserted the opposite until it was checked against live Bedrock
    # on 2026-07-21).
    try:
        if "input_tokens" in result:
            input_tokens = result.get("input_tokens", 0) or 0
            output_tokens = result.get("output_tokens", 0) or 0
            cache_read_tokens = result.get("cache_read_tokens", 0) or 0
            cache_creation_tokens = result.get("cache_creation_tokens", 0) or 0
        else:
            input_tokens = 0
            output_tokens = 0
            cache_read_tokens = 0
            cache_creation_tokens = 0
            for node_result in (node_results.values() if isinstance(node_results, dict) else []):
                if isinstance(node_result, dict):
                    input_tokens += node_result.get("input_tokens", 0) or 0
                    output_tokens += node_result.get("output_tokens", 0) or 0
                    cache_read_tokens += node_result.get("cache_read_tokens", 0) or 0
                    cache_creation_tokens += node_result.get("cache_creation_tokens", 0) or 0
    except Exception as e:  # noqa: BLE001 — token summation is best-effort
        logger.warning(f"Token summation failed (non-fatal): {e}")
        input_tokens = output_tokens = cache_read_tokens = cache_creation_tokens = 0

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
        "cache_creation_tokens": cache_creation_tokens,
    })
