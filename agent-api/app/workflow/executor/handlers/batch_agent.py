"""Batch agent node handler — runs BatchReactStrategy (map-reduce ReAct)."""
import logging
from typing import Any, Dict

from app.services.mcp_client_manager import MCPClientManager
from app.workflow.executor.streaming import _AgentStreamCallback

from . import register

logger = logging.getLogger(__name__)


@register("batchAgent")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
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

    active   = executor.active_executions.get(execution_id, {})
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

    if execution_id not in executor.mcp_managers:
        executor.mcp_managers[execution_id] = MCPClientManager()

    stream_callback = _AgentStreamCallback(executor, execution_id, node_id)

    strategy_context = {
        'execution_id':     execution_id,
        'user_query':       user_query,
        'mcp_manager':      executor.mcp_managers[execution_id],
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
