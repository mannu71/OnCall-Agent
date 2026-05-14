"""Visual workflow executor for node-based workflows."""
import asyncio
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import defaultdict
from pathlib import Path

from app.repositories import ExecutionRepository
from app.services.mcp_client_manager import MCPClientManager
from app.services.sql_orchestrator import SQLOrchestrator
from app.mcp.tools.watch_tools import (
    watch_log_groups,
    analyze_log_patterns,
    detect_anomalies,
    correlate_logs,
)
from app.core.redact import redact

logger = logging.getLogger(__name__)

# Node types that contain only connection metadata (excluded from sanitized output)
_TOOL_NODE_KEYS = {'server_id', 'tool_data'}


class _AgentStreamCallback:
    """Adapts StreamCallback protocol events to VisualWorkflowExecutor._publish_event."""

    def __init__(self, executor: "VisualWorkflowExecutor", execution_id: str, node_id: str):
        self._executor = executor
        self._execution_id = execution_id
        self._node_id = node_id

    async def on_llm_token(self, token: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "llm_token",
            {"token": token, "node_id": self._node_id},
        )

    async def on_tool_call(self, tool_name: str, args: dict) -> None:
        await self._executor._publish_event(
            self._execution_id, "tool_call",
            {"tool": tool_name, "args": args, "node_id": self._node_id},
        )

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "tool_result",
            {"tool": tool_name, "result": redact(result)[:2000], "node_id": self._node_id},
        )

    async def on_error(self, error: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "agent_error",
            {"error": redact(error), "node_id": self._node_id},
        )

    async def on_complete(self, output: str) -> None:
        await self._executor._publish_event(
            self._execution_id, "agent_complete",
            {"output": redact(output)[:500], "node_id": self._node_id},
        )


class _ExecutionEvent:
    """Internal execution event for SSE streaming."""
    def __init__(self, event_type: str, data: Dict[str, Any]):
        self.event_type = event_type
        self.data = data
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    def dict(self):
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp
        }


class VisualWorkflowExecutor:
    """Executes visual node-based workflows."""
    
    def __init__(self):
        self.active_executions: Dict[str, Dict[str, Any]] = {}
        self.event_queues: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self.mcp_managers: Dict[str, MCPClientManager] = {}  # Per-execution MCP managers
        self.background_tasks: set = set()  # Keep references to background tasks
    
    @staticmethod
    def _build_node_result(value: Dict[str, Any]) -> Dict[str, Any]:
        """Extract relevant fields from a node execution result."""
        node_result = {}
        for field in ('status', 'output', 'trigger_time', 'model'):
            if field in value:
                node_result[field] = value[field]
        
        if 'queries_executed' in value:
            node_result['queries_executed'] = value['queries_executed']
            node_result['failures'] = value.get('failures', 0)
            node_result['results'] = value.get('results', [])
            if value.get('error'):
                node_result['error'] = value['error']
        
        if 'analysis_type' in value:
            node_result['analysis_type'] = value['analysis_type']
            node_result['log_groups_analyzed'] = value.get('log_groups_analyzed', [])
            node_result['time_range'] = value.get('time_range')
            node_result['data'] = value.get('data', {})
            if value.get('alerts'):
                node_result['alerts'] = value['alerts']
        
        return node_result
    
    def _sanitize_results(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Remove internal implementation details and sensitive data from results."""
        excluded_keys = {'execution_id', 'inputs', 'connected_tool_server', 'db_server_map'}
        sanitized = {}
        
        for key, value in results.items():
            if key in excluded_keys:
                continue
            if not isinstance(value, dict):
                sanitized[key] = value
                continue
            
            # Skip tool nodes (connection metadata only)
            if value.keys() & _TOOL_NODE_KEYS:
                continue
            
            node_result = self._build_node_result(value)
            if node_result:
                sanitized[key] = node_result
        
        return sanitized
    
    def subscribe_to_events(self, execution_id: str) -> asyncio.Queue:
        """Subscribe to execution events."""
        queue = asyncio.Queue()
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
        event = _ExecutionEvent(event_type, data)
        
        # Store in execution state
        if execution_id in self.active_executions:
            if 'events' not in self.active_executions[execution_id]:
                self.active_executions[execution_id]['events'] = []
            self.active_executions[execution_id]['events'].append(event.dict())
        
        # Publish to queues
        for queue in self.event_queues.get(execution_id, []):
            try:
                await queue.put(event)
            except Exception as e:
                logger.warning(f"Error publishing event: {e}")
    
    def _is_workflow_running(self, workflow_name: str) -> Optional[str]:
        """Return execution ID if workflow is already running, None otherwise."""
        for exec_id, exec_data in self.active_executions.items():
            if exec_data.get('workflow_name') == workflow_name and exec_data.get('status') == 'running':
                return exec_id
        return None

    async def execute_workflow(self, workflow: Dict[str, Any], inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a visual workflow and return results."""
        workflow_name = workflow.get('name')
        
        # Prevent duplicate concurrent executions of the same workflow
        existing_id = self._is_workflow_running(workflow_name)
        if existing_id:
            logger.warning(f"Workflow '{workflow_name}' is already running (ID: {existing_id}), skipping")
            return {
                "status": "skipped",
                "message": f"Workflow '{workflow_name}' is already running",
                "existing_execution_id": existing_id
            }
        
        execution_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        
        logger.info(f"Starting workflow execution: {workflow_name} (ID: {execution_id})")
        
        # Initialize execution state
        self.active_executions[execution_id] = {
            "id": execution_id,
            "workflow_name": workflow_name,
            "workflow_id": workflow.get('id'),
            "workflow": workflow,  # Full workflow stored for agent node access
            "status": "running",
            "start_time": start_time.isoformat().replace('+00:00', 'Z'),
            "nodes_completed": [],
            "events": [],
            "inputs": inputs or {}
        }
        
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
            
            adjacency, start_nodes, parents_of, node_map = self._build_execution_graph(nodes, edges)
            executed, execution_results = await self._execute_nodes_bfs(
                execution_id, adjacency, start_nodes, parents_of, node_map
            )
            
            # Check if any node failed
            failed_nodes = [
                node_id for node_id, node_result in execution_results.items()
                if isinstance(node_result, dict) and node_result.get('status') == 'failed'
            ]
            
            if failed_nodes:
                # Mark workflow as failed if any node failed
                error_messages = []
                for node_id in failed_nodes:
                    node_result = execution_results[node_id]
                    error_msg = node_result.get('error', 'Unknown error')
                    error_messages.append(f"{node_id}: {error_msg}")
                
                result = self._build_result(
                    execution_id, "failed", start_time, len(executed),
                    sanitized_results=self._sanitize_results(execution_results),
                    error=f"Node(s) failed: {'; '.join(error_messages)}"
                )
            else:
                result = self._build_result(
                    execution_id, "success", start_time, len(executed),
                    sanitized_results=self._sanitize_results(execution_results)
                )
            
            self.active_executions[execution_id]['status'] = 'completed'
            self.active_executions[execution_id]['end_time'] = result['end_time']
            self.active_executions[execution_id]['duration'] = result['duration']
            
            await self._publish_event(execution_id, "workflow_completed", {
                "execution_id": execution_id,
                "status": "success",
                "duration": result['duration'],
                "nodes_executed": len(executed)
            })
        
        except Exception as e:
            result = self._build_result(execution_id, "failed", start_time, error=str(e))
            logger.error(f"Workflow execution failed: {execution_id} - {e}")
            
            if execution_id in self.active_executions:
                self.active_executions[execution_id]['status'] = 'failed'
                self.active_executions[execution_id]['error'] = str(e)
                self.active_executions[execution_id]['end_time'] = result['end_time']
            
            await self._publish_event(execution_id, "workflow_failed", {
                "execution_id": execution_id,
                "error": str(e),
                "duration": result['duration']
            })
        
        finally:
            # Persist before cleanup (cleanup removes events from active_executions)
            if result:
                await self._persist_execution(result, workflow, execution_id)
            await self.cleanup_execution(execution_id)
        
        return result
    
    @staticmethod
    def _build_execution_graph(nodes, edges):
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
            if node.get('type') == 'scheduler' or in_degree[node.get('id')] == 0
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
    
    async def _execute_nodes_bfs(self, execution_id, adjacency, start_nodes, parents_of, node_map):
        """Execute nodes in topological order using BFS."""
        executed = set()
        queue = list(start_nodes)
        execution_results = {}
        max_iterations = len(node_map) * 2  # Safety limit against infinite loops
        iterations = 0
        
        while queue and iterations < max_iterations:
            iterations += 1
            node_id = queue.pop(0)
            
            if node_id in executed:
                continue
            
            if not parents_of[node_id] <= executed:
                queue.append(node_id)
                continue
            
            node = node_map.get(node_id)
            if not node:
                executed.add(node_id)  # Skip unknown nodes but mark as done
                continue
            
            logger.info(f"Executing node: {node_id} ({node.get('type')})")
            
            result = await self._execute_node(execution_id, node, execution_results)
            execution_results[node_id] = result
            executed.add(node_id)
            
            for child_id in adjacency.get(node_id, []):
                if child_id not in executed and child_id not in queue:
                    queue.append(child_id)
        
        if iterations >= max_iterations:
            logger.warning(f"BFS hit iteration limit for execution {execution_id}")
        
        return executed, execution_results
    
    @staticmethod
    def _build_result(execution_id, status, start_time, nodes_executed=0, sanitized_results=None, error=None):
        """Build a workflow execution result dict."""
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        result = {
            "execution_id": execution_id,
            "status": status,
            "start_time": start_time.isoformat().replace('+00:00', 'Z'),
            "end_time": end_time.isoformat().replace('+00:00', 'Z'),
            "duration": duration,
        }
        if status == "success":
            result["nodes_executed"] = nodes_executed
            result["results"] = sanitized_results or {}
        if error:
            result["error"] = error
        return result
    
    async def _persist_execution(self, result: Dict[str, Any], workflow: Dict[str, Any], execution_id: str):
        """Save execution result to storage."""
        try:
            execution_repo = ExecutionRepository()

            # Collect the full message trajectory from any agent node results so
            # it can be stored for later analysis, insight queries, and training.
            trajectory: list = []
            node_results = result.get("results") or {}
            if isinstance(node_results, dict):
                for node_result in node_results.values():
                    if isinstance(node_result, dict) and node_result.get("messages"):
                        trajectory.extend(node_result["messages"])

            # Accumulate token usage from all node results (agent nodes carry
            # input_tokens / output_tokens set by ReactStrategy._execute_agent)
            input_tokens  = 0
            output_tokens = 0
            for node_result in (node_results.values() if isinstance(node_results, dict) else []):
                if isinstance(node_result, dict):
                    input_tokens  += node_result.get("input_tokens",  0) or 0
                    output_tokens += node_result.get("output_tokens", 0) or 0

            await execution_repo.save({
                **result,
                "workflow_name":  workflow.get('name'),
                "workflow_id":    workflow.get('id'),
                "events":         self.active_executions[execution_id].get('events', []),
                "trajectory":     trajectory or None,
                "input_tokens":   input_tokens,
                "output_tokens":  output_tokens,
                "total_tokens":   input_tokens + output_tokens,
            })
        except Exception as e:
            logger.error(f"Failed to save execution to storage: {e}")
    
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
        context['inputs'] = self.active_executions[execution_id].get('inputs', {})
        context['workflow_name'] = self.active_executions[execution_id].get('workflow_name')
        
        # Node type → executor mapping
        executors = {
            'scheduler':          self._execute_scheduler_node,
            'orchestrator':       self._execute_orchestrator_node,
            'agent':              self._execute_agent_node,
            'batchAgent':         self._execute_batch_agent_node,
            'tool':               self._execute_tool_node,
            'cloudwatchAnalyzer': self._execute_cloudwatch_node,
            'codeAnalyzer':       self._execute_code_analyzer_node,
        }
        
        try:
            executor = executors.get(node_type)
            result = await executor(node, context) if executor else {
                "status": "skipped",
                "message": f"Unknown node type: {node_type}"
            }
            
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            await self._publish_event(execution_id, "node_completed", {
                "node_id": node_id,
                "node_type": node_type,
                "status": result.get('status', 'success'),
                "duration": duration,
                "output": result.get('output', '')[:500]  # Truncate for event
            })
            
            self.active_executions[execution_id]['nodes_completed'].append(node_id)
            
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
    
    async def _execute_scheduler_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute scheduler node (trigger point)."""
        # Scheduler nodes just mark the entry point
        await asyncio.sleep(0)  # Make function genuinely async
        return {
            "status": "success",
            "output": "Workflow triggered",
            "trigger_time": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        }
    
    @staticmethod
    def _load_sql_content(node_data: Dict[str, Any], workflow_name: Optional[str] = None) -> Optional[str]:
        """Load SQL content from node data, either inline or from a file.
        
        Args:
            node_data: The orchestrator node data containing sqlFile/fileName
            workflow_name: Optional workflow name to look for SQL in workflow's own directory
            
        Returns:
            SQL content string or None if not found
        """
        # Check for SQL content in order of preference
        sql_content = node_data.get('sqlContent') or node_data.get('fileContent')
        if sql_content:
            return sql_content
        
        sql_file = node_data.get('sqlFile') or node_data.get('fileName')
        if not sql_file:
            return None
        
        sql_filename = sql_file.replace('sql/', '').replace('sql\\', '')
        
        # Search paths in priority order:
        search_paths = []
        
        # 1. Workflow's own directory (if workflow_name provided)
        if workflow_name:
            safe_name = workflow_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
            search_paths.extend([
                Path('data/storage/workflows') / safe_name / sql_filename,
                Path('/app/data/storage/workflows') / safe_name / sql_filename,
            ])
        
        # 2. Global SQL directories (fallback for backwards compatibility)
        search_paths.extend([
            Path('data/config/sql') / sql_filename,
            Path('/app/data/config/sql') / sql_filename,
        ])
        
        for candidate in search_paths:
            if candidate.exists():
                logger.info(f"Loaded SQL from: {candidate}")
                return candidate.read_text(encoding='utf-8')
        
        return None
    
    async def _execute_orchestrator_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute orchestrator node with real SQL execution."""
        node_data = node.get('data', {})
        execution_id = context.get('execution_id')
        workflow_name = context.get('workflow_name')
        
        try:
            sql_content = self._load_sql_content(node_data, workflow_name)
            if not sql_content:
                sql_file = node_data.get('sqlFile') or node_data.get('fileName')
                error_msg = f"SQL file not found: {sql_file}" if sql_file else "No SQL content provided"
                return {"status": "failed", "error": error_msg}
            
            tool_server_id = context.get('connected_tool_server')
            if not tool_server_id:
                return {"status": "failed", "error": "No database tool connected to orchestrator"}
            
            mcp_manager = self.mcp_managers.get(execution_id)
            if not mcp_manager:
                return {"status": "failed", "error": "MCP manager not initialized"}
            
            # Create SQL orchestrator and execute
            orchestrator = SQLOrchestrator(mcp_manager, query_timeout=900)
            
            workflow_inputs = context.get('inputs', {})
            if workflow_inputs:
                orchestrator.variables.update(workflow_inputs)
            
            db_server_map = context.get('db_server_map', {})
            
            result = await orchestrator.execute_workflow(
                sql_content=sql_content,
                server_id=tool_server_id,
                db_server_map=db_server_map
            )
            
            return {
                "status": "success" if result.get('success') else "failed",
                "output": f"Executed {result.get('queries_executed', 0)} queries",
                "queries_executed": result.get('queries_executed', 0),
                "failures": result.get('failures', 0),
                "results": result.get('results', []),
                "error": result.get('error')
            }
            
        except Exception as e:
            logger.error(f"Orchestrator execution failed: {e}")
            return {
                "status": "failed",
                "error": str(e)
            }
    
    async def _execute_agent_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent node by delegating to ReactStrategy (LangGraph ReAct loop).

        The agent node's 'instructions' or the workflow's 'user_query' input drives
        the investigation. The shared MCP manager for this execution is passed so the
        agent can reuse already-connected database servers from preceding tool nodes.
        """
        from app.workflow.strategies.react import ReactStrategy

        node_id = node.get('id')
        node_data = node.get('data', {})
        execution_id = context.get('execution_id')

        active = self.active_executions.get(execution_id, {})
        workflow = active.get('workflow') or {
            'name': context.get('workflow_name', 'unknown'),
            'nodes': [node],
            'edges': [],
        }

        user_query = (
            context.get('inputs', {}).get('user_query')
            or context.get('inputs', {}).get('query')
            or node_data.get('instructions')
            or node_data.get('description')
            or ''
        )

        if not user_query:
            return {
                'status': 'skipped',
                'output': 'Agent node has no user_query or instructions configured.',
                'agent_data': node_data,
            }

        if execution_id not in self.mcp_managers:
            self.mcp_managers[execution_id] = MCPClientManager()

        mcp_manager = self.mcp_managers[execution_id]

        stream_callback = _AgentStreamCallback(self, execution_id, node_id)

        strategy_context = {
            'execution_id': execution_id,
            'user_query': user_query,
            'mcp_manager': mcp_manager,
            'inputs': context.get('inputs', {}),
            'logger': logger,
            'stream_callback': stream_callback,
        }

        # ------------------------------------------------------------------
        # Cross-node data: collect results from upstream CloudWatch nodes so
        # the agent can reason about pre-computed analysis.
        # ------------------------------------------------------------------
        cw_results = {}
        for key, value in context.items():
            if isinstance(value, dict) and value.get('analysis_type'):
                cw_results[key] = {
                    'analysis_type': value.get('analysis_type'),
                    'output': value.get('output'),
                    'log_groups_analyzed': value.get('log_groups_analyzed'),
                    'time_range': value.get('time_range'),
                    'alerts': value.get('alerts'),
                }
        if cw_results:
            strategy_context['cloudwatch_context'] = cw_results
            logger.info(
                "Agent node: injecting %d upstream CloudWatch result(s) into context",
                len(cw_results),
            )

        # ------------------------------------------------------------------
        # Cross-node data: collect results from upstream Code Analyzer nodes
        # so the agent can reason about pre-indexed code repositories.
        # ------------------------------------------------------------------
        code_results = {}
        for key, value in context.items():
            if isinstance(value, dict) and value.get('code_analysis_type'):
                code_results[key] = {
                    'code_analysis_type': value.get('code_analysis_type'),
                    'repos_indexed':      value.get('repos_indexed'),
                    'repos_config':       value.get('repos_config'),
                    'pre_summary':        value.get('pre_summary'),
                    'output':             value.get('output'),
                }
        if code_results:
            strategy_context['code_analyzer_context'] = code_results
            logger.info(
                "Agent node: injecting %d upstream Code Analyzer result(s) into context",
                len(code_results),
            )

        # ------------------------------------------------------------------
        # Anomaly-code correlation: when both CW and Code Analyzer results are
        # present, cross-reference anomaly messages with code chunk names and
        # inject the correlation into strategy_context for the agent's initial
        # context block.
        # ------------------------------------------------------------------
        if cw_results and code_results:
            try:
                log_group_to_repo = {}
                for _, cv in code_results.items():
                    for repo_cfg in (cv.get('repos_config') or []):
                        for lg in (repo_cfg.get('logGroups') or []):
                            log_group_to_repo[lg] = repo_cfg.get('name', '')
                if log_group_to_repo:
                    strategy_context['anomaly_code_correlation'] = \
                        await self._correlate_anomalies_to_code(
                            cw_results=cw_results,
                            code_results=code_results,
                            log_group_to_repo=log_group_to_repo,
                        )
            except Exception as _corr_exc:
                logger.debug("Anomaly-code correlation skipped: %s", _corr_exc)

        try:
            react_strategy = ReactStrategy()
            result = await react_strategy.execute(workflow, strategy_context)

            return {
                'status': 'success',
                'output': result.get('final_answer', 'Agent completed (no answer returned).'),
                'final_answer': result.get('final_answer'),
                'messages': result.get('messages', []),
                'message_count': result.get('message_count', 0),
                'tool_calls': result.get('tool_calls', []),
                'model': result.get('model'),
                'provider': result.get('provider'),
                'agent_data': node_data,
            }

        except Exception as e:
            logger.error(
                "Agent node execution failed: %s (node_id=%s, execution_id=%s)",
                e, node_id, execution_id,
            )
            return {
                'status': 'failed',
                'error': str(e),
                'agent_data': node_data,
            }

    async def _execute_batch_agent_node(
        self, node: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a batchAgent node using BatchReactStrategy (map-reduce).

        The node behaves like a regular agent node from the workflow editor's
        perspective — same data shape, same edges — but internally decomposes
        the query into N parallel sub-investigations and synthesises results.

        Extra context keys forwarded to BatchReactStrategy:
          ``batch_targets``     — explicit list of sub-investigation dicts
          ``batch_concurrency`` — max parallel sub-agents (default 5)
          ``batch_timeout``     — per-sub-agent timeout in seconds (default 180)
        """
        from app.workflow.strategies.batch_react import BatchReactStrategy

        node_id      = node.get('id')
        node_data    = node.get('data', {})
        execution_id = context.get('execution_id')

        active   = self.active_executions.get(execution_id, {})
        workflow = active.get('workflow') or {
            'name':  context.get('workflow_name', 'unknown'),
            'nodes': [node],
            'edges': [],
        }

        user_query = (
            context.get('inputs', {}).get('user_query')
            or context.get('inputs', {}).get('query')
            or node_data.get('instructions')
            or node_data.get('description')
            or ''
        )

        if not user_query:
            return {
                'status': 'skipped',
                'output': 'Batch agent node has no user_query or instructions configured.',
                'agent_data': node_data,
            }

        if execution_id not in self.mcp_managers:
            self.mcp_managers[execution_id] = MCPClientManager()

        stream_callback = _AgentStreamCallback(self, execution_id, node_id)

        strategy_context = {
            'execution_id':     execution_id,
            'user_query':       user_query,
            'mcp_manager':      self.mcp_managers[execution_id],
            'inputs':           context.get('inputs', {}),
            'logger':           logger,
            'stream_callback':  stream_callback,
            # Pass through batch-specific overrides if the caller set them.
            'batch_targets':    context.get('batch_targets'),
            'batch_concurrency': context.get('batch_concurrency'),
            'batch_timeout':    context.get('batch_timeout'),
        }

        # Forward upstream CloudWatch analysis (same as agent node).
        cw_results = {
            k: {
                'analysis_type':       v.get('analysis_type'),
                'output':              v.get('output'),
                'log_groups_analyzed': v.get('log_groups_analyzed'),
                'time_range':          v.get('time_range'),
                'alerts':              v.get('alerts'),
            }
            for k, v in context.items()
            if isinstance(v, dict) and v.get('analysis_type')
        }
        if cw_results:
            strategy_context['cloudwatch_context'] = cw_results

        try:
            result = await BatchReactStrategy().execute(workflow, strategy_context)

            return {
                'status':        'success',
                'output':        result.get('final_answer', 'Batch agent completed.'),
                'final_answer':  result.get('final_answer'),
                'sub_results':   result.get('sub_results', []),
                'map_stats':     result.get('map_stats', {}),
                'tool_calls':    result.get('tool_calls', []),
                'model':         result.get('model'),
                'provider':      result.get('provider'),
                'agent_data':    node_data,
            }

        except Exception as e:
            logger.error(
                "Batch agent node execution failed: %s (node_id=%s, execution_id=%s)",
                e, node_id, execution_id,
            )
            return {
                'status':     'failed',
                'error':      str(e),
                'agent_data': node_data,
            }

    async def _execute_tool_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tool node by connecting to MCP server."""
        node_id = node.get('id')
        node_data = node.get('data', {})
        execution_id = context.get('execution_id')
        
        try:
            # Get MCP configuration
            mcp_config = node_data.get('mcpConfig', {})
            if not mcp_config:
                return {
                    "status": "skipped",
                    "output": "No MCP configuration provided",
                    "tool_data": node_data
                }
            
            # Get or create MCP manager for this execution
            if execution_id not in self.mcp_managers:
                self.mcp_managers[execution_id] = MCPClientManager()
            
            mcp_manager = self.mcp_managers[execution_id]
            
            # Connect to MCP server
            logger.info(f"Connecting to MCP server: {node_data.get('label')}")
            
            success = await mcp_manager.connect_server(
                server_id=node_id,
                config=mcp_config
            )
            
            if success:
                # Store this tool as available for orchestrator
                context['connected_tool_server'] = node_id
                
                # Build label → tool node ID mapping for database routing
                if 'db_server_map' not in context:
                    context['db_server_map'] = {}
                label = node_data.get('label', '')
                if label:
                    context['db_server_map'][label] = node_id
                    logger.info(f"Mapped database '{label}' → {node_id}")
                
                return {
                    "status": "success",
                    "output": f"Connected to MCP server: {node_data.get('label')}",
                    "server_id": node_id,
                    "tools": mcp_manager.get_available_tools(node_id),
                    "tool_data": node_data
                }
            else:
                return {
                    "status": "failed",
                    "error": "Failed to connect to MCP server",
                    "tool_data": node_data
                }
                
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "tool_data": node_data
            }
    
    @staticmethod
    def _parse_time_range_minutes(time_range: str) -> int:
        """Convert time range string (e.g. '15m', '1h', '7d') to minutes."""
        units = {'m': 1, 'h': 60, 'd': 1440}
        if not time_range:
            return 60
        suffix = time_range[-1]
        if suffix not in units:
            return 60
        try:
            value = int(time_range[:-1])
            return value * units[suffix]
        except (ValueError, IndexError):
            return 60

    async def _execute_cloudwatch_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute CloudWatch Analyzer node by running log analysis."""
        from app.core.aws_credentials import resolve_aws_credentials
        from app.core.retry import with_retry

        node_data = node.get('data', {})
        log_groups = node_data.get('logGroups', [])
        analysis_type = node_data.get('analysisType', 'error-patterns')
        time_range = node_data.get('timeRange', '1h')
        error_threshold = node_data.get('errorThreshold', 10)
        aws_region = node_data.get('awsRegion', 'us-east-1')
        aws_profile = node_data.get('awsProfile')
        enable_alerts = node_data.get('enableAlerts', False)

        if not log_groups:
            return {
                "status": "failed",
                "error": "No log groups configured for CloudWatch Analyzer"
            }

        log_groups = [lg for lg in log_groups if lg]
        if not log_groups:
            return {
                "status": "failed",
                "error": "All configured log groups are empty"
            }

        time_range_minutes = self._parse_time_range_minutes(time_range)

        # Use shared credential resolver instead of inline duplication.
        credentials, aws_region = await resolve_aws_credentials(
            aws_profile=aws_profile,
            aws_region=aws_region,
        )

        async def _run_analysis() -> Dict[str, Any]:
            """Inner coroutine wrapped by with_retry for transient AWS errors."""
            if analysis_type == 'error-patterns':
                return await analyze_log_patterns(
                    log_group_names=log_groups,
                    time_range_minutes=time_range_minutes,
                    pattern_types=["error", "warning"],
                    region=aws_region,
                    credentials=credentials if credentials else None,
                )
            elif analysis_type == 'activity-summary':
                return await watch_log_groups(
                    log_group_names=log_groups,
                    time_range_minutes=time_range_minutes,
                    region=aws_region,
                    credentials=credentials if credentials else None,
                )
            elif analysis_type == 'anomaly-detection':
                return await detect_anomalies(
                    log_group_names=log_groups,
                    time_range_minutes=time_range_minutes,
                    sensitivity="medium",
                    region=aws_region,
                    credentials=credentials if credentials else None,
                )
            elif analysis_type == 'correlation':
                return await correlate_logs(
                    log_group_names=log_groups,
                    time_range_minutes=time_range_minutes,
                    region=aws_region,
                    credentials=credentials if credentials else None,
                )
            elif analysis_type == 'metrics':
                from app.mcp.tools.metrics_tools import get_metric_data
                metric_queries = node_data.get('metricQueries', [])
                if not metric_queries:
                    return {
                        "status": "failed",
                        "error": "No metricQueries configured for 'metrics' analysis type"
                    }
                return await get_metric_data(
                    metric_queries=metric_queries,
                    time_range_minutes=time_range_minutes,
                    region=aws_region,
                    credentials=credentials if credentials else None,
                )
            elif analysis_type == 'alarms':
                from app.mcp.tools.metrics_tools import list_metric_alarms
                return await list_metric_alarms(
                    alarm_name_prefix=node_data.get('alarmNamePrefix'),
                    state_value=node_data.get('alarmStateFilter'),
                    region=aws_region,
                    credentials=credentials if credentials else None,
                )
            elif analysis_type == 'custom-query':
                from app.mcp.tools.watch_tools import get_watcher, _extract_credentials
                from datetime import datetime, timedelta, timezone as _tz
                custom_query = node_data.get('customInsightsQuery', '').strip()
                if not custom_query:
                    return {
                        "status": "failed",
                        "error": "No customInsightsQuery set for 'custom-query' analysis type"
                    }
                watcher = get_watcher(
                    region=aws_region,
                    **_extract_credentials(credentials if credentials else {}),
                )
                end_time = datetime.now(_tz.utc)
                start_time = end_time - timedelta(minutes=time_range_minutes)
                return await watcher.query_with_insights(
                    log_group_names=log_groups,
                    query_string=custom_query,
                    start_time=start_time,
                    end_time=end_time,
                )
            else:
                return {
                    "status": "failed",
                    "error": f"Unknown analysis type: {analysis_type}"
                }

        try:
            result = await with_retry(_run_analysis, max_retries=2)

            if isinstance(result, dict) and result.get('error'):
                return {
                    "status": "failed",
                    "error": result.get('message', str(result.get('error')))
                }

            output_summary = self._build_cloudwatch_summary(result, analysis_type, log_groups)

            alerts = []
            if enable_alerts and isinstance(result, dict):
                alerts = self._check_cloudwatch_alerts(result, analysis_type, error_threshold)

            # Use the workflow's LLM to produce an agent-style analysis.
            # _analyze_cloudwatch_with_llm returns a 3-tuple
            # (text, model_name, structured_dict); the structured_dict may be
            # None for providers that don't support structured output.
            llm_result = await self._analyze_cloudwatch_with_llm(
                raw_result=result,
                analysis_type=analysis_type,
                log_groups=log_groups,
                time_range=time_range,
                alerts=alerts,
                execution_id=context.get('execution_id'),
            )
            if llm_result and len(llm_result) == 3:
                llm_analysis, model_used, structured_analysis = llm_result
            else:
                llm_analysis, model_used = (llm_result or (None, None))
                structured_analysis = None

            return {
                "status": "success",
                "output": llm_analysis or output_summary,
                "analysis_type": analysis_type,
                "log_groups_analyzed": log_groups,
                "time_range": time_range,
                "data": result,
                "alerts": alerts if alerts else None,
                "model": model_used,
                "structured_analysis": structured_analysis,
            }

        except Exception as e:
            logger.error(f"CloudWatch Analyzer execution failed: {e}")
            return {
                "status": "failed",
                "error": str(e)
            }

    # ------------------------------------------------------------------
    # Code Analyzer node
    # ------------------------------------------------------------------

    async def _execute_code_analyzer_node(
        self,
        node: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a Code Analyzer node.

        Phase 1 (fast, sync): index any un-indexed / stale repos, optionally
        run a pre-execution semantic summary search.
        Phase 2 (enrichment): fired in the background by CodeIndexer itself.

        Returns a result dict with ``code_analysis_type`` discriminator so the
        downstream agent node can pick it up from ``context``.
        """
        import os
        import re as _re
        from datetime import datetime, timezone

        from app.core.security import check_path, PathJailError
        from app.services.code_indexer import CodeIndexer
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text as _text

        node_data    = node.get('data', {})
        execution_id = context.get('execution_id')
        node_id      = node.get('id')
        repos        = node_data.get('repos', [])
        auto_index   = node_data.get('autoIndex', True)
        stale_hours  = node_data.get('staleAfterHours', 24)
        pre_summary  = node_data.get('preSummary', False)

        REPOS_BASE_PATH = os.getenv('REPOS_BASE_PATH', '/tmp/indexed_repos')

        if not repos:
            return {
                'status': 'failed',
                'error': 'No repos configured for Code Analyzer node.',
                'code_analysis_type': 'pre_summary',
            }

        # ── Security guards ────────────────────────────────────────────────
        try:
            # Lazy import to keep the import chain light until needed
            from app.core.security import scan_injection, InjectionError  # type: ignore
        except ImportError:
            # scan_injection may not exist yet on older deployments — define no-op
            def scan_injection(text: str) -> None:  # type: ignore
                pass
            class InjectionError(Exception):  # type: ignore
                pass

        for repo in repos:
            try:
                scan_injection(repo.get('name', ''))
                scan_injection(repo.get('path', ''))
            except InjectionError as exc:
                return {
                    'status': 'failed',
                    'error': f'Injection detected in repo config: {exc}',
                    'code_analysis_type': 'pre_summary',
                }
            try:
                check_path(repo.get('path', ''), jail=REPOS_BASE_PATH)
            except PathJailError as exc:
                return {
                    'status': 'failed',
                    'error': f'Repo path outside allowed root ({REPOS_BASE_PATH}): {exc}',
                    'code_analysis_type': 'pre_summary',
                }

        # ── Per-repo indexing ──────────────────────────────────────────────
        repos_newly_indexed:   List[str] = []
        repos_stale_reindexed: List[str] = []
        repos_already_indexed: List[str] = []
        cg_stale_warning: Optional[str] = None

        indexer = CodeIndexer()
        now = datetime.now(timezone.utc)

        for repo in repos:
            repo_name  = repo.get('name', '')
            local_path = repo.get('path', '')
            language   = repo.get('language', 'python')

            if not repo_name or not local_path:
                continue

            # Try advisory lock to prevent concurrent indexing of the same repo
            lock_key = abs(hash(repo_name)) % (2 ** 31)
            acquired = False
            try:
                async with AsyncSessionLocal() as lock_session:
                    result_lock = await lock_session.execute(
                        _text('SELECT pg_try_advisory_lock(:key)'),
                        {'key': lock_key},
                    )
                    acquired = bool(result_lock.scalar())

                    if not acquired:
                        logger.info(
                            'codeAnalyzer: repo %r is being indexed by another process — skipping',
                            repo_name,
                        )
                        repos_already_indexed.append(repo_name)
                        continue

                    # Check freshness
                    fresh_result = await lock_session.execute(
                        _text("""
                            SELECT COUNT(*) AS cnt, MAX(indexed_at) AS last_indexed
                              FROM code_chunks
                             WHERE repo_name = :rn
                        """),
                        {'rn': repo_name},
                    )
                    row = fresh_result.one_or_none()
                    chunk_count  = int(row.cnt) if row else 0
                    last_indexed = row.last_indexed if row else None

                    needs_index = (
                        chunk_count == 0
                        or (
                            auto_index
                            and last_indexed is not None
                            and (now - last_indexed.replace(tzinfo=timezone.utc)).total_seconds()
                                > stale_hours * 3600
                        )
                    )

                    # Check call-graph staleness (warn if graph is older than chunks)
                    cg_result = await lock_session.execute(
                        _text("""
                            SELECT MAX(indexed_at) AS cg_indexed
                              FROM code_calls
                             WHERE caller_repo = :rn
                        """),
                        {'rn': repo_name},
                    )
                    cg_row = cg_result.one_or_none()
                    cg_stale_warning: Optional[str] = None
                    if (
                        last_indexed and cg_row and cg_row.cg_indexed
                        and (last_indexed - cg_row.cg_indexed.replace(tzinfo=timezone.utc)).total_seconds()
                            > 1800  # 30-minute grace period
                    ):
                        cg_stale_warning = (
                            f'⚠️  Call graph for {repo_name} may be stale '
                            f'(last updated {cg_row.cg_indexed.isoformat()}, '
                            f'chunks updated {last_indexed.isoformat()}). '
                            'Structural traces may reflect deleted or renamed functions. '
                            'Verify with code_get_function.'
                        )

            except Exception as lock_exc:
                logger.warning(
                    'codeAnalyzer: advisory lock check failed for %s: %s',
                    repo_name, lock_exc,
                )
                needs_index = chunk_count == 0

            if not needs_index:
                repos_already_indexed.append(repo_name)
                continue

            # Build a progress callback that publishes SSE events
            async def _on_progress(done: int, total: int, _rn: str = repo_name) -> None:
                try:
                    await self._publish_event(execution_id, 'node_progress', {
                        'message':      f'Indexing {_rn}: {done}/{total} files',
                        'node_id':      node_id,
                        'progress_pct': round(done / total * 100) if total else 0,
                    })
                except Exception:
                    pass  # SSE disconnect — indexing continues

            try:
                await indexer.index_repo(
                    repo_name=repo_name,
                    local_path=local_path,
                    language=language,
                    progress_callback=_on_progress,
                )
                if chunk_count > 0:
                    repos_stale_reindexed.append(repo_name)
                else:
                    repos_newly_indexed.append(repo_name)
                logger.info('codeAnalyzer: indexed repo %s', repo_name)
            except Exception as idx_exc:
                logger.error(
                    'codeAnalyzer: failed to index %s: %s', repo_name, idx_exc,
                )

        all_indexed = repos_newly_indexed + repos_stale_reindexed + repos_already_indexed
        repos_config_with_lg = repos  # pass full config to agent so logGroupToRepo mapping is available

        # ── Optional pre-execution summary ────────────────────────────────
        pre_summary_results: Dict[str, Any] = {}
        if pre_summary and all_indexed:
            inputs = context.get('inputs', {})
            search_query = (
                inputs.get('service_name')
                or inputs.get('error_signature')
                or inputs.get('component')
                or inputs.get('user_query', '')[:200]
            )
            if search_query:
                from app.mcp.tools.code_tools import search_code as _search_code
                for repo in repos:
                    rn = repo.get('name', '')
                    if rn not in all_indexed:
                        continue
                    try:
                        hits = await _search_code(
                            query=search_query, repo=rn, limit=5,
                        )
                        pre_summary_results[rn] = hits
                    except Exception as se:
                        logger.debug('codeAnalyzer pre-summary search failed for %s: %s', rn, se)

        # ── Build output text ──────────────────────────────────────────────
        parts: List[str] = []
        if repos_newly_indexed:
            parts.append(f'Newly indexed: {", ".join(repos_newly_indexed)}')
        if repos_stale_reindexed:
            parts.append(f'Re-indexed (stale): {", ".join(repos_stale_reindexed)}')
        if repos_already_indexed:
            parts.append(f'Already indexed: {", ".join(repos_already_indexed)}')
        output_text = '; '.join(parts) or 'No repos indexed.'

        if cg_stale_warning:
            output_text += '\n' + cg_stale_warning

        return {
            'status':               'success',
            'output':               output_text,
            'code_analysis_type':   'pre_summary',
            'repos_indexed':        all_indexed,
            'repos_newly_indexed':  repos_newly_indexed,
            'repos_stale_reindexed': repos_stale_reindexed,
            'repos_already_indexed': repos_already_indexed,
            'repos_config':         repos_config_with_lg,
            'pre_summary':          pre_summary_results or None,
        }

    async def _correlate_anomalies_to_code(
        self,
        cw_results: Dict[str, Any],
        code_results: Dict[str, Any],
        log_group_to_repo: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Lightweight anomaly→code correlation.  No LLM — regex + exact match.

        Extracts log groups and error/function names from CloudWatch anomaly
        text, maps to repo names via ``log_group_to_repo``, then matches
        against ``code_chunks.name``.

        Returns a list of correlation dicts suitable for injection into the
        agent's initial context block.
        """
        import re as _re
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text as _text

        _ENTITY_RE = _re.compile(
            r'(?:ERROR|Exception|error|exception)\s+in\s+([\w\.]+)',
            _re.IGNORECASE,
        )

        correlations: List[Dict[str, Any]] = []

        for _key, cw_val in cw_results.items():
            output_text = cw_val.get('output', '') or ''
            log_groups  = cw_val.get('log_groups_analyzed') or []

            for lg in log_groups:
                repo_name = log_group_to_repo.get(lg)
                if not repo_name:
                    continue

                # Extract function/class names from anomaly text
                matches = _ENTITY_RE.findall(output_text)
                for match in matches:
                    func_name = match.split('.')[-1]  # "auth.service.authenticate_user" → "authenticate_user"
                    if not func_name:
                        continue
                    # Exact-match lookup in code_chunks
                    try:
                        async with AsyncSessionLocal() as session:
                            res = await session.execute(
                                _text("""
                                    SELECT name, file_path, line_start
                                      FROM code_chunks
                                     WHERE repo_name = :rn
                                       AND name = :fn
                                     LIMIT 1
                                """),
                                {'rn': repo_name, 'fn': func_name},
                            )
                            row = res.one_or_none()
                        if row:
                            correlations.append({
                                'log_group':  lg,
                                'repo':       repo_name,
                                'anomaly_snippet': output_text[:200],
                                'matched_function': row.name,
                                'matched_file':     row.file_path,
                                'matched_line':     row.line_start,
                                'confidence':       'exact_name_match',
                                'suggestion': (
                                    f'code_trace_flow(entry_function="{row.name}", '
                                    f'repo="{repo_name}")'
                                ),
                            })
                    except Exception:
                        pass

        return correlations

    async def _analyze_cloudwatch_with_llm(
        self,
        raw_result: Dict[str, Any],
        analysis_type: str,
        log_groups: List[str],
        time_range: str,
        alerts: list,
        execution_id: str,
    ) -> tuple:
        """Use the workflow's LLM node to produce a structured analysis.

        Attempts structured output (Pydantic schema) for providers that
        support it (OpenAI, Anthropic, Bedrock with Claude).  Falls back to
        free-form text for others (Ollama, Groq).

        Returns:
            Tuple of (analysis_text, model_name).  Both are ``None`` when
            no LLM is available or the call fails (graceful fallback to
            the static summary built by ``_build_cloudwatch_summary``).
        """
        try:
            from app.workflow.strategies.react import ReactStrategy
            from langchain_core.messages import SystemMessage, HumanMessage
            from pydantic import BaseModel as _PydanticBase, Field as _Field
            from typing import List as _List
            import json

            # ----------------------------------------------------------
            # Structured output schema
            # ----------------------------------------------------------
            class CloudWatchAnalysisSummary(_PydanticBase):
                """Structured CloudWatch incident analysis for on-call engineers."""
                headline: str = _Field(
                    description="One-sentence incident summary suitable for Slack or PagerDuty."
                )
                severity: str = _Field(
                    description="Overall severity: critical | high | medium | low | none."
                )
                key_findings: _List[str] = _Field(
                    description="2-5 bullet points describing what was found."
                )
                root_cause_hypothesis: str = _Field(
                    description=(
                        "Most likely root cause based on the data. "
                        "Use 'Unknown' if there is insufficient evidence."
                    )
                )
                recommended_actions: _List[str] = _Field(
                    description="Ordered list of actions for the on-call engineer."
                )
                related_services: _List[str] = _Field(
                    default_factory=list,
                    description="Other services likely involved, inferred from patterns.",
                )
                confidence: str = _Field(
                    description="Confidence in this analysis: high | medium | low."
                )

            active = self.active_executions.get(execution_id, {})
            workflow = active.get('workflow')
            if not workflow:
                return None, None, None

            nodes = workflow.get('nodes', [])
            if not any(n.get('type') == 'llm' for n in nodes):
                logger.debug("CloudWatch LLM analysis skipped: no LLM node in workflow")
                return None, None, None

            strategy = ReactStrategy()
            llm_config = await strategy._resolve_llm_config(workflow)
            llm = strategy._build_llm(llm_config)
            model_name = llm_config.get('model')

            # Serialize raw data (truncate to stay within context limits).
            data_str = json.dumps(raw_result, indent=2, default=str)
            if len(data_str) > 15_000:
                data_str = data_str[:15_000] + "\n... [truncated]"

            alert_section = ""
            if alerts:
                alert_section = (
                    "\n\nAlerts triggered:\n"
                    + json.dumps(alerts, indent=2, default=str)
                )

            system_prompt = (
                "You are an expert CloudWatch log analysis engineer for KYC Protect. "
                "Analyze the provided log analysis results and give a clear, actionable summary. "
                "Be concise but thorough. Focus on what matters for an on-call engineer."
            )

            human_prompt = (
                f"Analyze these **{analysis_type}** results from CloudWatch log groups "
                f"{log_groups} over the last **{time_range}**.\n\n"
                f"Raw analysis data:\n```json\n{data_str}\n```"
                f"{alert_section}"
            )

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]

            # ----------------------------------------------------------
            # Try structured output first (OpenAI, Anthropic, Bedrock).
            # Fall back gracefully to free-form text for other providers.
            # ----------------------------------------------------------
            provider = llm_config.get('provider', '').lower()
            _structured_providers = {'openai', 'anthropic', 'bedrock', 'azure'}

            structured_analysis: Optional[Dict[str, Any]] = None
            analysis_text: Optional[str] = None

            if provider in _structured_providers:
                try:
                    structured_llm = llm.with_structured_output(CloudWatchAnalysisSummary)
                    parsed: CloudWatchAnalysisSummary = await asyncio.wait_for(
                        structured_llm.ainvoke(messages),
                        timeout=60.0,
                    )
                    structured_analysis = parsed.model_dump()
                    # Build human-readable text from structured fields.
                    analysis_text = (
                        f"**{parsed.headline}**\n\n"
                        f"Severity: {parsed.severity.upper()} (confidence: {parsed.confidence})\n\n"
                        f"**Key Findings:**\n"
                        + "\n".join(f"- {f}" for f in parsed.key_findings)
                        + f"\n\n**Root Cause:** {parsed.root_cause_hypothesis}\n\n"
                        f"**Recommended Actions:**\n"
                        + "\n".join(f"{i+1}. {a}" for i, a in enumerate(parsed.recommended_actions))
                        + (
                            f"\n\n**Related Services:** {', '.join(parsed.related_services)}"
                            if parsed.related_services else ""
                        )
                    )
                    logger.info(
                        "CloudWatch structured LLM analysis complete (severity=%s, model=%s)",
                        parsed.severity, model_name,
                    )
                except Exception as struct_err:
                    logger.warning(
                        "CloudWatch structured output failed, falling back to free-form: %s",
                        struct_err,
                    )

            # Free-form fallback (or primary path for unsupported providers).
            if analysis_text is None:
                free_form_prompt = human_prompt + (
                    "\n\nProvide:\n"
                    "1. A concise summary of findings\n"
                    "2. Key patterns or anomalies identified\n"
                    "3. Severity assessment\n"
                    "4. Recommended next steps\n"
                )
                response = await asyncio.wait_for(
                    llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=free_form_prompt),
                    ]),
                    timeout=60.0,
                )
                analysis_text = str(response.content) if response.content else None
                if analysis_text:
                    logger.info(
                        "CloudWatch free-form LLM analysis complete (%d chars, model=%s)",
                        len(analysis_text), model_name,
                    )

            # Return text + optional structured dict via a 3-tuple so the
            # caller can store both.  The caller only unpacks 2 values so we
            # attach the structured dict as an attribute on a wrapper instead
            # — cleanest without touching the return type contract.
            return analysis_text, model_name, structured_analysis

        except Exception as e:
            logger.warning("CloudWatch LLM analysis failed (falling back to static): %s", e)
            return None, None, None

    @staticmethod
    def _build_cloudwatch_summary(result: Dict[str, Any], analysis_type: str, log_groups: list) -> str:
        """Build a human-readable summary from CloudWatch analysis results."""
        if not isinstance(result, dict):
            return f"Analysis complete ({analysis_type})"

        if analysis_type == 'error-patterns':
            patterns = result.get('patterns', {})
            pattern_summaries = []
            for ptype, pdata in patterns.items():
                status = pdata.get('status', 'unknown')
                if status == 'Complete':
                    data_count = len(pdata.get('data', []))
                    pattern_summaries.append(f"{ptype}: {data_count} results")
                else:
                    pattern_summaries.append(f"{ptype}: {status}")
            return f"Pattern analysis: {', '.join(pattern_summaries)} across {len(log_groups)} log group(s)"

        elif analysis_type == 'activity-summary':
            summary = result.get('summary', {})
            total_events = summary.get('total_events', 0)
            successful = summary.get('successful_log_groups', 0)
            return f"Activity summary: {total_events} events across {successful}/{len(log_groups)} log group(s)"

        elif analysis_type == 'anomaly-detection':
            anomaly_summary = result.get('summary', {})
            total = anomaly_summary.get('total_anomalies', 0)
            critical = anomaly_summary.get('critical_severity', 0)
            high = anomaly_summary.get('high_severity', 0)
            medium = anomaly_summary.get('medium_severity', 0)
            low = anomaly_summary.get('low_severity', 0)
            return f"Anomaly detection: {total} anomalies (critical: {critical}, high: {high}, medium: {medium}, low: {low})"

        elif analysis_type == 'correlation':
            summary = result.get('summary', {})
            total_events = summary.get('total_events', 0)
            services = summary.get('services_involved', [])
            return f"Log correlation: {total_events} events across {len(services)} service(s)"

        return f"Analysis complete ({analysis_type})"

    @staticmethod
    def _check_cloudwatch_alerts(result: Dict[str, Any], analysis_type: str, error_threshold: int) -> list:
        """Check analysis results against thresholds and generate alerts."""
        alerts = []

        if analysis_type == 'error-patterns':
            patterns = result.get('patterns', {})
            if not isinstance(patterns, dict):
                patterns = {}
            error_data = patterns.get('error', {})
            if not isinstance(error_data, dict):
                error_data = {}
            for entry in error_data.get('data', []):
                if not isinstance(entry, (list, tuple)):
                    continue
                for field in entry:
                    if not isinstance(field, dict):
                        continue
                    if field.get('field') == 'count()':
                        try:
                            count = float(field.get('value', 0))
                            if count > error_threshold:
                                alerts.append({
                                    "type": "error_threshold_exceeded",
                                    "message": f"Error count {count} exceeds threshold {error_threshold}",
                                    "severity": "high" if count > error_threshold * 2 else "medium"
                                })
                        except (ValueError, TypeError):
                            pass

            # Check unique error patterns — alert when a single pattern
            # exceeds the threshold (indicates a repeated systematic issue).
            for pattern_entry in result.get('unique_patterns', []):
                if not isinstance(pattern_entry, (list, tuple)):
                    continue
                for field in pattern_entry:
                    if not isinstance(field, dict):
                        continue
                    if field.get('field') == 'occurrence_count':
                        try:
                            occ_count = float(field.get('value', 0))
                            if occ_count > error_threshold:
                                # Find the pattern text
                                pattern_text = ""
                                for f2 in pattern_entry:
                                    if isinstance(f2, dict) and f2.get('field') == 'error_pattern':
                                        pattern_text = (f2.get('value') or '')[:120]
                                        break
                                alerts.append({
                                    "type": "repeated_error_pattern",
                                    "message": (
                                        f"Error pattern occurred {int(occ_count)} times "
                                        f"(threshold {error_threshold}): {pattern_text}"
                                    ),
                                    "severity": "high" if occ_count > error_threshold * 3 else "medium",
                                })
                        except (ValueError, TypeError):
                            pass

        elif analysis_type == 'anomaly-detection':
            for anomaly in result.get('anomalies', []):
                if not isinstance(anomaly, dict):
                    continue
                z_info = ""
                if anomaly.get('z_score'):
                    z_info = f", z-score={anomaly['z_score']}"
                deviation_info = ""
                if anomaly.get('deviation_factor'):
                    deviation_info = f" ({anomaly['deviation_factor']}x baseline)"
                alerts.append({
                    "type": "anomaly_detected",
                    "message": (
                        f"Anomaly at {anomaly.get('timestamp')}: "
                        f"{anomaly.get('current_count')} events{deviation_info}{z_info}"
                    ),
                    "severity": anomaly.get('severity', 'medium')
                })

        return alerts

    def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an execution."""
        return self.active_executions.get(execution_id)
    
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
        from app.engine.pocketflow import WorkflowGraph, default_registry

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
        
        self.active_executions.pop(execution_id, None)
        self.event_queues.pop(execution_id, None)


# Global executor instance
visual_executor = VisualWorkflowExecutor()
