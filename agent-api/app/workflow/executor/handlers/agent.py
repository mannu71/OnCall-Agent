"""Agent node handler — runs ReactStrategy LangGraph ReAct loop."""
import logging
from typing import Any, Dict

from app.services.mcp_client_manager import MCPClientManager
from app.workflow.executor.streaming import _AgentStreamCallback

from . import register

logger = logging.getLogger(__name__)


def _sole_picked_skill(node: Dict[str, Any]) -> str:
    """Return the one skill name on the node's Skills picker, else ``''``.

    A node with exactly one picked skill and no instructions is unambiguous —
    the operator wired this agent to run that runbook. Two or more stays
    ambiguous (skill selection is query-driven: ``select_for_query`` has nothing
    to rank an empty query against), so the caller keeps skipping.
    """
    try:
        from app.harness.spec_factory import resolve_profile_fields
        from app.workflow.strategies.react.workflow_config import extract_agent_config

        cfg = extract_agent_config({'nodes': [{**node, 'type': 'agent'}]})
        skills = resolve_profile_fields(cfg).get('skills') or []
    except Exception as exc:  # noqa: BLE001 — a config read must never break a run
        logger.warning("Agent node: skills-picker read failed (%s)", exc)
        return ''
    return skills[0] if len(skills) == 1 else ''


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

    # No instruction anywhere (a scheduled/manual run carries no chat message, and
    # this node has no Instructions text) — but a single picked skill IS the
    # instruction. Synthesise the slash command so ReactStrategy's existing
    # expansion runs it, instead of skipping a run the operator plainly intended.
    if not user_query:
        _skill_name = _sole_picked_skill(node)
        if _skill_name:
            from app.core.skills import get_default_skill_manager
            try:
                _mgr = get_default_skill_manager()
                # Safe here (unlike the agent hot path): this runs at most once
                # per execution, and only when no query was supplied at all — so
                # a SKILL.md dropped on the volume works without an API restart.
                _mgr.maybe_rescan()
                _resolved = _mgr.resolve_command(_skill_name)
            except Exception as _skill_err:  # noqa: BLE001
                logger.warning("Agent node: skill resolution failed (%s)", _skill_err)
                _resolved = None
            if _resolved is None:
                # Naming the skill beats the generic message: the usual cause is
                # a SKILL.md dropped on the volume that the cached manager has
                # not re-scanned (GET /api/v1/skills triggers maybe_rescan).
                return {
                    'status': 'skipped',
                    'output': (
                        f"Agent node has no instructions, and its only picked skill "
                        f"'{_skill_name}' could not be resolved — check the skill "
                        f"exists and has been re-scanned."
                    ),
                    'agent_data': node_data,
                }
            user_query = f"/{_resolved.name}"
            logger.info(
                "Agent node: no instructions — running sole picked skill /%s",
                _resolved.name,
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
            # cache_read / cache_creation are a SUBSET of input_tokens, not
            # additional to it. Bedrock's raw counters ARE exclusive, but
            # langchain_aws folds them back in when it builds usage_metadata and
            # callbacks._parse_llm_output normalizes the raw branches to match,
            # so everything downstream of the callback is inclusive. (This
            # comment previously said the opposite; verified against live
            # Bedrock 2026-07-21 — see app.core.observability.cache_metrics.)
            # cache_read bills at ~10% of fresh input; cache_creation at a
            # premium for the write.
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
