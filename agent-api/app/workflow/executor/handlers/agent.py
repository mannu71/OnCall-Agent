"""Agent node handler — runs ReactStrategy LangGraph ReAct loop."""
import logging
from typing import Any, Dict

from app.services.mcp_client_manager import MCPClientManager
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
    # Capture the parent run's callback so delegated subagents can surface their
    # tool activity on this same chat stream, tagged with the subagent name.
    try:
        from app.harness.subagent_factory import set_parent_stream_callback
        set_parent_stream_callback(stream_callback)
    except Exception:  # noqa: BLE001 — attribution must never break a run
        pass

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
                'code_analysis_type':   value.get('code_analysis_type'),
                'repos_indexed':        value.get('repos_indexed'),
                'repos_config':         value.get('repos_config'),
                'output':               value.get('output'),
                'codegraph_repo_paths': value.get('codegraph_repo_paths'),
            }
    if code_results:
        strategy_context['code_analyzer_context'] = code_results
        logger.info(
            "Agent node: injecting %d upstream Code Analyzer result(s) into context",
            len(code_results),
        )

    # ------------------------------------------------------------------
    # Cross-node data: collect results from upstream Vector Memory nodes
    # (semantic recall) so the agent starts with relevant remembered facts.
    # ------------------------------------------------------------------
    mem_results = {}
    for key, value in context.items():
        if isinstance(value, dict) and value.get('memory_recall_type') and value.get('output'):
            mem_results[key] = {
                'memory_recall_type': value.get('memory_recall_type'),
                'output': value.get('output'),
                'count': value.get('count'),
            }
    if mem_results:
        strategy_context['vector_memory_context'] = mem_results
        logger.info(
            "Agent node: injecting %d upstream Vector Memory result(s) into context",
            len(mem_results),
        )

    try:
        react_strategy = ReactStrategy()
        result = await react_strategy.execute(workflow, strategy_context)

        return {
            'status': 'success',
            'output': result.get('final_answer', 'Agent completed (no answer returned).'),
            'final_answer': result.get('final_answer'),
            'structured_output': result.get('structured_output'),
            'output_mode': result.get('output_mode', 'text'),
            'messages': result.get('messages', []),
            'message_count': result.get('message_count', 0),
            'tool_calls': result.get('tool_calls', []),
            'model': result.get('model'),
            'provider': result.get('provider'),
            'agent_data': node_data,
            'input_tokens':  result.get('input_tokens',  0) or 0,
            'output_tokens': result.get('output_tokens', 0) or 0,
            'total_tokens':  result.get('total_tokens',  0) or 0,
            # cache_read / cache_creation are ADDITIONAL to input_tokens, not a
            # subset of it (Bedrock/Anthropic report them as separate counters).
            # cache_read bills at ~10% of fresh input; cache_creation at a
            # premium for the write. Carried through so the UI can show real
            # cache savings instead of misreading input_tokens as inclusive.
            'cache_read_tokens': result.get('cache_read_tokens', 0) or 0,
            'cache_creation_tokens': result.get('cache_creation_tokens', 0) or 0,
            # Context-window fullness for this turn (Chat UI context bar).
            'context_window_size': result.get('context_window_size', 0) or 0,
            'context_used_tokens': result.get('context_used_tokens', 0) or 0,
            'context_used_pct': result.get('context_used_pct', 0) or 0,
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
