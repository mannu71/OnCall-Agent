"""
Orchestrator Strategy for SQL Workflows

Executes predefined SQL-based workflows in a sequential manner.
This strategy is used for scheduled reports and data queries where
the steps are known in advance.
"""

from typing import Dict, Any, List, Optional
import logging
import re

from app.workflow.strategies.base import BaseStrategy
from app.repositories.db_repository import db_repository

logger = logging.getLogger(__name__)


class OrchestratorStrategy(BaseStrategy):
    """
    Strategy for executing SQL orchestrator workflows.
    
    Use cases:
    - Daily reports
    - Scheduled data queries
    - Multi-step SQL workflows with dependencies
    """
    
    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        """
        Check if workflow is an orchestrator workflow.
        
        A workflow is considered an orchestrator workflow if it has:
        - An 'orchestrator' node (defines SQL workflow)
        - One or more 'tool' nodes (MCP servers for database access)
        
        Args:
            workflow: The workflow definition
            
        Returns:
            True if this is an orchestrator workflow
        """
        nodes = workflow.get("nodes", [])
        
        if not isinstance(nodes, list):
            return False
        
        has_orchestrator_node = any(
            node.get("type") == "orchestrator" for node in nodes
        )
        
        return has_orchestrator_node
    
    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute orchestrator workflow.
        
        Args:
            workflow: The workflow definition
            context: Execution context with variables, mcp_manager, etc.
            
        Returns:
            Execution result
        """
        execution_id = context.get("execution_id")
        logger_instance = context.get("logger", logger)
        mcp_manager = context.get("mcp_manager")
        variables = context.get("variables", {})
        
        logger_instance.info(
            "OrchestratorStrategy: Starting execution",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow.get("id"),
                "workflow_name": workflow.get("name")
            }
        )
        
        try:
            # Parse workflow to extract execution graph
            execution_graph = await self._parse_workflow(workflow)
            
            logger_instance.info(
                "OrchestratorStrategy: Workflow parsed",
                extra={
                    "execution_id": execution_id,
                    "tool_count": len(execution_graph.get("tools", [])),
                    "step_count": len(execution_graph.get("steps", []))
                }
            )
            
            # Connect to MCP servers
            await self._connect_tools(execution_graph.get("tools", []), mcp_manager)
            
            # Execute workflow steps
            results = await self._execute_steps(
                execution_graph.get("steps", []),
                mcp_manager,
                variables,
                logger_instance
            )
            
            # Format output
            output = self._format_output(results, execution_graph)
            
            logger_instance.info(
                "OrchestratorStrategy: Execution completed",
                extra={
                    "execution_id": execution_id,
                    "success": True,
                    "step_count": len(results)
                }
            )
            
            return {
                "type": "orchestrator",
                "success": True,
                "output": output,
                "results": results,
                "error": None
            }
            
        except Exception as error:
            logger_instance.error(
                "OrchestratorStrategy: Execution failed",
                extra={
                    "execution_id": execution_id,
                    "error": str(error)
                },
                exc_info=True
            )
            raise
    
    def validate_workflow(self, workflow: Dict[str, Any]) -> bool:
        """
        Validate orchestrator workflow structure.
        
        Args:
            workflow: The workflow definition
            
        Returns:
            True if valid
            
        Raises:
            ValueError: If workflow is invalid
        """
        super().validate_workflow(workflow)
        
        nodes = workflow.get("nodes", [])
        orchestrator_node = next(
            (n for n in nodes if n.get("type") == "orchestrator"),
            None
        )
        
        if not orchestrator_node:
            raise ValueError("Orchestrator workflow must have an orchestrator node")
        
        # Check for workflow definition or SQL file
        node_data = orchestrator_node.get("data", {})
        has_definition = "workflowDefinition" in node_data
        has_sql_file = "sqlFile" in node_data or "fileName" in node_data
        
        if not has_definition and not has_sql_file:
            raise ValueError(
                "Orchestrator node must have either workflowDefinition or sqlFile"
            )
        
        # Check for connected tools
        has_tools = any(n.get("type") == "tool" for n in nodes)
        if not has_tools:
            raise ValueError("Orchestrator workflow must have at least one tool node")
        
        # Check for edges connecting tools to orchestrator
        edges = workflow.get("edges", [])
        orchestrator_id = orchestrator_node.get("id")
        has_tool_connections = any(
            edge.get("target") == orchestrator_id and
            edge.get("targetHandle") == "tool"
            for edge in edges
        )
        
        if not has_tool_connections:
            raise ValueError("Orchestrator must be connected to at least one tool")
        
        return True
    
    async def _parse_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse workflow to extract execution graph.
        
        Args:
            workflow: The workflow definition
            
        Returns:
            Execution graph with tools and steps
        """
        nodes = workflow.get("nodes", [])
        edges = workflow.get("edges", [])
        
        # Find orchestrator node
        orchestrator_node = next(
            (n for n in nodes if n.get("type") == "orchestrator"),
            None
        )
        
        # Extract workflow definition
        node_data = orchestrator_node.get("data", {})
        workflow_def = node_data.get("workflowDefinition", {})
        
        # Find connected tool nodes
        orchestrator_id = orchestrator_node.get("id")
        tool_edges = [
            e for e in edges
            if e.get("target") == orchestrator_id and e.get("targetHandle") == "tool"
        ]
        
        tool_node_ids = [e.get("source") for e in tool_edges]
        tool_nodes = [n for n in nodes if n.get("id") in tool_node_ids]
        
        # Extract tools configuration
        tools = []
        for tool_node in tool_nodes:
            tool_data = tool_node.get("data", {})
            server_name = tool_data.get("serverName", "")
            
            # Check if command is embedded or needs database lookup
            if tool_data.get("command"):
                # Use embedded configuration
                tools.append({
                    "name": server_name,
                    "command": tool_data.get("command", ""),
                    "args": tool_data.get("args", []),
                    "env": tool_data.get("env", {})
                })
            elif server_name:
                # Load from database
                db_config = await db_repository.get_mcp_server_by_name(server_name)
                if db_config:
                    tools.append({
                        "name": server_name,
                        "command": db_config.get("command", ""),
                        "args": db_config.get("args", []),
                        "env": db_config.get("env", {})
                    })
                    logger.info(f"Loaded MCP server '{server_name}' from database")
                else:
                    logger.warning(f"MCP server '{server_name}' not found in database")
                    # Add with minimal config
                    tools.append({
                        "name": server_name,
                        "command": "",
                        "args": [],
                        "env": {}
                    })
        
        # Extract steps from workflow definition
        steps = workflow_def.get("steps", [])
        
        return {
            "tools": tools,
            "steps": steps,
            "output": workflow_def.get("output", {})
        }
    
    async def _connect_tools(
        self,
        tools: List[Dict[str, Any]],
        mcp_manager: Any
    ) -> None:
        """
        Connect to MCP tool servers.

        Args:
            tools: List of tool configurations
            mcp_manager: MCPClientManager instance
        """
        for tool in tools:
            server_id = tool["name"]
            if not mcp_manager.is_connected(server_id):
                config = {
                    "command": tool.get("command", ""),
                    "args": tool.get("args", []),
                    "env": tool.get("env", {}),
                }
                await mcp_manager.connect_server(server_id, config)
    
    async def _execute_steps(
        self,
        steps: List[Dict[str, Any]],
        mcp_manager: Any,
        variables: Dict[str, Any],
        logger_instance: Any
    ) -> List[Dict[str, Any]]:
        """
        Execute workflow steps sequentially.
        
        Args:
            steps: List of step definitions
            mcp_manager: MCP client manager
            variables: Variables for substitution
            logger_instance: Logger instance
            
        Returns:
            List of step results
        """
        results = []
        step_variables = {**variables}
        
        for step in steps:
            step_name = step.get("name", "unnamed")
            server_name = step.get("server", "")
            tool_name = step.get("tool", "")
            args = step.get("args", {})
            
            logger_instance.info(f"Executing step: {step_name}")
            
            try:
                # Substitute variables in arguments
                substituted_args = self._substitute_variables(args, step_variables)
                
                # Execute tool
                result = await mcp_manager.execute_tool(
                    server_name,
                    tool_name,
                    substituted_args
                )
                
                # Store result for next steps
                if "output" in step:
                    step_variables[step["output"]] = result
                
                results.append({
                    "step": step_name,
                    "success": True,
                    "result": result
                })
                
            except Exception as error:
                logger_instance.error(f"Step failed: {step_name} - {str(error)}")
                results.append({
                    "step": step_name,
                    "success": False,
                    "error": str(error)
                })
                
                # Stop on error unless configured to continue
                if not step.get("continueOnError", False):
                    raise
        
        return results
    
    def _substitute_variables(
        self,
        args: Dict[str, Any],
        variables: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Substitute variables in arguments.
        
        Supports ${variable_name} syntax for variable substitution.
        Variables can be nested using dot notation: ${step1.result.field}
        
        Args:
            args: Arguments dictionary
            variables: Variables for substitution
            
        Returns:
            Arguments with substituted values
        """
        def get_nested_value(obj: Any, path: str) -> Any:
            """Get a nested value using dot notation."""
            keys = path.split('.')
            current = obj
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                elif isinstance(current, list) and key.isdigit():
                    idx = int(key)
                    current = current[idx] if 0 <= idx < len(current) else None
                else:
                    return None
                if current is None:
                    return None
            return current
        
        def substitute_value(value: Any) -> Any:
            """Recursively substitute variables in a value."""
            if isinstance(value, str):
                # Pattern to match ${variable_name}
                pattern = r'\$\{([^}]+)\}'
                
                def replace_var(match):
                    var_path = match.group(1)
                    var_value = get_nested_value(variables, var_path)
                    if var_value is None:
                        # Keep original if variable not found
                        logger.warning(f"Variable '{var_path}' not found, keeping original")
                        return match.group(0)
                    return str(var_value)
                
                return re.sub(pattern, replace_var, value)
            
            elif isinstance(value, dict):
                return {k: substitute_value(v) for k, v in value.items()}
            
            elif isinstance(value, list):
                return [substitute_value(item) for item in value]
            
            return value
        
        return substitute_value(args)
    
    def _format_output(
        self,
        results: List[Dict[str, Any]],
        execution_graph: Dict[str, Any]
    ) -> Any:
        """
        Format workflow output.
        
        Args:
            results: Step results
            execution_graph: Execution graph
            
        Returns:
            Formatted output
        """
        # TODO: Implement output formatting based on execution_graph.output
        # For now, return the last successful result
        for result in reversed(results):
            if result.get("success"):
                return result.get("result")
        
        return None
