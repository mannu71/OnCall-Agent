"""
MCP → LangChain Tool Adapter

Converts live MCP server connections (managed by MCPClientManager) into
LangChain-compatible BaseTool instances so that the LangGraph ReAct agent
can call them dynamically during reasoning.

Each MCP tool is wrapped as a StructuredTool whose _arun() method dispatches
back through the MCPClientManager.execute_tool() coroutine.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Type

from app.config import settings
from app.core.compaction.compressor import compress_text

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

_BEDROCK_TOOL_NAME_LIMIT = 64


def _safe_tool_name(server_id: str, tool_name: str) -> str:
    """Return a Bedrock-safe composite tool name (≤64 chars).

    Bedrock's ConverseStream rejects tool names longer than 64 characters.
    When the composite ``{server_id}__{tool_name}`` exceeds the limit we
    truncate to 57 chars and append ``_`` + the first 6 hex digits of the
    SHA-256 of the full name so the shortened name stays unique.
    """
    full = f"{server_id}__{tool_name}"
    if len(full) <= _BEDROCK_TOOL_NAME_LIMIT:
        return full
    suffix = hashlib.sha256(full.encode()).hexdigest()[:6]
    return full[: _BEDROCK_TOOL_NAME_LIMIT - 7] + "_" + suffix


# Ceiling for a single MCP tool's text output. Unbounded outputs are a token
# sink: the full result is replayed in the message history on every subsequent
# ReAct iteration. Above this many chars we truncate and tell the model how to
# get the rest (narrower args / pagination). Override via env.
MCP_TOOL_OUTPUT_MAX_CHARS = settings.mcp_tool_output_max_chars


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


async def _compress_or_truncate(text: str) -> str:
    """Compress large tool output when enabled; fall back to lossy truncation.

    Compression is type-aware and reversible (no data loss); truncation is the
    existing lossy char-cap kept as the safety net when compression is disabled
    or the sidecar is unreachable.
    """
    chars = len(text)
    if settings.compression_enabled and chars > settings.compression_min_chars:
        logger.info(
            "Compression: attempting %d-char tool output (min_chars=%d)",
            chars,
            settings.compression_min_chars,
        )
        result = await compress_text(
            text,
            endpoint=settings.compression_endpoint,
            timeout_ms=settings.compression_timeout_ms,
        )
        if result.compressed:
            logger.info(
                "Compression: %d → %d chars (%.0f%% saved, %d → %d tokens)",
                chars,
                len(result.text),
                (1 - len(result.text) / max(chars, 1)) * 100,
                result.tokens_before,
                result.tokens_after,
            )
            return result.text
        logger.info(
            "Compression: sidecar returned unchanged text for %d-char output; falling back to truncate",
            chars,
        )
    return _truncate_output(text)


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
    _free_form = create_model(
        "MCPToolInput",
        query=(str, Field(default="", description="Tool arguments as JSON string")),
    )

    if not tool_schema or not tool_schema.get("properties"):
        return _free_form

    properties = tool_schema.get("properties", {})
    if not isinstance(properties, dict):
        logger.warning(
            "MCP tool schema 'properties' is not a dict (got %s) — "
            "falling back to free-form input model",
            type(properties).__name__,
        )
        return _free_form

    required_raw = tool_schema.get("required", [])
    required: List[str] = required_raw if isinstance(required_raw, list) else []

    field_definitions: Dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            logger.warning(
                "MCP schema property '%s' has a non-dict schema (got %s) — skipping",
                prop_name, type(prop_schema).__name__,
            )
            continue
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
    # True when this tool came from a node with an explicit tool filter set — an
    # operator's deliberate allowlist. The relevance router must NOT prune these
    # (the user already chose them); see app.harness.tool_router.filter_tools.
    router_pinned: bool = False
    # Optional clean→canonical rewrite applied to the `project` arg before the
    # MCP call (e.g. codegraph's "compliance-api" → "app-data-indexed_repos-
    # compliance-api" — see app.workflow.tools.codegraph_tools.cg_project_name).
    # Keeps internal naming schemes out of the agent's prompt/tool-call surface.
    # Idempotent: a value already in canonical form (not a key in the map) is
    # left unchanged.
    project_aliases: Optional[Dict[str, str]] = None

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

        # Strip None values before sending — MCP servers with strict JSON-schema
        # validation reject explicit null for optional params that they'd accept
        # as absent. The ADO MCP server is a known case (project, fields, etc.).
        clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}

        if self.project_aliases and "project" in clean_kwargs:
            _p = clean_kwargs["project"]
            clean_kwargs["project"] = self.project_aliases.get(_p, _p)

        result = await self.mcp_manager.execute_tool(
            server_id=self.server_id,
            tool_name=self.tool_name,
            arguments=clean_kwargs,
            tool_timeout=self.tool_timeout,
        )

        if result.get("isError"):
            raw = result.get("content") or result.get("error") or "Unknown MCP tool error"
            if isinstance(raw, list):
                parts = []
                for item in raw:
                    if hasattr(item, "text"):
                        parts.append(item.text)
                    elif isinstance(item, dict):
                        parts.append(item.get("text", json.dumps(item)))
                    else:
                        parts.append(str(item))
                error_msg = "\n".join(parts) or "Unknown MCP tool error"
            else:
                error_msg = str(raw)
            logger.warning("MCPToolWrapper: tool error (terminal=%s): %s", result.get("terminal"), error_msg)
            if result.get("terminal"):
                # Infrastructure failure after retries exhausted (timeout, disconnect).
                # Raise so the agent run terminates immediately rather than the agent
                # re-calling the same broken tool.
                raise RuntimeError(f"Tool '{self.tool_name}' failed: {error_msg}")
            # MCP application error (wrong args, not found, permission denied) —
            # return as string so the agent can self-correct with different arguments.
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
            return await _compress_or_truncate("\n".join(parts))

        return await _compress_or_truncate(str(content)) if content else "Tool executed successfully (no output)"


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
        # Use all connected servers, but honor any per-server tool filter set on
        # the manager (e.g. an MCP node that exposes only wit_* from an ADO server
        # advertising 90 tools). Filtering here keeps the agent's tool list — and
        # every request's token count — small, which avoids guardrail throttling.
        get_filtered = getattr(mcp_manager, "filtered_tool_names", None)
        if callable(get_filtered):
            server_tool_map = {
                sid: get_filtered(sid) for sid in mcp_manager.get_available_tools()
            }
        else:
            server_tool_map = mcp_manager.get_available_tools()

    # Servers carrying an explicit per-node tool filter: their tools are an
    # operator allowlist and must survive the relevance router downstream.
    _filtered_servers = set(getattr(mcp_manager, "tool_filters", {}) or {})

    for server_id, tool_names in server_tool_map.items():
        if not mcp_manager.is_connected(server_id):
            logger.warning("build_langchain_tools: server '%s' not connected, skipping", server_id)
            continue

        _server_pinned = server_id in _filtered_servers

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

            composite_name = _safe_tool_name(server_id, tool_name)
            if composite_name != f"{server_id}__{tool_name}":
                logger.warning(
                    "build_langchain_tools: tool name truncated to fit Bedrock 64-char limit "
                    "'%s' → '%s'",
                    f"{server_id}__{tool_name}", composite_name,
                )
            tool = MCPToolWrapper(
                name=composite_name,
                description=description,
                server_id=server_id,
                tool_name=tool_name,
                mcp_manager=mcp_manager,
                args_schema=args_schema,
                tool_timeout=tool_timeout,
                router_pinned=_server_pinned,
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
    logger.debug(
        "MCP: no cached metadata for tool '%s' on server '%s' — "
        "falling back to generic description",
        tool_name, server_id,
    )
    return None
