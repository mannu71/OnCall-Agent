"""Tool node handler — connects to MCP server."""
import logging
from typing import Any, Dict

from app.services.mcp_client_manager import MCPClientManager

from . import register

logger = logging.getLogger(__name__)


@register("tool")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
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
        if execution_id not in executor.mcp_managers:
            executor.mcp_managers[execution_id] = MCPClientManager()

        mcp_manager = executor.mcp_managers[execution_id]

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
