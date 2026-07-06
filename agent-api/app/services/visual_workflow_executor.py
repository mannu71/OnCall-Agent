"""Visual workflow executor for node-based workflows."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import defaultdict

from app.infrastructure.persistence import ExecutionRepository
from app.config import settings
from app.core.workflow_concurrency import workflow_semaphore
from app.services.execution_state import execution_state
from app.services.mcp_client_manager import MCPClientManager
from app.workflow.executor.events import ExecutionEvent
from app.workflow.executor.graph import build_execution_graph, execute_nodes_bfs
from app.workflow.executor.result import build_result
from app.workflow.executor.sanitize import sanitize_results
# Streaming subsystem (extracted) — re-exported for backwards compatibility.
from app.workflow.executor.streaming import (  # noqa: F401
    _AgentStreamCallback,
    _DROPPED_EVENTS,
    _SSE_QUEUE_MAXSIZE,
    _safe_put,
    get_dropped_event_count,
)

logger = logging.getLogger(__name__)


class VisualWorkflowExecutor:
    """Executes visual node-based workflows."""
    
    def __init__(self):
        self._execution_state = execution_state
        # Ephemeral runtime cache — DB holds durable status (see execution_state).
        self.active_executions: Dict[str, Dict[str, Any]] = (
            self._execution_state.runtime_cache
        )
        self.event_queues: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self.mcp_managers: Dict[str, MCPClientManager] = {}  # Per-execution MCP managers
        self.background_tasks: set = set()  # Keep references to background tasks
        # Serializes read-modify-write on the per-execution cache entry. Same-level
        # nodes run concurrently via asyncio.gather and mutate shared lists
        # (events buffer, nodes_completed); a lock keeps those compound updates
        # atomic and guards against a cleanup popping the entry mid-update.
        self._state_lock = asyncio.Lock()

    async def _record_event(self, execution_id: str, event) -> None:
        """Append an event to the cache buffer (capped), under the state lock.

        No-op when the execution entry is absent (already cleaned up / cancelled).
        """
        async with self._state_lock:
            entry = self.active_executions.get(execution_id)
            if entry is None:
                return
            # Stamp a monotonic per-execution sequence so a late-attaching SSE
            # stream can replay this backlog and dedupe live events by seq.
            seq = entry.get('_event_seq', 0) + 1
            entry['_event_seq'] = seq
            event.seq = seq
            events = entry.setdefault('events', [])
            events.append(event.dict())
            cap = settings.max_runtime_events
            if len(events) > cap:
                del events[:-cap]

    async def get_buffered_events(self, execution_id: str) -> List[Dict[str, Any]]:
        """Return a snapshot of the buffered events for *execution_id*.

        Each item is an ``ExecutionEvent.dict()`` (already carrying ``seq``).
        Used by the SSE adapter to replay the backlog to a stream that attached
        after the run started. Empty when the execution is unknown/cleaned up.
        """
        async with self._state_lock:
            entry = self.active_executions.get(execution_id)
            if entry is None:
                return []
            return list(entry.get('events', []))

    async def _mark_node_completed(self, execution_id: str, node_id: str) -> None:
        """Record a completed node under the state lock.

        Uses setdefault so an orphaned entry (missing nodes_completed) never
        raises, and skips silently if the entry was already cleaned up.
        """
        async with self._state_lock:
            entry = self.active_executions.get(execution_id)
            if entry is None:
                return
            entry.setdefault('nodes_completed', []).append(node_id)
    
    def subscribe_to_events(self, execution_id: str) -> asyncio.Queue:
        """Subscribe to execution events."""
        queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
        self.event_queues[execution_id].append(queue)
        return queue
    
    def unsubscribe_from_events(self, execution_id: str, queue: asyncio.Queue):
        """Unsubscribe from execution events."""
        if execution_id in self.event_queues:
            try:
                self.event_queues[execution_id].remove(queue)
            except ValueError:
                pass
    
    async def _publish_event(self, execution_id: str, event_type: str, data: Dict[str, Any]):
        """Publish an event to all subscribers."""
        event = ExecutionEvent(event_type, data)

        # Store in execution state (atomic read-modify-write under the lock).
        await self._record_event(execution_id, event)

        # Publish to queues using non-blocking put with drop-on-full semantics.
        # Prevents a slow SSE subscriber from stalling the executor's event loop.
        for queue in self.event_queues.get(execution_id, []):
            try:
                _safe_put(queue, event)
            except Exception as e:
                logger.warning(f"Error publishing event: {e}")
    
    async def publish_token_usage_delta(self, execution_id: str, ledger) -> None:
        """Push a ``token_usage_delta`` SSE event with current ledger totals.

        Strategies that wire up a per-execution ``TokenLedger`` via the
        ``on_usage`` callback (see ``app/core/transport/anthropic_transport.py``)
        call this helper after each LLM round so the UI shows live spend
        — closing the loop on plan §2.6.

        Argument ``ledger`` is duck-typed: anything exposing ``totals()``
        works. We accept Any so tests can pass a stub without importing
        the production type.
        """
        try:
            totals = ledger.totals() if hasattr(ledger, "totals") else dict(ledger)
        except Exception as e:  # noqa: BLE001
            logger.warning("publish_token_usage_delta: bad ledger (%s)", e)
            return
        await self._publish_event(execution_id, "token_usage_delta", totals)

    async def _is_workflow_running(self, workflow_name: str) -> Optional[str]:
        """Return execution ID if workflow is already running (DB-backed)."""
        return await self._execution_state.find_running_id(workflow_name)

    async def execute_workflow(self, workflow: Dict[str, Any], inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a visual workflow and return results."""
        workflow_name = workflow.get('name')
        
        # Prevent duplicate concurrent executions of the same workflow
        existing_id = await self._is_workflow_running(workflow_name)
        if existing_id:
            logger.warning(f"Workflow '{workflow_name}' is already running (ID: {existing_id}), skipping")
            return {
                "status": "skipped",
                "message": f"Workflow '{workflow_name}' is already running",
                "existing_execution_id": existing_id
            }

        async with workflow_semaphore():
            execution_id = await self._execution_state.start_execution(
                workflow_name,
                workflow_id=workflow.get('id'),
                inputs=inputs,
                workflow=workflow,
            )
            # Register the task actually running this coroutine so a later
            # "Stop" can genuinely cancel it (not just mark the DB row cancelled
            # — see execution_state.cancel_execution / _cancel_task). The
            # foreground /execute path (workflows.py) wraps run_workflow() in
            # asyncio.create_task(), so current_task() here IS that task
            # regardless of how many awaits deep we are.
            _cur_task = asyncio.current_task()
            if _cur_task is not None:
                self._execution_state.register_task(execution_id, _cur_task)
            start_time = datetime.now(timezone.utc)

            logger.info(f"Starting workflow execution: {workflow_name} (ID: {execution_id})")
            result = None
            try:
                await self._publish_event(execution_id, "workflow_started", {
                    "execution_id": execution_id,
                    "workflow_name": workflow_name,
                    "workflow_id": workflow.get('id')
                })

                nodes = workflow.get('nodes', [])
                edges = workflow.get('edges', [])

                logger.info(f"Workflow has {len(nodes)} nodes and {len(edges)} edges")

                if not nodes:
                    raise ValueError("Workflow has no nodes to execute")

                adjacency, start_nodes, parents_of, node_map = build_execution_graph(nodes, edges)
                executed, execution_results = await execute_nodes_bfs(
                    self._execute_node, adjacency, start_nodes, parents_of, node_map, execution_id,
                )

                failed_nodes = [
                    node_id for node_id, node_result in execution_results.items()
                    if isinstance(node_result, dict) and node_result.get('status') == 'failed'
                ]

                # Set only when this run was triggered by a chat turn (the
                # /execute endpoint stashes it here rather than as a top-level
                # workflow input, to avoid colliding with a real workflow
                # input the user might separately name "session_id").
                _chat_session_id = (inputs or {}).get("_chat_session_id")

                if failed_nodes:
                    error_messages = []
                    for node_id in failed_nodes:
                        node_result = execution_results[node_id]
                        error_msg = node_result.get('error', 'Unknown error')
                        error_messages.append(f"{node_id}: {error_msg}")

                    result = build_result(
                        execution_id, "failed", start_time, len(executed),
                        sanitized_results=sanitize_results(execution_results),
                        error=f"Node(s) failed: {'; '.join(error_messages)}",
                        chat_session_id=_chat_session_id,
                    )
                else:
                    result = build_result(
                        execution_id, "success", start_time, len(executed),
                        sanitized_results=sanitize_results(execution_results),
                        chat_session_id=_chat_session_id,
                    )

                self._execution_state.mirror_status(
                    execution_id,
                    "success",
                    end_time=result['end_time'],
                    duration=result['duration'],
                )

                await self._publish_event(execution_id, "workflow_completed", {
                    "execution_id": execution_id,
                    "status": "success",
                    "duration": result['duration'],
                    "nodes_executed": len(executed),
                    "input_tokens":  result['input_tokens'],
                    "output_tokens": result['output_tokens'],
                    "total_tokens":  result['total_tokens'],
                })

            except Exception as e:
                result = build_result(
                    execution_id, "failed", start_time, error=str(e),
                    chat_session_id=(inputs or {}).get("_chat_session_id"),
                )
                logger.error(f"Workflow execution failed: {execution_id} - {e}")

                if execution_id in self.active_executions:
                    self._execution_state.mirror_status(
                        execution_id,
                        "failed",
                        error=str(e),
                        end_time=result['end_time'],
                    )

                await self._publish_event(execution_id, "workflow_failed", {
                    "execution_id": execution_id,
                    "error": str(e),
                    "duration": result['duration']
                })

            finally:
                if result:
                    try:
                        await self._persist_execution(result, workflow, execution_id)
                    except Exception as persist_exc:  # noqa: BLE001
                        # The rich persist path (execution_repo.save(), with
                        # trajectory + token fields) has real surface area to
                        # fail on — e.g. the same network blip that just
                        # failed the workflow can also disrupt this DB write.
                        # If it throws, the execution row is left at
                        # status='running' — which is exactly what the
                        # "already running" duplicate-run guard checks,
                        # permanently blocking every future run of this
                        # workflow until someone finds and clears it manually.
                        # Fall back to a minimal, low-surface-area write that
                        # just flips the status so the lock always releases.
                        logger.error(
                            f"Rich persist failed for execution {execution_id}, "
                            f"falling back to minimal status write: {persist_exc}"
                        )
                        try:
                            await ExecutionRepository().mark_failed(
                                execution_id,
                                error=result.get("error") or str(persist_exc),
                            )
                        except Exception as fallback_exc:  # noqa: BLE001
                            logger.error(
                                f"Fallback status write also failed for "
                                f"execution {execution_id}: {fallback_exc}"
                            )
                await self.cleanup_execution(execution_id)

            return result

    async def _persist_execution(self, result: Dict[str, Any], workflow: Dict[str, Any], execution_id: str):
        """Save execution result to storage (delegates to app.workflow.executor.result)."""
        from app.workflow.executor.result import persist_execution
        execution_repo = ExecutionRepository()
        cached = self.active_executions.get(execution_id, {})
        events = cached.get('events', [])
        await persist_execution(execution_repo, result, workflow, events)
        if execution_id in self.active_executions:
            self.active_executions[execution_id].pop('workflow', None)
            self.active_executions[execution_id].pop('events', None)
    
    async def _execute_node(self, execution_id: str, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single node."""
        node_id = node.get('id')
        node_type = node.get('type')
        node_data = node.get('data', {})
        
        await self._publish_event(execution_id, "node_started", {
            "node_id": node_id,
            "node_type": node_type,
            "label": node_data.get('label', node_type)
        })
        
        start_time = datetime.now(timezone.utc)
        context['execution_id'] = execution_id
        # Guard against the entry being cleaned up (e.g. cancelled) mid-run.
        entry = self.active_executions.get(execution_id, {})
        context['inputs'] = entry.get('inputs', {})
        context['workflow_name'] = entry.get('workflow_name')
        
        # Node type dispatch — handler registry is canonical.
        try:
            from app.workflow.executor.handlers import HANDLERS
            if node_type in HANDLERS:
                result = await HANDLERS[node_type](self, node, context)
            else:
                # Loud, not silent: a core type (agent/language_model/...)
                # landing here means the handler registry is broken (e.g. a
                # module-import failure poisoned registration) — the run would
                # otherwise "succeed" with the wrong node's output as the
                # visible answer.
                logger.warning(
                    "Unknown node type '%s' (node=%s) — skipping; registered "
                    "handlers: %s", node_type, node_id, sorted(HANDLERS.keys()),
                )
                result = {
                    "status": "skipped",
                    "output": f"Unknown node type: {node_type}",
                }
            
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            await self._publish_event(execution_id, "node_completed", {
                "node_id": node_id,
                "node_type": node_type,
                "status": result.get('status', 'success'),
                "duration": duration,
                "output": result.get('output', '')[:500],  # Truncate for event
                "input_tokens":  result.get('input_tokens',  0) or 0,
                "output_tokens": result.get('output_tokens', 0) or 0,
                "total_tokens":  result.get('total_tokens',  0) or 0,
            })
            
            await self._mark_node_completed(execution_id, node_id)

            return result
        
        except Exception as e:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            await self._publish_event(execution_id, "node_failed", {
                "node_id": node_id,
                "node_type": node_type,
                "error": str(e),
                "duration": duration
            })
            
            return {
                "status": "failed",
                "error": str(e)
            }
    

    async def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an execution (cache first, then DB for running rows)."""
        return await self._execution_state.get_execution_status(execution_id)
    
    # ── Dynamic graph execution ───────────────────────────────────────────────

    async def run_workflow_graph(
        self,
        workflow: Dict[str, Any],
        inputs: Optional[Dict[str, Any]] = None,
        registry: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute a workflow using the dynamic graph engine.

        Unlike the BFS executor (which routes by node type at runtime),
        WorkflowGraph instantiates nodes from the ``node_registry`` using the
        node ``type`` field, wires successors from the edge list, then runs
        the graph.  This enables arbitrary custom node logic without changing
        the executor.

        Args:
            workflow: ReactFlow workflow dict (same schema as ``execute_workflow``).
            inputs:   Optional initial shared state injected before the first node.
            registry: NodeRegistry to use.  Defaults to ``default_registry``
                      from ``app.engine``.

        Returns:
            The shared state dict after the graph terminates.
        """
        from app.engine.crawler_engine import WorkflowGraph, default_registry

        reg = registry or default_registry
        shared: Dict[str, Any] = {"inputs": inputs or {}, "workflow": workflow}

        try:
            flow = WorkflowGraph.from_workflow_json(workflow, reg)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "run_workflow_graph: could not build WorkflowGraph: %s — "
                "falling back to BFS executor",
                exc,
            )
            return await self.execute_workflow(workflow, inputs)

        workflow_name = workflow.get("name", "workflow")
        logger.info("WorkflowGraph: running workflow '%s'", workflow_name)
        result = await flow.run(shared)
        logger.info("WorkflowGraph: workflow '%s' complete", workflow_name)
        return result

    async def cleanup_execution(self, execution_id: str):
        """Clean up execution data and disconnect MCP clients."""
        if execution_id in self.mcp_managers:
            mcp_manager = self.mcp_managers.pop(execution_id)
            try:
                await mcp_manager.disconnect_all()
            except asyncio.CancelledError:
                logger.warning(f"MCP cleanup cancelled for {execution_id}")
            except Exception as e:
                logger.warning(f"Error during MCP cleanup of {execution_id}: {e}")
        
        self._execution_state.cleanup_cache(execution_id)
        self._execution_state.unregister_task(execution_id)
        self.event_queues.pop(execution_id, None)


# Global executor instance
visual_executor = VisualWorkflowExecutor()
