"""codegraph backend — native code-intelligence tools for the Code Crawler node.

codegraph is the native C engine baked into the agent-api image
(`/usr/local/bin/codegraph`). It speaks MCP JSON-RPC over stdio (`codegraph
serve`) and exposes ~20 structural/graph/semantic tools.

We drive it **in-process** with an *inline* MCP config — we never register it in
the `mcp_servers` table, so it stays invisible to the platform's MCP governance /
UI and is purely an implementation detail of the node's "codegraph" backend.
This reuses the entire stdio transport / sanitization / reconnect lifecycle in
``MCPClientManager`` and the MCP→LangChain adapter; we only restrict the exposed
tools to the codegraph connection via the adapter's existing ``server_tool_map``.

The returned tools are named ``codegraph__<tool>`` and flow through the harness's
progressive tool disclosure (``apply_tool_disclosure``) like any other MCP tool,
so the full tool set stays available on demand without bloating the prompt.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

#: Connection id used for the in-process codegraph stdio session. Stable so a
#: workflow with two codegraph nodes reuses one connection (is_connected guard).
CODEGRAPH_SERVER_ID = "codegraph"


def codegraph_inline_config() -> Dict[str, Any]:
    """Inline MCP config to spawn ``codegraph serve`` — no DB row required.

    Env is empty: the container already sets ``CODEGRAPH_DB`` / ``CODEGRAPH_TS_SO``
    on the image, and ``connect_server`` merges ``os.environ`` over this dict.
    """
    return {"command": settings.codegraph_bin, "args": ["serve"], "env": {}}


async def build_codegraph_tools(
    mcp_manager: Any,
    repos: Optional[List[Dict[str, str]]] = None,
) -> List[Any]:
    """Connect to the in-process codegraph engine and return its LangChain tools.

    Args:
        mcp_manager: Live ``MCPClientManager`` (per-execution) — the same one the
            crawler/MCP path uses. Owns connection lifecycle + cleanup.
        repos: ``[{name, path}, ...]`` from the code-analyzer node. Used only for
            an availability hint today; indexing happens on save (see
            ``background_indexer.index_workflow_repos_codegraph``).

    Returns:
        List of LangChain ``BaseTool`` instances for codegraph's tools, named
        ``codegraph__<tool>``. Empty list (logged) if the engine can't start, so
        the agent degrades gracefully rather than crashing the turn.
    """
    from app.workflow.mcp.mcp_langchain_adapter import build_langchain_tools

    if not mcp_manager:
        logger.warning("build_codegraph_tools: no mcp_manager — cannot start codegraph")
        return []

    # Connect once (reuse if a prior codegraph node already wired it this run).
    if not mcp_manager.is_connected(CODEGRAPH_SERVER_ID):
        connected = await mcp_manager.connect_server(
            CODEGRAPH_SERVER_ID, codegraph_inline_config()
        )
        if not connected:
            err = getattr(mcp_manager, "last_errors", {}).get(CODEGRAPH_SERVER_ID, "")
            logger.warning(
                "build_codegraph_tools: failed to start codegraph (bin=%s): %s",
                settings.codegraph_bin, err,
            )
            return []

    # Restrict the adapter to ONLY the codegraph connection so we don't pull in
    # any other servers wired this run. get_available_tools(id) returns
    # {id: [tool_names]} — exactly the server_tool_map the adapter expects.
    server_tool_map = mcp_manager.get_available_tools(CODEGRAPH_SERVER_ID)
    tools = build_langchain_tools(mcp_manager, server_tool_map=server_tool_map)

    # Cap only `search_code` — the one tool that shells out to grep over source
    # files (potentially slow on large repos). The fast graph tools keep the
    # default timeout. A single slow grep then errors out at the cap instead of
    # stalling the whole agent run; the agent recovers via the graph tools.
    _search_timeout = settings.codegraph_search_timeout_seconds
    for _t in tools:
        if getattr(_t, "name", "") == f"{CODEGRAPH_SERVER_ID}__search_code":
            try:
                _t.tool_timeout = _search_timeout
            except Exception:  # noqa: BLE001 — never break tool build on this
                logger.debug("build_codegraph_tools: could not cap search_code timeout")

    _repo_names = [r.get("name", "") for r in (repos or []) if r.get("name")]
    logger.info(
        "build_codegraph_tools: exposed %d codegraph tools (repos=%s)",
        len(tools), _repo_names or "none",
    )
    return tools
