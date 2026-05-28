"""Semantic Router node handler for VisualWorkflowExecutor."""
import logging
from typing import Any, Dict

from app.workflow.router_classify import classify_route

from . import register

logger = logging.getLogger(__name__)


@register("router")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the Semantic Router classifier in the visual graph pipeline.

    This handler performs only the **classification** step (one small LLM call)
    and returns the routing decision so the BFS graph knows which downstream
    agent to execute next.  It intentionally does NOT delegate to
    RouterStrategy.execute() (which would launch ReactStrategy inline) — in the
    visual executor the downstream agent node is executed separately by BFS.

    The critical contract this handler must satisfy:
      result["routing"]["target_agent_id"]  — the node ID of the selected agent
      result["routing"]["selected_category"] — the human-readable category key
      result["routing"]["all_routes"]       — all configured route keys (for UI)
    """
    execution_id = context.get("execution_id")
    full_workflow = executor.active_executions.get(execution_id, {}).get("workflow", {
        "name": context.get("workflow_name", "Visual Workflow"),
        "nodes": [node],
        "edges": []
    })

    try:
        user_query = (
            context.get("user_query")
            or context.get("inputs", {}).get("user_query", "")
        )

        node_params = node.get("params") or {}
        node_data   = node.get("data")   or {}
        routes      = node_params.get("routes")      or node_data.get("routes")      or {}

        if not routes:
            return {
                "status": "failed",
                "error": "Router node has no routes configured."
            }

        if not user_query:
            first_key, first_val = next(iter(routes.items()))
            logger.warning(
                "Router node: no user_query in context — defaulting to first route '%s' -> '%s' (exec=%s)",
                first_key, first_val, execution_id,
            )
            return {
                "status": "success",
                "output": (
                    f"No user query provided. Defaulted to first route: "
                    f"category '{first_key}' → agent '{first_val}'"
                ),
                "routing": {
                    "selected_category": first_key,
                    "target_agent_id":   first_val,
                    "all_routes":        list(routes.keys()),
                },
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }

        result = await classify_route(
            full_workflow,
            node,
            user_query,
            execution_id=execution_id,
        )

        logger.info(
            "Router: routed query '%s...' to category='%s' -> agent='%s' (exec=%s, in=%d out=%d)",
            user_query[:60],
            result.selected_key,
            result.target_agent_id,
            execution_id,
            result.input_tokens,
            result.output_tokens,
        )

        trunc_note = " (query truncated for classify)" if result.query_truncated else ""
        return {
            "status": "success",
            "output": (
                f"Semantic Routing Decision:\n"
                f"  Query:    \"{user_query[:120]}{'...' if len(user_query) > 120 else ''}\"{trunc_note}\n"
                f"  Category: {result.selected_key}\n"
                f"  Agent:    {result.target_agent_id}\n"
                f"  Model:    {result.model} (classify tier)"
            ),
            "routing": {
                "selected_category": result.selected_key,
                "target_agent_id":   result.target_agent_id,
                "all_routes":        list(result.routes.keys()),
                "classify_model":    result.model,
                "query_truncated":   result.query_truncated,
                "lm_connected":      result.lm_connected,
            },
            "input_tokens":  result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens":  result.input_tokens + result.output_tokens,
            "role": "classify",
        }

    except Exception as e:
        logger.error("Router handler failed: %s (exec=%s)", e, execution_id, exc_info=True)
        return {
            "status": "failed",
            "error": f"Router failed: {str(e)}"
        }
