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

logger = logging.getLogger(__name__)

# Node types that contain only connection metadata (excluded from sanitized output)
_TOOL_NODE_KEYS = {'server_id', 'tool_data'}


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
        for field in ('status', 'output', 'trigger_time'):
            if field in value:
                node_result[field] = value[field]
        
        if 'queries_executed' in value:
            node_result['queries_executed'] = value['queries_executed']
            node_result['failures'] = value.get('failures', 0)
            node_result['results'] = value.get('results', [])
            if value.get('error'):
                node_result['error'] = value['error']
        
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
            await execution_repo.save({
                **result,
                "workflow_name": workflow.get('name'),
                "workflow_id": workflow.get('id'),
                "events": self.active_executions[execution_id].get('events', [])
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
            'scheduler': self._execute_scheduler_node,
            'orchestrator': self._execute_orchestrator_node,
            'agent': self._execute_agent_node,
            'tool': self._execute_tool_node,
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
        sql_content = node_data.get('sqlContent')
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
        """Execute agent node."""
        # Agent nodes are processed by the orchestrator - this is a pass-through
        logger.debug(f"Agent node executed: {node.get('id')}")
        
        return {
            "status": "success",
            "output": "Agent task completed",
            "agent_data": node.get('data', {})
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
    
    def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an execution."""
        return self.active_executions.get(execution_id)
    
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
