"""Agent node handler — runs ReactStrategy LangGraph ReAct loop."""
import logging
from typing import Any, Dict

from app.services.mcp_client_manager import MCPClientManager
from app.workflow.executor.code_correlation import correlate_anomalies_to_code
from app.workflow.executor.streaming import _AgentStreamCallback

from . import register

logger = logging.getLogger(__name__)


@register("agent")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute agent node by delegating to ReactStrategy (LangGraph ReAct loop).

    The agent node's 'instructions' or the workflow's 'user_query' input drives
    the investigation. The shared MCP manager for this execution is passed so the
    agent can reuse already-connected database servers from preceding tool nodes.
    """
    from app.workflow.execution_port import ExecutionPort
    from app.workflow.strategies.react import ReactStrategy

    node_id = node.get('id')
    node_data = node.get('data', {})
    # New LangflowEditor nodes store config under `params`; legacy ReactFlow
    # nodes store under `data`. Check both so this handler works for either.
    node_params = node.get('params', {}) or {}
    execution_id = context.get('execution_id')

    active = executor.active_executions.get(execution_id, {})
    workflow = active.get('workflow') or {
        'name': context.get('workflow_name', 'unknown'),
        'nodes': [node],
        'edges': [],
    }

    user_query = (
        context.get('inputs', {}).get('user_query')
        or context.get('inputs', {}).get('query')
        or node_params.get('system')
        or node_params.get('instructions')
        or node_data.get('instructions')
        or node_data.get('description')
        or ''
    )

    if not user_query:
        return {
            'status': 'skipped',
            'output': 'Agent node has no user_query or instructions configured.',
            'agent_data': node_data,
        }

    if execution_id not in executor.mcp_managers:
        executor.mcp_managers[execution_id] = MCPClientManager()

    mcp_manager = executor.mcp_managers[execution_id]

    stream_callback = _AgentStreamCallback(executor, execution_id, node_id)

    strategy_context = {
        'execution_id': execution_id,
        'user_query': user_query,
        'mcp_manager': mcp_manager,
        'inputs': context.get('inputs', {}),
        'logger': logger,
        'stream_callback': stream_callback,
        'execution_port': ExecutionPort(
            executor.active_executions,
            publish_event=executor._publish_event,
        ),
    }

    # ------------------------------------------------------------------
    # Cross-node data: collect results from upstream CloudWatch nodes so
    # the agent can reason about pre-computed analysis.
    # ------------------------------------------------------------------
    cw_results = {}
    for key, value in context.items():
        # Collect pre-computed CloudWatch analyses from BOTH node types:
        # legacy cloudwatchAnalyzer and the new cloudwatch_tool — both now run
        # run_investigation_pipeline upfront and carry a real analysis_type +
        # output + data bundle. (cloudwatch_tool also sets tool_provider, but it
        # is no longer a no-op stub, so we no longer exclude it.)
        if isinstance(value, dict) and value.get('analysis_type') and value.get('output'):
            from app.workflow.tools.cloudwatch_summarizers import compact_context_snippet
            entry = {
                'analysis_type': value.get('analysis_type'),
                'output': value.get('output'),
                'log_groups_analyzed': value.get('log_groups_analyzed'),
                'time_range': value.get('time_range'),
                'alerts': value.get('alerts'),
            }
            raw_data = value.get('data')
            if isinstance(raw_data, dict):
                entry['triage'] = compact_context_snippet(
                    raw_data, value.get('analysis_type', ''),
                )
            cw_results[key] = entry
    if cw_results:
        strategy_context['cloudwatch_context'] = cw_results
        logger.info(
            "Agent node: injecting %d upstream CloudWatch result(s) into context",
            len(cw_results),
        )

    # ------------------------------------------------------------------
    # Cross-node data: collect results from upstream Code Analyzer nodes
    # so the agent can reason about pre-indexed code repositories.
    # ------------------------------------------------------------------
    code_results = {}
    for key, value in context.items():
        if isinstance(value, dict) and value.get('code_analysis_type'):
            code_results[key] = {
                'code_analysis_type': value.get('code_analysis_type'),
                'repos_indexed':      value.get('repos_indexed'),
                'repos_config':       value.get('repos_config'),
                'pre_summary':        value.get('pre_summary'),
                'output':             value.get('output'),
            }
    if code_results:
        strategy_context['code_analyzer_context'] = code_results
        logger.info(
            "Agent node: injecting %d upstream Code Analyzer result(s) into context",
            len(code_results),
        )

    # ------------------------------------------------------------------
    # Anomaly-code correlation: when both CW and Code Analyzer results are
    # present, cross-reference anomaly messages with code chunk names and
    # inject the correlation into strategy_context for the agent's initial
    # context block.
    # ------------------------------------------------------------------
    if cw_results and code_results:
        try:
            log_group_to_repo = {}
            for _, cv in code_results.items():
                for repo_cfg in (cv.get('repos_config') or []):
                    for lg in (repo_cfg.get('logGroups') or []):
                        log_group_to_repo[lg] = repo_cfg.get('name', '')
            if log_group_to_repo:
                strategy_context['anomaly_code_correlation'] = \
                    await correlate_anomalies_to_code(
                        cw_results=cw_results,
                        code_results=code_results,
                        log_group_to_repo=log_group_to_repo,
                    )
        except Exception as _corr_exc:
            logger.debug("Anomaly-code correlation skipped: %s", _corr_exc)

    try:
        react_strategy = ReactStrategy()
        result = await react_strategy.execute(workflow, strategy_context)

        return {
            'status': 'success',
            'output': result.get('final_answer', 'Agent completed (no answer returned).'),
            'final_answer': result.get('final_answer'),
            'messages': result.get('messages', []),
            'message_count': result.get('message_count', 0),
            'tool_calls': result.get('tool_calls', []),
            'model': result.get('model'),
            'provider': result.get('provider'),
            'agent_data': node_data,
            'input_tokens':  result.get('input_tokens',  0) or 0,
            'output_tokens': result.get('output_tokens', 0) or 0,
            'total_tokens':  result.get('total_tokens',  0) or 0,
        }

    except Exception as e:
        logger.error(
            "Agent node execution failed: %s (node_id=%s, execution_id=%s)",
            e, node_id, execution_id,
        )
        return {
            'status': 'failed',
            'error': str(e),
            'agent_data': node_data,
        }
