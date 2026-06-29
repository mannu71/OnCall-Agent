"""Orchestrator node handler — canonical SQL orchestration path.

Delegates to ``app.services.sql_pipeline.run_pipeline``.  The legacy
``OrchestratorStrategy`` class was removed; do not reintroduce a parallel
strategy for SQL workflows.
"""
import logging
from typing import Any, Dict, List

from app.workflow.executor.sql_loader import load_sql_content

from . import register

logger = logging.getLogger(__name__)


@register("orchestrator")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the orchestrator node.

    Supports both schema shapes:
    - Legacy (ReactFlow):   ``node['data']['fileName']`` / ``fileContent``
    - New (LangflowEditor): ``node['params']['sqlFile']`` / ``sqlContent``
    """
    node_data = {**node.get('data', {}), **node.get('params', {})}
    execution_id = context.get('execution_id')
    workflow_name = context.get('workflow_name')

    try:
        sql_content = load_sql_content(node_data, workflow_name)
        if not sql_content:
            sql_file = node_data.get('sqlFile') or node_data.get('fileName')
            error_msg = f"SQL file not found: {sql_file}" if sql_file else "No SQL content provided"
            return {"status": "failed", "error": error_msg}

        tool_server_id = context.get('connected_tool_server')
        if not tool_server_id:
            return {"status": "failed", "error": "No database tool connected to orchestrator"}

        mcp_manager = executor.mcp_managers.get(execution_id)
        if not mcp_manager:
            return {"status": "failed", "error": "MCP manager not initialized"}

        workflow_inputs = context.get('inputs') or {}
        db_server_map = context.get('db_server_map') or {}

        from app.services.sql_pipeline import run_pipeline

        report = await run_pipeline(
            sql_content,
            server_id=tool_server_id,
            mcp_manager=mcp_manager,
            db_server_map=db_server_map,
            workflow_inputs=workflow_inputs,
            timeout=900,
            parameterized=False,  # deployed MCP server doesn't bind params
        )

        legacy_results: List[Dict[str, Any]] = []
        for r in report.results:
            entry: Dict[str, Any] = {
                "query_id": r.statement_id,
                "label": r.label,
                "success": r.success,
                "database": r.server_id,
            }
            if r.success:
                entry["result"] = r.rows
            else:
                entry["error"] = r.error
            legacy_results.append(entry)

        return {
            "status": "success" if report.success else "failed",
            "output": f"Executed {report.statements_executed} queries",
            "queries_executed": report.statements_executed,
            "failures": report.failures,
            "results": legacy_results,
            "error": report.error,
        }

    except Exception as e:  # noqa: BLE001
        logger.error("Orchestrator execution failed: %s", e)
        return {
            "status": "failed",
            "error": str(e),
        }
