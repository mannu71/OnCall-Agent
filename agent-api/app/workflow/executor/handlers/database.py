"""Database node handler — connects to an MCP server by name from Settings.

New LangflowEditor schema stores the chosen server name in node['params']['server'].
This handler looks up the full MCP config from the DB-backed repository and connects
exactly like the legacy 'tool' handler does, so the orchestrator node receives
context['connected_tool_server'] as expected.

Auto-detect fallback: when no server is explicitly configured, the handler scans
the workflow's orchestrator node SQL content for ``-- db:<server>`` directives
and connects to exactly those servers, so the workflow runs without manual
database-node configuration.
"""
import logging
import re
from typing import Any, Dict, List

from app.services.mcp_client_manager import MCPClientManager

from . import register

logger = logging.getLogger(__name__)

_DB_DIRECTIVE_RE = re.compile(r'--\s*db\s*:\s*(\S+)', re.IGNORECASE)


def _extract_db_servers_from_sql(sql: str) -> List[str]:
    """Return unique server names referenced by ``-- db:<name>`` directives."""
    seen: dict = {}
    for m in _DB_DIRECTIVE_RE.finditer(sql):
        name = m.group(1).strip()
        if name and name not in seen:
            seen[name] = True
    return list(seen)


def _derive_server_names_from_workflow(executor, execution_id: str) -> List[str]:
    """Scan all orchestrator nodes in the running workflow for -- db: directives."""
    exec_state = executor.active_executions.get(execution_id, {})
    workflow = exec_state.get('workflow', {})
    nodes = workflow.get('nodes', [])

    server_names: List[str] = []
    for n in nodes:
        if n.get('type') != 'orchestrator':
            continue
        params = n.get('params', {})
        data = n.get('data', {})
        sql = params.get('sqlContent') or data.get('fileContent') or ''
        for srv in _extract_db_servers_from_sql(sql):
            if srv not in server_names:
                server_names.append(srv)

    return server_names


@register("database")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Database node (new LangflowEditor schema).

    node['params']['server'] holds the MCP server name (e.g. 'postgres-production').
    We fetch the full config from the DB-backed MCPConfigRepository and connect via
    MCPClientManager, then mirror the 'tool' handler's context side-effects so the
    downstream orchestrator node works unchanged.

    When params['server'] is empty (node not configured), the handler falls back to
    auto-detecting server names from ``-- db:<name>`` directives in any orchestrator
    nodes present in the same workflow.
    """
    node_id = node.get('id')
    params = node.get('params', {})
    execution_id = context.get('execution_id')

    # Support both single server name (string) and multi-select (comma-separated).
    server_names_raw = params.get('server', '')
    server_names = [s.strip() for s in server_names_raw.split(',') if s.strip()] if server_names_raw else []

    if not server_names:
        # Auto-detect from SQL directives in the workflow's orchestrator nodes.
        server_names = _derive_server_names_from_workflow(executor, execution_id)
        if server_names:
            logger.info(
                "Database node: no server configured — auto-detected from SQL directives: %s",
                server_names,
            )
        else:
            return {
                "status": "skipped",
                "output": "No database server configured on this node and none found in SQL directives",
            }

    # Use the DB-backed repository (app.infrastructure.persistence) not the
    # file-backed one (app.repositories) so we read the servers saved in Postgres.
    from app.infrastructure.persistence.mcp_config_repository import (
        MCPConfigRepository as DBMCPConfigRepository,
    )

    mcp_repo = DBMCPConfigRepository()
    server_list = await mcp_repo.list_all(include_disabled=False)
    # Build name → config mapping
    all_server_configs: Dict[str, Any] = {s['name']: s for s in server_list}

    if execution_id not in executor.mcp_managers:
        executor.mcp_managers[execution_id] = MCPClientManager()
    mcp_manager = executor.mcp_managers[execution_id]

    connected = []
    failed = []

    for server_name in server_names:
        mcp_config = all_server_configs.get(server_name)
        if not mcp_config:
            logger.warning("Database node: MCP server '%s' not found in settings", server_name)
            failed.append(server_name)
            continue

        # The connection key is node_id + server_name to stay unique per node per server.
        conn_key = f"{node_id}__{server_name}"
        success = await mcp_manager.connect_server(server_id=conn_key, config=mcp_config)

        if success:
            logger.info("Database node: connected '%s' as '%s'", server_name, conn_key)
            # Last connected server becomes the default for the orchestrator.
            context['connected_tool_server'] = conn_key

            # Build label → connection key map so SQLOrchestrator can route
            # '-- db:postgres-production' directives to the right connection.
            if 'db_server_map' not in context:
                context['db_server_map'] = {}
            context['db_server_map'][server_name] = conn_key
            connected.append(server_name)
        else:
            logger.error("Database node: failed to connect to '%s'", server_name)
            failed.append(server_name)

    if not connected:
        return {
            "status": "failed",
            "error": f"Could not connect to any database server: {', '.join(failed)}",
        }

    status = "success" if not failed else "partial"
    return {
        "status": status,
        "output": f"Connected to: {', '.join(connected)}"
                  + (f"; failed: {', '.join(failed)}" if failed else ""),
        "server_id": context.get('connected_tool_server'),
        "tools": mcp_manager.get_available_tools(context.get('connected_tool_server', '')),
    }
