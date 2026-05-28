"""Shared Semantic Router classification (cheap LLM tier, token caps)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.core.model_router import NodeRole, explain, model_for
from app.workflow.llm_config import find_llm_node_for_consumer, resolve_llm_config_for_node

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """\
You are an expert routing assistant for an on-call incident response system.
Your job is to analyze the user query and classify it into exactly one of the available category routes.

Available category routes:
{categories_description}

Output ONLY the exact category name key matching the selected route. Do not output markdown, preambles, or quotes. Just output the matching key."""

ROUTER_QUERY_MAX_CHARS = int(os.environ.get("ROUTER_QUERY_MAX_CHARS", "2000"))
ROUTER_CLASSIFY_MAX_OUTPUT = int(os.environ.get("ROUTER_CLASSIFY_MAX_OUTPUT", "32"))


@dataclass
class ClassifyResult:
    """Outcome of a single router classification LLM call."""
    selected_key: str
    target_agent_id: str
    routes: Dict[str, str]
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    query_truncated: bool = False
    lm_connected: bool = False


def _router_routes_from_node(router_node: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Read routes from Langflow ``params`` or legacy ReactFlow ``data``."""
    node_params = router_node.get("params") or {}
    node_data = router_node.get("data") or {}
    routes = node_params.get("routes") or node_data.get("routes") or {}
    routes_desc = (
        node_params.get("routes_description")
        or node_data.get("routes_description")
        or {}
    )
    return routes, routes_desc


def _truncate_query(user_query: str) -> Tuple[str, bool]:
    if len(user_query) <= ROUTER_QUERY_MAX_CHARS:
        return user_query, False
    return user_query[:ROUTER_QUERY_MAX_CHARS], True


def _usage_from_response(response: Any) -> Tuple[int, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    if not isinstance(usage, dict):
        usage = {}
    return (
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
    )


def resolve_category(
    selected_key: str,
    routes: Dict[str, str],
    *,
    log: Optional[logging.Logger] = None,
    execution_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Map LLM category string to canonical key and target agent node id."""
    log = log or logger
    target_agent_id = routes.get(selected_key)
    if not target_agent_id:
        lower_routes = {k.lower(): v for k, v in routes.items()}
        lower_match = lower_routes.get(selected_key.lower())
        if lower_match:
            selected_key = next(k for k in routes if k.lower() == selected_key.lower())
            target_agent_id = lower_match

    if not target_agent_id:
        first_key, first_val = next(iter(routes.items()))
        log.warning(
            "Router: unrecognized category '%s' — falling back to '%s' -> '%s'%s",
            selected_key,
            first_key,
            first_val,
            f" (exec={execution_id})" if execution_id else "",
        )
        selected_key = first_key
        target_agent_id = first_val

    return selected_key, target_agent_id


async def classify_route(
    workflow: Dict[str, Any],
    router_node: Dict[str, Any],
    user_query: str,
    *,
    execution_id: Optional[str] = None,
) -> ClassifyResult:
    """Classify *user_query* into one route using the cheap CLASSIFY tier."""
    router_id = router_node.get("id")
    if not router_id:
        raise ValueError("Router node must have an 'id'.")

    routes, routes_desc = _router_routes_from_node(router_node)
    if not routes or not isinstance(routes, dict):
        raise ValueError("Router node must define a dictionary of routes.")

    llm_config = await resolve_llm_config_for_node(workflow, router_id)
    llm_config["model"] = model_for(NodeRole.CLASSIFY)
    llm_config["max_tokens"] = min(
        int(llm_config.get("max_tokens") or 4096),
        ROUTER_CLASSIFY_MAX_OUTPUT,
    )

    logger.info(
        "Router classify: %s (exec=%s)",
        explain(NodeRole.CLASSIFY),
        execution_id,
    )

    classify_query, query_truncated = _truncate_query(user_query)

    categories_list = []
    for key, target_id in routes.items():
        desc = routes_desc.get(key) or f"Route to agent node: {target_id}"
        categories_list.append(f"- {key}: {desc}")
    categories_description = "\n".join(categories_list)
    valid_keys_line = f"\nValid keys (output exactly one): {', '.join(routes.keys())}"

    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(
            content=ROUTER_SYSTEM_PROMPT.format(
                categories_description=categories_description + valid_keys_line
            )
        ),
        HumanMessage(content=f"Query to classify: {classify_query}"),
    ]

    from app.workflow.strategies.react import ReactStrategy

    react = ReactStrategy()
    llm = react._build_llm(llm_config)
    response = await llm.ainvoke(messages)
    selected_key = (response.content or "").strip().strip("`'\"")

    input_tokens, output_tokens = _usage_from_response(response)
    selected_key, target_agent_id = resolve_category(
        selected_key,
        routes,
        execution_id=execution_id,
    )

    _, lm_connected = find_llm_node_for_consumer(workflow, router_id)

    return ClassifyResult(
        selected_key=selected_key,
        target_agent_id=target_agent_id,
        routes=routes,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=llm_config.get("model", ""),
        query_truncated=query_truncated,
        lm_connected=lm_connected,
    )
