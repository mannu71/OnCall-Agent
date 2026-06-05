"""Crawler tools exposed as LangChain StructuredTools for the ReAct agent.

When a ``codeAnalyzer`` node is connected to an ``agent`` node in the
workflow graph, the executor calls :func:`build_crawler_tools` to create
6 LangChain-compatible tool instances:

1. ``crawler_index_repo``       — build the repo abstraction overview
2. ``crawler_find_symbol``      — locate a symbol definition (disk-verified)
3. ``crawler_get_body``         — retrieve source lines by handle
4. ``crawler_trace_path``       — trace call-graph edges (callers / callees)
5. ``crawler_search_semantic``  — natural-language code search
6. ``crawler_investigate_alert`` — locate and analyse the code relevant to an alert / error / query
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.config import settings

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field as PydanticField

logger = logging.getLogger(__name__)

# Ceiling for a single crawler tool's serialized JSON output. Without a cap a
# large find/trace/semantic result is replayed in the message history on every
# subsequent ReAct iteration, inflating token cost. Override via env.
CODE_ANALYZER_OUTPUT_MAX_CHARS = settings.code_analyzer_output_max_chars


def _cap(text: str, max_chars: int = CODE_ANALYZER_OUTPUT_MAX_CHARS) -> str:
    """Cap a serialized tool-output string, appending a hint when truncated."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)
    return text[:max_chars] + (
        f"\n…[truncated; showing {max_chars} of {total} chars. "
        f"Narrow the query or paginate (e.g. crawler_get_body page=N) "
        f"to retrieve the rest.]"
    )


def build_crawler_tools(
    repos: Optional[List[Dict[str, str]]] = None,
) -> List[StructuredTool]:
    """Build and return 6 LangChain StructuredTool instances for the crawler.

    These tools use a DAG-based node/flow architecture with an LLM prompt cache
    and a Postgres-backed flow_runs log.

    Args:
        repos: List of repo configs [{"name": str, ...}]. Used for the hint
               appended to tool descriptions; repo boundaries are enforced by
               REPOS_BASE_PATH at runtime.

    Returns:
        6 StructuredTool objects covering index, find, body, trace, semantic,
        and investigate_alert.
    """
    from app.mcp.tools.crawler_tools import (
        crawler_index_repo as _index,
        crawler_find_symbol as _find,
        crawler_get_body as _body,
        crawler_trace_path as _trace,
        crawler_search_semantic as _semantic,
        crawler_investigate_alert as _investigate,
    )

    _repo_names = [r.get("name", "") for r in (repos or [])]
    repo_hint = f" Available repositories: {_repo_names}." if _repo_names else ""

    # ── Input schemas ─────────────────────────────────────────────────────────

    class _IndexInput(BaseModel):
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        force: bool = PydanticField(False, description="Force reindex even if unchanged.")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    class _FindInput(BaseModel):
        symbol: str = PydanticField(..., description="Symbol name to locate.")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        kind: Optional[str] = PydanticField(
            None, description="Type hint: function, class, method, constant."
        )
        limit: int = PydanticField(5, description="Max results (1-20).")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    class _BodyInput(BaseModel):
        handle: str = PydanticField(..., description="Body handle from crawler_find_symbol.")
        page: int = PydanticField(1, description="Page number (1-based).")

    class _TraceInput(BaseModel):
        symbol: str = PydanticField(..., description="Symbol name to trace.")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        direction: str = PydanticField("callers", description="'callers' or 'callees'.")
        depth: int = PydanticField(2, description="Number of hops to trace.")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    class _SemanticInput(BaseModel):
        query: str = PydanticField(..., description="Natural-language query.")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        limit: int = PydanticField(10, description="Max results (default 10).")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    class _InvestigateInput(BaseModel):
        alert: str = PydanticField(..., description="Full alert text (error, stack trace, etc.).")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    # ── Async wrappers (return JSON strings for the agent) ────────────────────

    async def _index_repo(repo: str, force: bool = False, model_id: Optional[str] = None) -> str:
        import json
        result = await _index(repo=repo, force=force, model_id=model_id)
        return _cap(json.dumps(result, default=str))

    async def _find_symbol(
        symbol: str,
        repo: str,
        kind: Optional[str] = None,
        limit: int = 5,
        model_id: Optional[str] = None,
    ) -> str:
        import json
        result = await _find(symbol=symbol, repo=repo, kind=kind, limit=limit, model_id=model_id)
        return _cap(json.dumps(result, default=str))

    async def _get_body(handle: str, page: int = 1) -> str:
        import json
        result = await _body(handle=handle, page=page)
        return _cap(json.dumps(result, default=str))

    async def _trace_path(
        symbol: str, repo: str, direction: str = "callers", depth: int = 2,
        model_id: Optional[str] = None,
    ) -> str:
        import json
        result = await _trace(symbol=symbol, repo=repo, direction=direction, depth=depth, model_id=model_id)
        return _cap(json.dumps(result, default=str))

    async def _search_semantic(
        query: str, repo: str, limit: int = 10, model_id: Optional[str] = None,
    ) -> str:
        import json
        result = await _semantic(query=query, repo=repo, limit=limit, model_id=model_id)
        return _cap(json.dumps(result, default=str))

    async def _investigate_alert(
        alert: str, repo: str, model_id: Optional[str] = None,
    ) -> str:
        import json
        result = await _investigate(alert=alert, repo=repo, model_id=model_id)
        return _cap(json.dumps(result, default=str))

    # ── StructuredTool instances ──────────────────────────────────────────────

    tools = [
        StructuredTool.from_function(
            coroutine=_index_repo,
            name="crawler_index_repo",
            description=(
                "Index a repository so it can be searched with crawler_find_symbol. "
                "Must be called once before find. Subsequent calls are cheap (SHA skip)."
                + repo_hint
            ),
            args_schema=_IndexInput,
        ),
        StructuredTool.from_function(
            coroutine=_find_symbol,
            name="crawler_find_symbol",
            description=(
                "Find where a symbol (function, class, constant) is defined in an indexed repo. "
                "Returns file, line, and a body_handle for use with crawler_get_body. "
                "All results are disk-verified — no hallucinations."
                + repo_hint
            ),
            args_schema=_FindInput,
        ),
        StructuredTool.from_function(
            coroutine=_get_body,
            name="crawler_get_body",
            description=(
                "Retrieve numbered source lines for a body_handle from crawler_find_symbol. "
                "Large files are paginated at 100 lines per page."
            ),
            args_schema=_BodyInput,
        ),
        StructuredTool.from_function(
            coroutine=_trace_path,
            name="crawler_trace_path",
            description=(
                "Trace the call graph for a symbol — find who calls it or what it calls. "
                "Returns edges with file, line, and body_handles. "
                "Direction: 'callers' (who calls it) or 'callees' (what it calls)."
                + repo_hint
            ),
            args_schema=_TraceInput,
        ),
        StructuredTool.from_function(
            coroutine=_search_semantic,
            name="crawler_search_semantic",
            description=(
                "Answer a freeform natural-language query against an indexed repo. "
                "Finds relevant code passages, all disk-verified. "
                "Use for 'where is X implemented?', 'show me the auth flow', etc."
                + repo_hint
            ),
            args_schema=_SemanticInput,
        ),
        StructuredTool.from_function(
            coroutine=_investigate_alert,
            name="crawler_investigate_alert",
            description=(
                "Locate and analyse the code relevant to an alert, error, stack trace, "
                "or natural-language query. Maps the input to code abstractions, reads the "
                "implicated files, and synthesises a structured analysis with suggested "
                "next steps (useful for root-cause analysis, but not limited to it)."
                + repo_hint
            ),
            args_schema=_InvestigateInput,
        ),
    ]

    logger.info("build_crawler_tools: created %d tools (repos=%s)", len(tools), _repo_names)
    return tools


# ---------------------------------------------------------------------------
# Backwards-compat alias — react.py still calls build_crawler_tools directly,
# but any stale reference to build_code_analyzer_tools raises ImportError early.
# ---------------------------------------------------------------------------
build_code_analyzer_tools = None  # intentionally None — v1 deleted
