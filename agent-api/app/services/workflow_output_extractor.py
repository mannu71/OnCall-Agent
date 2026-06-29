"""Shared helper for extracting the main workflow output from an execution record.

Canonical version (from app.api.v1.endpoints.executions) — harmonized so all
endpoints returning execution detail produce identical `output` and token fields.
"""
from typing import Any, Dict


def extract_workflow_output(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the main workflow output from execution results.

    Mutates the given execution dict in place (adds `output` and token fields)
    and returns it for convenience. Safe to call with an empty dict.
    """
    results = execution.get('results') or {}

    orchestrator_output = next(
        (v for v in results.values() if isinstance(v, dict) and 'queries_executed' in v),
        None
    )

    if orchestrator_output:
        output = {
            'queries_executed': orchestrator_output.get('queries_executed', 0),
            'failures': orchestrator_output.get('failures', 0),
            'results': orchestrator_output.get('results', [])
        }
        if orchestrator_output.get('error'):
            output['error'] = orchestrator_output['error']
        execution['output'] = output

    if not orchestrator_output:
        cloudwatch_output = next(
            (v for v in results.values() if isinstance(v, dict) and 'analysis_type' in v),
            None
        )
        if cloudwatch_output:
            output = {
                'analysis_type': cloudwatch_output.get('analysis_type'),
                'log_groups_analyzed': cloudwatch_output.get('log_groups_analyzed', []),
                'time_range': cloudwatch_output.get('time_range'),
                'results': cloudwatch_output.get('data', {}),
                'output': cloudwatch_output.get('output'),
                'model': cloudwatch_output.get('model'),
            }
            if cloudwatch_output.get('alerts'):
                output['alerts'] = cloudwatch_output['alerts']
            execution['output'] = output

    if not orchestrator_output and not execution.get('output'):
        react_output = next(
            (v for v in results.values() if isinstance(v, dict) and v.get('type') == 'react'),
            None
        )
        if react_output:
            execution['output'] = {
                'type':          'react',
                'final_answer':  react_output.get('final_answer'),
                'user_query':    react_output.get('user_query'),
                'message_count': react_output.get('message_count', 0),
                'tool_calls':    react_output.get('tool_calls', []),
                'model':         react_output.get('model'),
                'provider':      react_output.get('provider'),
                # Token usage — passed through from ReactStrategy._execute_agent
                'input_tokens':  react_output.get('input_tokens',  0) or 0,
                'output_tokens': react_output.get('output_tokens', 0) or 0,
                'total_tokens':  react_output.get('total_tokens',  0) or 0,
            }

    # Ensure top-level token fields are always present in the response
    execution.setdefault('input_tokens',  execution.get('input_tokens',  0) or 0)
    execution.setdefault('output_tokens', execution.get('output_tokens', 0) or 0)
    execution.setdefault('total_tokens',  execution.get('total_tokens',  0) or 0)

    return execution
