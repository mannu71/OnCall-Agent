from __future__ import annotations

import logging
from typing import Any, Dict

from app.workflow.router_classify import ROUTER_SYSTEM_PROMPT, classify_route
from app.workflow.strategies.base import BaseStrategy
from app.workflow.strategies.react import ReactStrategy

logger = logging.getLogger(__name__)

# Backwards compatibility for imports of _ROUTER_SYSTEM_PROMPT
_ROUTER_SYSTEM_PROMPT = ROUTER_SYSTEM_PROMPT


class RouterStrategy(BaseStrategy):
    """Semantic Router Strategy.

    Workflow node type: ``router``

    Given a user query and a list of target agents/routes, it uses a lightweight LLM
    to classify the query and delegates execution to the appropriate ReactStrategy agent.
    """

    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return False
        return any(n.get("type") == "router" for n in nodes)

    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        execution_id = context.get("execution_id")
        logger_instance = context.get("logger", logger)
        user_query = (
            context.get("user_query")
            or context.get("inputs", {}).get("user_query", "")
        )

        if not user_query:
            raise ValueError(
                "RouterStrategy requires a user_query in the execution context."
            )

        logger_instance.info(
            "RouterStrategy: Starting query routing",
            extra={
                "execution_id": execution_id,
                "user_query_preview": user_query[:100]
            }
        )

        nodes = workflow.get("nodes", [])
        router_node = next((n for n in nodes if n.get("type") == "router"), None)
        if not router_node:
            raise ValueError("RouterStrategy could not find any 'router' node in the workflow graph.")

        node_params = router_node.get("params") or {}
        node_data = router_node.get("data") or {}
        routes = node_params.get("routes") or node_data.get("routes") or {}

        if not routes or not isinstance(routes, dict):
            raise ValueError("Router node must define a dictionary of routing paths under 'routes'.")

        classify = await classify_route(
            workflow,
            router_node,
            user_query,
            execution_id=execution_id,
        )

        selected_key = classify.selected_key
        target_agent_id = classify.target_agent_id

        logger_instance.info(
            "RouterStrategy: LLM returned category '%s' (in=%d out=%d model=%s)",
            selected_key,
            classify.input_tokens,
            classify.output_tokens,
            classify.model,
            extra={"execution_id": execution_id}
        )

        logger_instance.info(
            "RouterStrategy: Routing query to target agent node ID '%s'",
            target_agent_id,
            extra={"execution_id": execution_id}
        )

        pruned_nodes = []
        for n in nodes:
            if n.get("type") == "agent":
                if n.get("id") == target_agent_id:
                    pruned_nodes.append(n)
            else:
                pruned_nodes.append(n)

        pruned_workflow = {
            **workflow,
            "nodes": pruned_nodes
        }

        react = ReactStrategy()
        logger_instance.info(
            "RouterStrategy: Delegating execution to ReactStrategy for routed agent '%s'",
            target_agent_id,
            extra={"execution_id": execution_id}
        )

        result = await react.execute(pruned_workflow, context)

        # Roll the classify call's tokens into the execution totals so the
        # router's true cost is visible (the classify tier is cheap but was
        # previously omitted from input_tokens/output_tokens/total_tokens).
        result["input_tokens"] = (result.get("input_tokens") or 0) + (classify.input_tokens or 0)
        result["output_tokens"] = (result.get("output_tokens") or 0) + (classify.output_tokens or 0)
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]

        result["routing"] = {
            "selected_category": selected_key,
            "target_agent_id": target_agent_id,
            "all_routes": list(routes.keys()),
            "classify_model": classify.model,
            "query_truncated": classify.query_truncated,
            "classify_input_tokens": classify.input_tokens,
            "classify_output_tokens": classify.output_tokens,
        }

        return result
