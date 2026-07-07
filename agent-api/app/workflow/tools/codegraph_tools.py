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
import os
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

#: Connection id used for the in-process codegraph stdio session. Stable so a
#: workflow with two codegraph nodes reuses one connection (is_connected guard).
CODEGRAPH_SERVER_ID = "codegraph"

#: Fast index-backed search tools — annotated as preferred over search_code.
_FAST_SEARCH_TOOLS = frozenset({"search_semantic", "find_symbol", "search_graph"})

#: Prepended to search_code's description to steer the model to the fast tools.
_SEARCH_CODE_WARNING = (
    "[SLOW — greps raw source, often 30-60s on a large repo and may time out. "
    "PREFER the indexed tools first: codegraph__search_semantic(query) for a "
    "concept/description, codegraph__find_symbol(name) for a known symbol, "
    "codegraph__search_graph(query) for structure — each uses the prebuilt index "
    "and returns in ~1s. Use search_code ONLY for an exact literal string the "
    "index can't surface, scoped as narrowly as possible.]\n"
)

#: Prepended to each fast index-backed search tool's description.
_FAST_SEARCH_HINT = (
    "[Fast — uses the prebuilt index; prefer over codegraph__search_code.] "
)


def _build_onnx_semantic_tool(repo_names: List[str]) -> Optional[Any]:
    """Build the ONNX-backed ``codegraph__search_semantic`` StructuredTool.

    Replaces codegraph's native (empty) random-indexing ``search_semantic`` with
    real sentence-transformer embeddings over the project's symbol nodes. Same
    tool name so all existing fast-tool steering / disclosure keeps working.
    Returns None if the feature is off or LangChain isn't importable.
    """
    if not settings.code_semantic_enabled:
        return None
    try:
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field
    except Exception:  # noqa: BLE001
        logger.debug("code_semantic: langchain/pydantic unavailable — skipping tool")
        return None

    from app.core.code_semantic.search import get_code_semantic_search

    _default_project = repo_names[0] if len(repo_names) == 1 else None

    class _SemanticSearchInput(BaseModel):
        query: str = Field(
            description="Natural-language concept/description to search for, e.g. "
            "'where user credentials are validated'."
        )
        project: Optional[str] = Field(
            default=None,
            description="Repo name to search. Optional when only one repo is wired.",
        )
        top_k: int = Field(default=10, ge=1, le=50, description="Max results.")
        include_tests: Optional[bool] = Field(
            default=None,
            description="Test handling. Leave unset for smart default (tests are "
            "de-prioritized unless the query is about tests). Set true when you "
            "WANT tests (e.g. writing/finding test cases); false to suppress them.",
        )

    async def _search_semantic(
        query: str, project: Optional[str] = None, top_k: int = 10,
        include_tests: Optional[bool] = None,
    ) -> str:
        import json

        proj = project or _default_project
        if not proj:
            return json.dumps({
                "error": "specify 'project' — multiple repos are wired",
                "available": repo_names,
            })
        svc = get_code_semantic_search()
        try:
            hits = svc.search(proj, query, top_k=top_k, include_tests=include_tests)
        except FileNotFoundError as exc:
            return json.dumps({"error": f"embedding model not provisioned: {exc}"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("code_semantic: search failed")
            return json.dumps({"error": str(exc)})
        if not hits:
            # Distinguish "index still building in the background" from "no matches"
            # so the agent knows to retry shortly rather than give up.
            if svc.is_indexing(proj):
                return json.dumps({
                    "status": "indexing",
                    "note": f"Semantic index for '{proj}' is building in the "
                            "background (first use on this repo). Retry in a few "
                            "seconds, or use codegraph__search_graph meanwhile.",
                })
            return json.dumps({
                "results": [],
                "note": f"no semantic matches in '{proj}' (indexed by codegraph?)",
            })
        return json.dumps({"project": proj, "results": hits}, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=_search_semantic,
        name="codegraph__search_semantic",
        description=(
            _FAST_SEARCH_HINT
            + "Semantic (embedding) search over indexed code — embeds the actual "
            "function/class SOURCE, so it finds code by MEANING, not literal "
            "tokens. Best first choice for a concept or description (e.g. "
            "'credential handling', 'retry with backoff'). Tests are de-prioritized "
            "by default; pass include_tests=true when you want test code. Returns "
            "ranked entities with file:line."
        ),
        args_schema=_SemanticSearchInput,
    )


def codegraph_inline_config() -> Dict[str, Any]:
    """Inline MCP config to spawn ``codegraph serve`` — no DB row required.

    Env is empty: the container already sets ``CODEGRAPH_DB`` / ``CODEGRAPH_TS_SO``
    on the image, and ``connect_server`` merges ``os.environ`` over this dict.
    """
    return {"command": settings.codegraph_bin, "args": ["serve"], "env": {}}


def cg_project_name(short_name: str) -> str:
    """Derive codegraph's canonical (full-path-based) project name.

    codegraph keys projects internally by their absolute indexed path with
    every path-unsafe char (including ``/``) mapped to ``-`` — e.g.
    ``/app/data/indexed_repos/compliance-api`` → ``app-data-indexed_repos-
    compliance-api``. This mirrors the upstream engine's
    ``cbm_project_name_from_path`` (codebase-memory-mcp/src/pipeline/fqn.c) and
    is deliberate: two repos sharing a basename at different paths must not
    collide. Agents and users should only ever see ``short_name``; this is the
    only place that derives the canonical name, and ``build_codegraph_tools()``
    uses it to alias tool ``project=`` args back to it before each MCP call.
    """
    abs_path = os.path.join(settings.repos_base_path, short_name)
    return abs_path.lstrip('/').replace('/', '-')


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
    _repo_names = [r.get("name", "") for r in (repos or []) if r.get("name")]
    # Clean-name → codegraph's canonical name, applied to every tool's `project`
    # arg at call time (MCPToolWrapper._arun) so the agent only ever needs to
    # know the short name — the ugly internal name never reaches the prompt or
    # the model. Idempotent: a value not in this map (e.g. the agent already
    # passed the canonical name) is left unchanged.
    _project_aliases = {name: cg_project_name(name) for name in _repo_names}
    for _t in tools:
        _suffix = getattr(_t, "name", "").split("__", 1)[-1]
        if _suffix == "search_code":
            try:
                _t.tool_timeout = _search_timeout
                # Steer the model to the fast INDEXED tools first. search_code
                # greps raw source (30-60s on a large repo, often times out); the
                # engine's own description carries no such warning, and the
                # code-analyzer capability prompt is written for the crawler
                # backend's tool names — so without this the agent defaults to the
                # slow grep and stalls (the reported "tools timing out" behaviour).
                _t.description = _SEARCH_CODE_WARNING + (_t.description or "")
            except Exception:  # noqa: BLE001 — never break tool build on this
                logger.debug("build_codegraph_tools: could not steer search_code")
        elif _suffix in _FAST_SEARCH_TOOLS:
            try:
                _t.description = _FAST_SEARCH_HINT + (_t.description or "")
            except Exception:  # noqa: BLE001 — never break tool build on this
                logger.debug("build_codegraph_tools: could not annotate %s", _suffix)
        try:
            _t.project_aliases = _project_aliases
        except Exception:  # noqa: BLE001 — never break tool build on this
            logger.debug("build_codegraph_tools: could not set project_aliases on %s", getattr(_t, "name", "?"))

    # When ONNX code embeddings are enabled, swap codegraph's native (empty)
    # random-indexing `search_semantic` for the real embedding-backed tool. Same
    # tool name, so all the fast-tool steering above and progressive disclosure
    # keep working — the agent just gets meaningful results instead of nothing.
    _onnx_tool = _build_onnx_semantic_tool(_repo_names)
    if _onnx_tool is not None:
        tools = [
            t for t in tools
            if getattr(t, "name", "").split("__", 1)[-1] != "search_semantic"
        ]
        tools.append(_onnx_tool)
        logger.info("build_codegraph_tools: enabled ONNX search_semantic (model=%s)",
                    settings.code_semantic_model)

    logger.info(
        "build_codegraph_tools: exposed %d codegraph tools (repos=%s)",
        len(tools), _repo_names or "none",
    )
    return tools
