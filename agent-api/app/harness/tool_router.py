"""Action-space filtering for the harness.

Prunes the live tool set to the most relevant subset for a given query before
the agent is bound — a 15-25K-token saving per LLM call on rigs with many MCP
servers. "Special" tools (CloudWatch / code / DB / SQL families, matched by name
prefix) are always kept; only the open-ended MCP tools are ranked and capped.

Extracted verbatim (behaviour-preserving) from ``ReactStrategy.execute`` so the
filtering rule lives in one testable place. Phase 2's later step moves the call
*before* tool instantiation (using ``tool_registry.get_all_schemas(query=...)``)
so unused tools are never built.
"""
from __future__ import annotations

import os
from typing import Any, List

# Tool name prefixes that are never pruned — the agent's core investigation
# families must always be available regardless of relevance score.
_DEFAULT_KEEP_PREFIXES = "cloudwatch_,code_,db_,database_,sql_"
_DEFAULT_TOP_K = "12"


def filter_tools(tools: List[Any], query: str) -> List[Any]:
    """Return *tools* pruned to the top-K most relevant (special tools kept).

    Raises on internal error so the caller can log+fall back to the full set —
    matching the original inline behaviour.
    """
    from app.core.tools.router import ToolRouter

    keep_prefixes_env = os.environ.get("TOOL_ROUTER_ALWAYS_KEEP_PREFIXES", _DEFAULT_KEEP_PREFIXES)
    keep_prefixes = tuple(p.strip() for p in keep_prefixes_env.split(",") if p.strip())
    top_k = int(os.environ.get("TOOL_ROUTER_TOP_K", _DEFAULT_TOP_K))

    # An MCP tool is "rankable" (eligible for pruning) only when it is NOT a
    # core-family tool AND NOT explicitly pinned by an operator tool filter on
    # its node. A node-level filter is a deliberate allowlist — the user already
    # chose those tools, so the relevance ranker must never drop them.
    mcp_tools = [
        t for t in tools
        if hasattr(t, "name")
        and not any(t.name.startswith(p) for p in keep_prefixes)
        and not getattr(t, "router_pinned", False)
    ]
    special_tools = [t for t in tools if t not in mcp_tools]
    if not mcp_tools:
        return tools

    schemas = [
        {"name": t.name, "description": getattr(t, "description", "")}
        for t in mcp_tools
    ]
    filtered = ToolRouter(top_k=top_k).filter(schemas, query=query)
    allowed = {s["name"] for s in filtered}
    return [t for t in mcp_tools if t.name in allowed] + special_tools
