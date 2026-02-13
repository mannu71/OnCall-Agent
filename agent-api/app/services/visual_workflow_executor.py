"""Visual workflow executor for node-based workflows."""
import asyncio
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from collections import defaultdict
import os
from pathlib import Path

from app.repositories import ExecutionRepository
from app.services.mcp_client_manager import MCPClientManager
from app.services.sql_orchestrator import SQLOrchestrator

logger = logging.getLogger(__name__)

class WorkflowExecutionEvent:
    """Represents an execution event."""
    def __init__(self, event_type: str, data: Dict[str, Any]):
        self.event_type = event_type
        self.data = data
        self.timestamp = datetime.utcnow().isoformat() + 'Z'
    
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
        event = WorkflowExecutionEvent(event_type, data)
        
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
                print(f"Error publishing event: {e}")
    
    async def execute_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a visual workflow and return results."""
        execution_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        
        logger.info(f"Starting workflow execution: {workflow.get('name')} (ID: {execution_id})")
        
        # Initialize execution state
        self.active_executions[execution_id] = {
            "id": execution_id,
            "workflow_name": workflow.get('name'),
            "workflow_id": workflow.get('id'),
            "status": "running",
            "start_time": start_time.isoformat() + 'Z',
            "nodes_completed": [],
            "events": []
        }
        
        try:
            await self._publish_event(execution_id, "workflow_started", {
                "execution_id": execution_id,
                "workflow_name": workflow.get('name'),
                "workflow_id": workflow.get('id')
            })
            
            # Get nodes and edges
            nodes = workflow.get('nodes', [])
            edges = workflow.get('edges', [])
            
            logger.info(f"Workflow has {len(nodes)} nodes and {len(edges)} edges")
            
            if not nodes:
                raise ValueError("Workflow has no nodes to execute")
            
            # Build adjacency list for traversal
            adjacency = defaultdict(list)
            in_degree = defaultdict(int)
            
            for edge in edges:
                source = edge.get('source')
                target = edge.get('target')
                if source and target:
                    adjacency[source].append(target)
                    in_degree[target] += 1
            
            # Find starting nodes (nodes with no incoming edges or scheduler nodes)
            start_nodes = []
            for node in nodes:
                node_id = node.get('id')
                if node.get('type') == 'scheduler' or in_degree[node_id] == 0:
                    start_nodes.append(node_id)
            
            if not start_nodes:
                start_nodes = [nodes[0].get('id')]  # Fallback to first node
            
            logger.info(f"Starting nodes: {start_nodes}")
            
            # Execute nodes in topological order using BFS
            node_map = {n.get('id'): n for n in nodes}
            executed = set()
            queue = start_nodes.copy()
            execution_results = {}
            
            while queue:
                node_id = queue.pop(0)
                
                if node_id in executed:
                    continue
                
                # Check if all dependencies are satisfied
                dependencies_met = True
                for parent_id in node_map.keys():
                    if node_id in adjacency.get(parent_id, []):
                        if parent_id not in executed:
                            dependencies_met = False
                            break
                
                if not dependencies_met:
                    queue.append(node_id)  # Re-queue for later
                    continue
                
                node = node_map.get(node_id)
                if not node:
                    continue
                
                logger.info(f"Executing node: {node_id} ({node.get('type')})")
                
                # Execute node
                result = await self._execute_node(execution_id, node, execution_results)
                execution_results[node_id] = result
                executed.add(node_id)
                
                # Add children to queue
                for child_id in adjacency.get(node_id, []):
                    if child_id not in executed and child_id not in queue:
                        queue.append(child_id)
            
            # Workflow completed successfully
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            logger.info(f"Workflow completed successfully: {execution_id} ({duration:.2f}s, {len(executed)} nodes)")
            
            self.active_executions[execution_id]['status'] = 'completed'
            self.active_executions[execution_id]['end_time'] = end_time.isoformat() + 'Z'
            self.active_executions[execution_id]['duration'] = duration
            
            await self._publish_event(execution_id, "workflow_completed", {
                "execution_id": execution_id,
                "status": "success",
                "duration": duration,
                "nodes_executed": len(executed)
            })
            
            result = {
                "execution_id": execution_id,
                "status": "success",
                "start_time": start_time.isoformat() + 'Z',
                "end_time": end_time.isoformat() + 'Z',
                "duration": duration,
                "nodes_executed": len(executed),
                "results": execution_results
            }
            
            # Persist to storage
            try:
                execution_storage.save_execution({
                    **result,
                    "workflow_name": workflow.get('name'),
                    "workflow_id": workflow.get('id'),
                    "events": self.active_executions[execution_id].get('events', [])
                })
            except Exception as e:
                logger.error(f"Failed to save execution to storage: {e}")
            
            return result
        
        except Exception as e:
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            logger.error(f"Workflow execution failed: {execution_id} - {str(e)}")
            
            self.active_executions[execution_id]['status'] = 'failed'
            self.active_executions[execution_id]['error'] = str(e)
            self.active_executions[execution_id]['end_time'] = end_time.isoformat() + 'Z'
            
            await self._publish_event(execution_id, "workflow_failed", {
                "execution_id": execution_id,
                "error": str(e),
                "duration": duration
            })
            
            result = {
                "execution_id": execution_id,
                "status": "failed",
                "error": str(e),
                "start_time": start_time.isoformat() + 'Z',
                "end_time": end_time.isoformat() + 'Z',
                "duration": duration
            }
            
            # Persist to storage
            try:
                execution_storage.save_execution({
                    **result,
                    "workflow_name": workflow.get('name'),
                    "workflow_id": workflow.get('id'),
                    "events": self.active_executions[execution_id].get('events', [])
                })
            except Exception as save_error:
                logger.error(f"Failed to save execution to storage: {save_error}")
            
            return result
    
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
        
        start_time = datetime.utcnow()
        
        # Add execution_id to context for node executors
        context['execution_id'] = execution_id
        
        try:
            # Execute based on node type
            if node_type == 'scheduler':
                result = await self._execute_scheduler_node(node, context)
            elif node_type == 'orchestrator':
                result = await self._execute_orchestrator_node(node, context)
            elif node_type == 'agent':
                result = await self._execute_agent_node(node, context)
            elif node_type == 'tool':
                result = await self._execute_tool_node(node, context)
            else:
                result = {
                    "status": "skipped",
                    "message": f"Unknown node type: {node_type}"
                }
            
            end_time = datetime.utcnow()
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
            end_time = datetime.utcnow()
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
        return {
            "status": "success",
            "output": "Workflow triggered",
            "trigger_time": datetime.utcnow().isoformat() + 'Z'
        }
    
    async def _execute_orchestrator_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute orchestrator node with real SQL execution."""
        node_data = node.get('data', {})
        execution_id = context.get('execution_id')
        
        try:
            # Get SQL file path or content
            sql_file = node_data.get('sqlFile') or node_data.get('fileName')
            sql_content = node_data.get('sqlContent')
            
            if not sql_content:
                if sql_file:
                    # Strip "sql/" prefix if present (it's already in the path)
                    sql_filename = sql_file.replace('sql/', '').replace('sql\\', '')
                    
                    # Load SQL from file
                    sql_path = Path('data/config/sql') / sql_filename
                    if sql_path.exists():
                        sql_content = sql_path.read_text(encoding='utf-8')
                        logger.info(f"Loaded SQL from file: {sql_file}")
                    else:
                        # Try absolute path from container root
                        abs_sql_path = Path('/app/data/config/sql') / sql_filename
                        if abs_sql_path.exists():
                            sql_content = abs_sql_path.read_text(encoding='utf-8')
                            logger.info(f"Loaded SQL from absolute path: {abs_sql_path}")
                        else:
                            return {
                                "status": "failed",
                                "error": f"SQL file not found: {sql_file} (tried {sql_path} and {abs_sql_path})",
                                "data": node_data
                            }
            
            if not sql_content:
                return {
                    "status": "failed",
                    "error": "No SQL content provided",
                    "data": node_data
                }
            
            # Find connected tool nodes for database access
            tool_server_id = context.get('connected_tool_server')
            if not tool_server_id:
                return {
                    "status": "failed",
                    "error": "No database tool connected to orchestrator",
                    "data": node_data
                }
            
            # Get or create MCP manager for this execution
            mcp_manager = self.mcp_managers.get(execution_id)
            if not mcp_manager:
                return {
                    "status": "failed",
                    "error": "MCP manager not initialized",
                    "data": node_data
                }
            
            # Create SQL orchestrator and execute
            orchestrator = SQLOrchestrator(mcp_manager)
            result = await orchestrator.execute_workflow(
                sql_content=sql_content,
                server_id=tool_server_id,
                timeout=900  # 15 minutes per query
            )
            
            return {
                "status": "success" if result.get('success') else "failed",
                "output": f"Executed {result.get('queries_executed', 0)} queries",
                "queries_executed": result.get('queries_executed', 0),
                "failures": result.get('failures', 0),
                "results": result.get('results', []),
                "error": result.get('error'),
                "data": node_data
            }
            
        except Exception as e:
            logger.error(f"Orchestrator execution failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "data": node_data
            }
    
    async def _execute_agent_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute agent node."""
        # Simulate agent work
        await asyncio.sleep(2)
        
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
    
    def cleanup_execution(self, execution_id: str):
        """Clean up execution data and disconnect MCP clients."""
        # Disconnect MCP manager if exists
        if execution_id in self.mcp_managers:
            mcp_manager = self.mcp_managers[execution_id]
            # Schedule async cleanup
            asyncio.create_task(mcp_manager.disconnect_all())
            del self.mcp_managers[execution_id]
        
        if execution_id in self.active_executions:
            del self.active_executions[execution_id]
        if execution_id in self.event_queues:
            del self.event_queues[execution_id]


# Global executor instance
visual_executor = VisualWorkflowExecutor()
