"""Sanitize node execution results before persistence and API responses."""
from typing import Any, Dict

_TOOL_NODE_KEYS = {'server_id', 'tool_data'}


def build_node_result(value: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant fields from a node execution result."""
    node_result = {}
    for field in ('status', 'output', 'trigger_time', 'model',
                  'input_tokens', 'output_tokens', 'total_tokens',
                  'final_answer', 'tool_calls', 'provider', 'message_count', 'user_query',
                  'messages'):
        if field in value:
            node_result[field] = value[field]

    if 'queries_executed' in value:
        node_result['queries_executed'] = value.get('queries_executed')
        node_result['failures'] = value.get('failures', 0)
        node_result['results'] = value.get('results', [])
        err = value.get('error')
        if err:
            node_result['error'] = err

    if 'analysis_type' in value:
        node_result['analysis_type'] = value.get('analysis_type')
        node_result['log_groups_analyzed'] = (
            value.get('log_groups_analyzed') or value.get('log_groups', [])
        )
        node_result['time_range'] = value.get('time_range')
        node_result['data'] = value.get('data', {})
        alerts = value.get('alerts')
        if alerts:
            node_result['alerts'] = alerts

    return node_result


def sanitize_results(results: Dict[str, Any]) -> Dict[str, Any]:
    """Remove internal implementation details and sensitive data from results."""
    excluded_keys = {'execution_id', 'inputs', 'connected_tool_server', 'db_server_map'}
    sanitized = {}

    for key, value in results.items():
        if key in excluded_keys:
            continue
        if not isinstance(value, dict):
            sanitized[key] = value
            continue

        if value.keys() & _TOOL_NODE_KEYS:
            continue

        node_result = build_node_result(value)
        if node_result:
            sanitized[key] = node_result

    return sanitized
