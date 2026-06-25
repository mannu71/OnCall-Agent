"""MCP node handler — connects any MCP server(s) from Settings.

The new generic `mcp_server` node lets users pick one or more MCP servers
(ado, postgres, code, api, …) and wire each output port independently to the
agent.  Unlike the `database` node this handler has no DB-schema or SQL-
orchestrator plumbing — it simply connects every selected server to the shared
MCPClientManager so the agent can call their tools.

params['servers'] — comma-separated server names chosen in the UI.
params['tools']   — optional comma-separated fnmatch patterns (e.g. ``wit_*``)
                    restricting which of a server's tools reach the agent. When
                    omitted, every tool the server advertises is exposed. Some
                    servers (Azure DevOps) advertise ~90 tools; exposing them all
                    bloats every request and trips Bedrock guardrail throttling,
                    so a node can narrow the set to just what the workflow needs.
"""
import logging
from typing import Any, Dict, List

from app.services.mcp_client_manager import MCPClientManager

from . import register

logger = logging.getLogger(__name__)


def _parse_patterns(raw: Any) -> List[str]:
    """Normalize a tool-filter value (string or list) into a pattern list."""
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    return [p.strip() for p in str(raw or "").split(",") if p.strip()]


@register("mcp_server")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP node.

    Reads node['params']['servers'] (comma-separated names), looks each up in
    the DB-backed MCP settings, and connects them to the execution's
    MCPClientManager so the agent receives their tools at runtime.
    """
    node_id = node.get("id")
    params = node.get("params", {})
    execution_id = context.get("execution_id")

    servers_raw = params.get("servers", "")
    server_names: List[str] = [s.strip() for s in servers_raw.split(",") if s.strip()]

    # Optional tool allowlist (fnmatch patterns) shared across the node's servers.
    tool_patterns = _parse_patterns(params.get("tools"))

    if not server_names:
        return {"status": "skipped", "output": "No MCP servers selected on this node"}

    from app.infrastructure.persistence import mcp_config_repository

    server_list = await mcp_config_repository.list_all(include_disabled=False)
    all_configs: Dict[str, Any] = {s["name"]: s for s in server_list}

    if execution_id not in executor.mcp_managers:
        executor.mcp_managers[execution_id] = MCPClientManager()
    mcp_manager = executor.mcp_managers[execution_id]

    connected: List[str] = []
    failed: List[str] = []
    failure_reasons: Dict[str, str] = {}

    for server_name in server_names:
        mcp_config = all_configs.get(server_name)
        if not mcp_config:
            logger.warning("MCP node: server '%s' not found in settings", server_name)
            failed.append(server_name)
            failure_reasons[server_name] = "not found in MCP settings (or disabled)"
            continue

        conn_key = f"{node_id}__{server_name}"
        success = await mcp_manager.connect_server(server_id=conn_key, config=mcp_config)

        if success:
            logger.info("MCP node: connected '%s' as '%s'", server_name, conn_key)
            if tool_patterns:
                mcp_manager.set_tool_filter(conn_key, tool_patterns)
                kept = mcp_manager.filtered_tool_names(conn_key)
                logger.info(
                    "MCP node: tool filter %s on '%s' → %d of %d tools exposed",
                    tool_patterns, server_name,
                    len(kept), len(mcp_manager.tools.get(conn_key, [])),
                )
            context.setdefault("connected_tool_server", conn_key)
            connected.append(server_name)
        else:
            reason = mcp_manager.last_errors.get(conn_key, "connection failed")
            logger.error("MCP node: failed to connect '%s': %s", server_name, reason)
            failed.append(server_name)
            failure_reasons[server_name] = reason

    if not connected:
        detail = "; ".join(
            f"{n} ({failure_reasons.get(n, 'connection failed')})" for n in failed
        )
        return {"status": "failed", "error": f"Could not connect to any MCP server: {detail}"}

    status = "success" if not failed else "partial"
    return {
        "status": status,
        "output": f"Connected: {', '.join(connected)}"
                  + (f"; failed: {', '.join(failed)}" if failed else ""),
        "server_id": context.get("connected_tool_server"),
    }
