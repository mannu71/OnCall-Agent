"""
MCP → LangChain Tool Adapter

Converts live MCP server connections (managed by MCPClientManager) into
LangChain-compatible BaseTool instances so that the LangGraph ReAct agent
can call them dynamically during reasoning.

Each MCP tool is wrapped as a StructuredTool whose _arun() method dispatches
back through the MCPClientManager.execute_tool() coroutine.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Type

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

# Ceiling for a single MCP tool's text output. Unbounded outputs are a token
# sink: the full result is replayed in the message history on every subsequent
# ReAct iteration. Above this many chars we truncate and tell the model how to
# get the rest (narrower args / pagination). Override via env.
MCP_TOOL_OUTPUT_MAX_CHARS = int(os.environ.get("MCP_TOOL_OUTPUT_MAX_CHARS", "8000"))


def _truncate_output(text: str, max_chars: int = MCP_TOOL_OUTPUT_MAX_CHARS) -> str:
    """Cap a tool-output string, appending a hint when truncated."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)
    suffix = (
        f"\n…[truncated; showing {max_chars} of {total} chars. "
        f"Call again with narrower arguments / a more specific query "
        f"to retrieve the rest.]"
    )
    return text[:max_chars] + suffix


def _build_input_schema(tool_schema: Optional[Dict[str, Any]]) -> Type[BaseModel]:
    """
    Dynamically build a Pydantic model from an MCP inputSchema definition.

    MCP tools expose a JSON Schema under `inputSchema`. We convert the top-level
    properties to Pydantic fields so LangChain can validate and pass arguments
    correctly.

    Args:
        tool_schema: The MCP tool's inputSchema dict, or None.

    Returns:
        A Pydantic BaseModel class with fields matching the MCP schema.
    """
    if not tool_schema or not tool_schema.get("properties"):
        # No schema — accept a single free-form JSON string argument
        return create_model("MCPToolInput", query=(str, Field(default="", description="Tool arguments as JSON string")))

    properties: Dict[str, Any] = tool_schema.get("properties", {})
    required: List[str] = tool_schema.get("required", [])

    field_definitions: Dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type", "string")
        description = prop_schema.get("description", prop_name)

        # Map JSON Schema types to Python types
        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        python_type = type_map.get(prop_type, str)

        if prop_name in required:
            field_definitions[prop_name] = (python_type, Field(description=description))
        else:
            field_definitions[prop_name] = (Optional[python_type], Field(default=None, description=description))

    return create_model("MCPToolInput", **field_definitions)


class MCPToolWrapper(BaseTool):
    """
    LangChain BaseTool wrapper around a single MCP tool.

    Delegates execution to the MCPClientManager which already holds the live
    stdio connection to the MCP server process.

    The ``tool_timeout`` field limits how long a single tool call may block
    before being cancelled.  Set to ``None`` to inherit the MCPClientManager
    class-level ``TOOL_TIMEOUT`` default (60 s).
    """

    name: str
    description: str
    server_id: str
    tool_name: str
    mcp_manager: Any  # MCPClientManager — typed as Any to avoid circular imports
    args_schema: Optional[Type[BaseModel]] = None
    tool_timeout: Optional[float] = None  # seconds; None = use manager default

    class Config:
        arbitrary_types_allowed = True

    def _run(self, *args: Any, **kwargs: Any) -> str:
        """Sync run — not supported; use async."""
        raise NotImplementedError("MCPToolWrapper only supports async execution via _arun().")

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        """
        Execute the MCP tool asynchronously.

        kwargs are the structured arguments from the LangChain agent, already
        validated by the Pydantic args_schema.

        Returns:
            String representation of the MCP tool result.
        """
        logger.info(
            "MCPToolWrapper: executing tool",
            extra={"tool": self.tool_name, "server": self.server_id, "kwargs": list(kwargs.keys())},
        )

        result = await self.mcp_manager.execute_tool(
            server_id=self.server_id,
            tool_name=self.tool_name,
            arguments=kwargs,
            tool_timeout=self.tool_timeout,
        )

        if result.get("isError"):
            error_msg = result.get("error", "Unknown MCP tool error")
            logger.warning("MCPToolWrapper: tool returned error: %s", error_msg)
            return f"[Tool Error] {error_msg}"

        content = result.get("content", "")
        if isinstance(content, list):
            # MCP TextContent list — join all text parts
            parts = []
            for item in content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, dict):
                    parts.append(item.get("text", json.dumps(item)))
                else:
                    parts.append(str(item))
            return _truncate_output("\n".join(parts))

        return _truncate_output(str(content)) if content else "Tool executed successfully (no output)"


def build_langchain_tools(
    mcp_manager: Any,
    server_tool_map: Optional[Dict[str, List[str]]] = None,
    *,
    tool_timeout: Optional[float] = None,
) -> List[BaseTool]:
    """
    Build a list of LangChain tools from all connected MCP servers.

    Args:
        mcp_manager:     Live MCPClientManager with active connections.
        server_tool_map: Optional dict of {server_id: [tool_names]} to restrict
                         which tools are exposed to the agent. If None, all
                         discovered tools from all servers are included.
        tool_timeout:    Per-call timeout in seconds forwarded to every
                         ``MCPToolWrapper``.  When ``None`` each wrapper
                         inherits the manager default.

    Returns:
        List of LangChain BaseTool-compatible instances, one per MCP tool.
    """
    langchain_tools: List[BaseTool] = []

    # Resolve which servers and tools to expose
    if server_tool_map is None:
        # Use all connected servers and their discovered tools
        server_tool_map = mcp_manager.get_available_tools()

    for server_id, tool_names in server_tool_map.items():
        if not mcp_manager.is_connected(server_id):
            logger.warning("build_langchain_tools: server '%s' not connected, skipping", server_id)
            continue

        # Try to retrieve the raw tool metadata (name, description, inputSchema)
        # The MCPClientManager stores the session — we can inspect the tools list
        session = mcp_manager.connections.get(server_id, {}).get("session")
        tools_metadata: Dict[str, Any] = {}

        if session is not None:
            # tools_metadata is populated lazily during connect_server via list_tools()
            # We re-derive from the cached tool_names for now. Full schema requires
            # storing the MCP Tool objects, which we add below via the MCPToolRegistry.
            pass

        for tool_name in tool_names:
            # Retrieve full MCP Tool object if the manager stores it (enhanced below)
            mcp_tool_obj = _get_tool_metadata(mcp_manager, server_id, tool_name)
            description = (
                mcp_tool_obj.get("description", f"MCP tool '{tool_name}' on server '{server_id}'")
                if mcp_tool_obj
                else f"MCP tool '{tool_name}' on server '{server_id}'"
            )
            input_schema = mcp_tool_obj.get("inputSchema") if mcp_tool_obj else None
            args_schema = _build_input_schema(input_schema)

            tool = MCPToolWrapper(
                name=f"{server_id}__{tool_name}",
                description=description,
                server_id=server_id,
                tool_name=tool_name,
                mcp_manager=mcp_manager,
                args_schema=args_schema,
                tool_timeout=tool_timeout,
            )
            langchain_tools.append(tool)

            logger.debug(
                "build_langchain_tools: registered tool '%s' for server '%s'",
                tool_name,
                server_id,
            )

    logger.info(
        "build_langchain_tools: created %d LangChain tools from %d MCP server(s)",
        len(langchain_tools),
        len(server_tool_map),
    )
    return langchain_tools


def _get_tool_metadata(mcp_manager: Any, server_id: str, tool_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve cached MCP tool metadata from the manager's tool registry.

    The MCPClientManager stores raw tool names in `self.tools`. If it has been
    enhanced to store full Tool objects in `self.tool_objects`, we use those.
    Otherwise we fall back to name-only metadata.

    Args:
        mcp_manager: The MCPClientManager instance.
        server_id: Server identifier.
        tool_name: Name of the tool.

    Returns:
        Dict with at least 'description' and optionally 'inputSchema', or None.
    """
    # Check if manager stores full tool objects (added in enhanced connect_server)
    tool_objects = getattr(mcp_manager, "tool_objects", {})
    server_tools = tool_objects.get(server_id, {})
    if tool_name in server_tools:
        tool_obj = server_tools[tool_name]
        return {
            "description": getattr(tool_obj, "description", ""),
            "inputSchema": getattr(tool_obj, "inputSchema", None),
        }
    return None
