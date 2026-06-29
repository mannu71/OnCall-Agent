"""DAG graph construction and BFS execution helpers.

Extracted from VisualWorkflowExecutor so the topological / concurrent scheduling
logic can be tested and reasoned about independently of the executor's mutable
state. The original methods on ``VisualWorkflowExecutor`` remain as thin
delegators for backwards compatibility (the test suite monkey-patches
``_execute_node`` on the instance, which still works because the method lives
on the class).
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List

# Per-node wall-clock budget. A handler that wedges (e.g. an MCP call that
# never returns) would otherwise block the entire asyncio.gather batch
# indefinitely. Override via env when long-running agent nodes need more time.
_NODE_TIMEOUT_SECONDS = float(os.getenv("WORKFLOW_NODE_TIMEOUT_SECONDS", "300"))

logger = logging.getLogger(__name__)


class WorkflowDeadlockError(RuntimeError):
    """Raised internally when the BFS frontier can make no further progress.

    Carries the node ids that never became runnable so callers (and the user)
    can see exactly which nodes were stranded instead of silently dropping them.
    """

    def __init__(self, blocked_node_ids):
        self.blocked_node_ids = list(blocked_node_ids)
        super().__init__(
            "Workflow deadlocked — nodes never became runnable: "
            + ", ".join(str(n) for n in self.blocked_node_ids)
        )


def build_execution_graph(nodes, edges):
    """Build adjacency list, start nodes, parent mapping, and node map from workflow."""
    adjacency = defaultdict(list)
    in_degree = defaultdict(int)

    for edge in edges:
        source = edge.get('source')
        target = edge.get('target')
        if source and target:
            adjacency[source].append(target)
            in_degree[target] += 1

    start_nodes = [
        node.get('id') for node in nodes
        if node.get('type') in ('scheduler', 'schedule') or in_degree[node.get('id')] == 0
    ]

    if not start_nodes:
        start_nodes = [nodes[0].get('id')]

    logger.info(f"Starting nodes: {start_nodes}")

    node_map = {n.get('id'): n for n in nodes}
    parents_of = defaultdict(set)
    for src, targets in adjacency.items():
        for tgt in targets:
            parents_of[tgt].add(src)

    return adjacency, start_nodes, parents_of, node_map


async def execute_nodes_bfs(
    execute_node_fn: Callable[[str, Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]],
    adjacency,
    start_nodes,
    parents_of,
    node_map,
    execution_id: str,
):
    """Execute nodes in topological order, running same-level nodes concurrently.

    Nodes whose parents have all completed execute as a single ``asyncio.gather``
    batch, bounded by ``PARALLEL_FLOW_CONCURRENCY`` (default 5) so we don't
    flood the LLM / MCP layer when a workflow fans out widely. This delivers
    the 2-4x wall-clock reduction described in plan section 3.1 without altering
    the public return contract.

    ``execute_node_fn`` is the caller's per-node executor — typically
    ``VisualWorkflowExecutor._execute_node`` bound to the instance. It is
    invoked as ``await execute_node_fn(execution_id, node, execution_results)``.
    """
    from app.core.parallel_flow import ParallelFlowConfig

    executed: set = set()
    execution_results: Dict[str, Any] = {}

    # Track candidate frontier — nodes whose parents have all completed but
    # who have not yet been launched. Initialize from the workflow's roots.
    frontier: List[str] = list(dict.fromkeys(start_nodes))  # de-dup, preserve order

    # Concurrency cap reused from the existing parallel-flow tunable so the
    # same env var (PARALLEL_FLOW_CONCURRENCY) drives both subsystems.
    concurrency = max(1, ParallelFlowConfig().concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(node_id: str):
        node = node_map.get(node_id)
        if not node:
            return node_id, None, False  # skip unknown
        async with semaphore:
            logger.info(
                "Executing node: %s (%s) [exec=%s]",
                node_id, node.get("type"), execution_id,
            )
            try:
                result = await asyncio.wait_for(
                    execute_node_fn(execution_id, node, execution_results),
                    timeout=_NODE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Node '%s' (%s) timed out after %.0fs [exec=%s]",
                    node_id, node.get("type"), _NODE_TIMEOUT_SECONDS, execution_id,
                )
                result = {
                    "status": "failed",
                    "error": (
                        f"Node timed out after {_NODE_TIMEOUT_SECONDS:.0f}s — "
                        "set WORKFLOW_NODE_TIMEOUT_SECONDS to a higher value for "
                        "long-running agent or MCP nodes."
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Node '%s' (%s) raised unexpectedly [exec=%s]: %s",
                    node_id, node.get("type"), execution_id, exc,
                )
                result = {"status": "failed", "error": f"Unexpected error: {exc}"}
        return node_id, result, True

    # Safety: cap total levels at 2x node count to defend against malformed
    # graphs that would otherwise loop forever.
    max_levels = max(1, len(node_map) * 2)
    levels = 0

    while frontier and levels < max_levels:
        levels += 1

        # Partition frontier: ready (parents all executed) vs not-yet-ready.
        ready: List[str] = []
        blocked: List[str] = []
        for nid in frontier:
            if nid in executed:
                continue
            if parents_of[nid] <= executed:
                ready.append(nid)
            else:
                blocked.append(nid)

        if not ready:
            # Deadlock guard — no node became ready this pass. Instead of
            # silently dropping the stranded nodes, mark each as failed so the
            # executor's failed-node reporting surfaces exactly which nodes
            # never ran (partial results from completed nodes are preserved).
            logger.error(
                "BFS deadlocked for execution %s; stranded nodes=%s",
                execution_id, blocked,
            )
            for nid in blocked:
                execution_results[nid] = {
                    "status": "failed",
                    "error": (
                        "Node never became runnable — its upstream "
                        "dependencies did not all complete (workflow deadlock)."
                    ),
                }
            break

        # Launch every ready node concurrently.  return_exceptions=True so a
        # single failing node does not abort the entire level and discard the
        # partial results of its siblings (_run_one already handles its own
        # exceptions, so items should never be BaseException in practice).
        results = await asyncio.gather(
            *(_run_one(nid) for nid in ready),
            return_exceptions=True,
        )

        next_frontier: List[str] = list(blocked)
        for item in results:
            if isinstance(item, BaseException):
                # _run_one escaped its own guard — log and continue; we cannot
                # attribute the exception to a specific node_id here.
                logger.error("BFS: unattributed node runner exception: %s", item)
                continue
            node_id, result, ran = item
            executed.add(node_id)
            if ran:
                execution_results[node_id] = result

                # ── Router node: skip non-selected downstream agent nodes ──
                # The router returns routing.target_agent_id telling us which
                # ONE agent should run. Mark every other agent that is a direct
                # child of this router as executed+skipped so the BFS never
                # launches them — otherwise all connected agents would run.
                if result and isinstance(result, dict) and result.get("routing"):
                    target_id = result["routing"].get("target_agent_id")
                    if target_id:
                        for child_id in adjacency.get(node_id, []):
                            child_node = node_map.get(child_id, {})
                            if (
                                child_node.get("type") == "agent"
                                and child_id != target_id
                                and child_id not in executed
                            ):
                                executed.add(child_id)
                                execution_results[child_id] = {
                                    "status": "skipped",
                                    "output": (
                                        f"Skipped by Semantic Router — "
                                        f"selected route: '{result['routing'].get('selected_category')}' "
                                        f"→ agent '{target_id}'"
                                    ),
                                }
                                logger.info(
                                    "Router skipped non-selected agent '%s' (exec=%s)",
                                    child_id, execution_id,
                                )

            for child_id in adjacency.get(node_id, []):
                if child_id not in executed and child_id not in next_frontier:
                    next_frontier.append(child_id)

        frontier = next_frontier

    if levels >= max_levels:
        # Hit the safety cap with frontier nodes still pending — surface them
        # as failed rather than returning a silently-incomplete run.
        stranded = [nid for nid in frontier if nid not in executed]
        logger.error(
            "BFS hit level limit for execution %s; stranded nodes=%s",
            execution_id, stranded,
        )
        for nid in stranded:
            execution_results.setdefault(nid, {
                "status": "failed",
                "error": (
                    "Node never ran — workflow exceeded the maximum execution "
                    "depth (possible cycle or malformed graph)."
                ),
            })

    return executed, execution_results
