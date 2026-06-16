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


# Result fields that hold a list of items we can trim structurally (keeping
# valid JSON) rather than cutting mid-token.
_TRIMMABLE_LIST_FIELDS = ("hits", "results", "symbols", "edges", "callers", "callees")


def _cap(text: str, max_chars: int = CODE_ANALYZER_OUTPUT_MAX_CHARS) -> str:
    """Cap a serialized tool-output string so it can't blow up the context.

    Large find/trace/semantic results are replayed in message history on every
    ReAct iteration, inflating cost. When the payload is a JSON object with a
    list field (hits/results/symbols/…), trim that list until it fits and report
    how many items were dropped — the output stays valid JSON and the agent is
    nudged to narrow or page rather than drown in results. Falls back to a
    boundary-aware char cut for non-JSON payloads.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    total = len(text)

    # Structural trim: keep the head of the first trimmable list field.
    try:
        import json as _json
        obj = _json.loads(text)
        if isinstance(obj, dict):
            field = next(
                (f for f in _TRIMMABLE_LIST_FIELDS
                 if isinstance(obj.get(f), list) and obj[f]),
                None,
            )
            if field:
                full = obj[field]
                kept = full[:]
                while kept and len(_json.dumps({**obj, field: kept}, default=str)) > max_chars:
                    kept = kept[: max(1, len(kept) // 2)] if len(kept) > 1 else []
                obj[field] = kept
                obj["_truncated"] = {
                    "field": field,
                    "shown": len(kept),
                    "total": len(full),
                    "hint": "Result trimmed to fit context. Narrow the query (name_like / a "
                            "more specific term) or crawler_get_body a specific hit for detail.",
                }
                return _json.dumps(obj, default=str)
    except Exception:  # noqa: BLE001 — not JSON / not trimmable → char fallback
        pass

    cut = text.rfind("\n", 0, max_chars)
    if cut < max_chars // 2:
        cut = max_chars
    return text[:cut] + (
        f"\n…[truncated; showing {cut} of {total} chars. "
        f"Narrow the query or paginate (e.g. crawler_get_body page=N) to retrieve the rest.]"
    )


def build_crawler_tools(
    repos: Optional[List[Dict[str, str]]] = None,
    default_model_id: Optional[str] = None,
) -> List[StructuredTool]:
    """Build and return 6 LangChain StructuredTool instances for the crawler.

    These tools use a DAG-based node/flow architecture with an LLM prompt cache
    and a Postgres-backed flow_runs log.

    Args:
        repos: List of repo configs [{"name": str, ...}]. Used for the hint
               appended to tool descriptions; repo boundaries are enforced by
               REPOS_BASE_PATH at runtime.
        default_model_id: Node-level gateway model wired to the agent's
               ``crawler`` port. Used as the crawler LLM when the agent doesn't
               pass an explicit per-call ``model_id``. When None, call_llm falls
               back to its own chain (global "crawler" role → first row → env).

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
        crawler_repo_map as _repo_map,
        crawler_project_brief as _brief,
        crawler_module_doc as _module_doc,
        crawler_coding_standards as _standards,
        crawler_find_feature as _find_feature,
        crawler_grep as _grep,
        crawler_read_file as _read_file,
        crawler_list_files as _list_files,
    )

    _repo_names = [r.get("name", "") for r in (repos or [])]
    repo_hint = f" Available repositories: {_repo_names}." if _repo_names else ""
    if len(_repo_names) > 1:
        repo_hint += (
            " For find/trace/grep/list you may omit repo (or pass repos=[...]) to "
            "search across all connected repos at once."
        )

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
        repos: Optional[List[str]] = PydanticField(
            None, description="Optional list of repos to search across (group-wide search)."
        )

    class _BodyInput(BaseModel):
        handle: str = PydanticField(..., description="Body handle from crawler_find_symbol.")
        page: int = PydanticField(1, description="Page number (1-based).")

    class _TraceInput(BaseModel):
        symbol: str = PydanticField(..., description="Symbol name to trace.")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        direction: str = PydanticField("callers", description="'callers' or 'callees'.")
        depth: int = PydanticField(2, description="Number of hops to trace.")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")
        repos: Optional[List[str]] = PydanticField(
            None, description="Optional list of repos to trace across (group-wide, cross-repo)."
        )

    class _SemanticInput(BaseModel):
        query: str = PydanticField(..., description="Natural-language query.")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        limit: int = PydanticField(10, description="Max results (default 10).")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    class _InvestigateInput(BaseModel):
        alert: str = PydanticField(..., description="Full alert text (error, stack trace, etc.).")
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        model_id: Optional[str] = PydanticField(None, description="LLM model override.")

    class _RepoMapInput(BaseModel):
        repo: Optional[str] = PydanticField(
            None, description="Repository name under REPOS_BASE_PATH. Omit or pass '*' to map "
                              "ALL connected repositories at once (results are labelled by repo).")
        name_like: Optional[str] = PydanticField(
            None, description="Optional case-insensitive substring to filter symbol names.")
        limit: int = PydanticField(60, description="Max symbols to return (default 60).")

    class _RepoInput(BaseModel):
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")

    class _ModuleDocInput(BaseModel):
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        path: Optional[str] = PydanticField(
            None, description="Module/directory path. Omit to list documented modules.")

    class _FindFeatureInput(BaseModel):
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        query: str = PydanticField(..., description="Feature/flow to locate (e.g. 'workflow execution').")

    class _GrepInput(BaseModel):
        pattern: str = PydanticField(..., description="Regex (or literal) to search file contents for.")
        repo: Optional[str] = PydanticField(None, description="Repo to search. Omit to search all connected repos.")
        glob: Optional[str] = PydanticField(None, description="Optional file glob filter, e.g. '*.cs' or 'appsettings*.json'.")
        max_results: int = PydanticField(80, description="Max matches to return.")

    class _ReadFileInput(BaseModel):
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        path: str = PydanticField(..., description="File path relative to the repo root.")
        start: Optional[int] = PydanticField(None, description="First line (1-based) to read.")
        end: Optional[int] = PydanticField(None, description="Last line to read.")

    class _ListFilesInput(BaseModel):
        repo: Optional[str] = PydanticField(None, description="Repo to list. Omit to list across all connected repos.")
        glob: Optional[str] = PydanticField(None, description="Optional file glob filter, e.g. '**/*.tf' or '*Controller.cs'.")
        limit: int = PydanticField(400, description="Max file paths to return.")

    # ── Async wrappers (return JSON strings for the agent) ────────────────────

    async def _index_repo(repo: str, force: bool = False, model_id: Optional[str] = None) -> str:
        import json
        result = await _index(repo=repo, force=force, model_id=model_id or default_model_id)
        return _cap(json.dumps(result, default=str))

    async def _find_symbol(
        symbol: str,
        repo: str,
        kind: Optional[str] = None,
        limit: int = 5,
        model_id: Optional[str] = None,
        repos: Optional[List[str]] = None,
    ) -> str:
        import json
        result = await _find(
            symbol=symbol, repo=repo, kind=kind, limit=limit,
            model_id=model_id or default_model_id,
            repos=repos,
        )
        return _cap(json.dumps(result, default=str))

    async def _get_body(handle: str, page: int = 1) -> str:
        import json
        result = await _body(handle=handle, page=page)
        return _cap(json.dumps(result, default=str))

    async def _trace_path(
        symbol: str, repo: str, direction: str = "callers", depth: int = 2,
        model_id: Optional[str] = None, repos: Optional[List[str]] = None,
    ) -> str:
        import json
        result = await _trace(
            symbol=symbol, repo=repo, direction=direction, depth=depth,
            model_id=model_id or default_model_id,
            repos=repos,
        )
        return _cap(json.dumps(result, default=str))

    async def _search_semantic(
        query: str, repo: str, limit: int = 10, model_id: Optional[str] = None,
    ) -> str:
        import json
        result = await _semantic(
            query=query, repo=repo, limit=limit, model_id=model_id or default_model_id,
        )
        return _cap(json.dumps(result, default=str))

    async def _investigate_alert(
        alert: str, repo: str, model_id: Optional[str] = None,
    ) -> str:
        import json
        result = await _investigate(
            alert=alert, repo=repo, model_id=model_id or default_model_id,
        )
        return _cap(json.dumps(result, default=str))

    async def _repo_map_tool(
        repo: Optional[str] = None, name_like: Optional[str] = None, limit: int = 60,
    ) -> str:
        import json
        # Fan out across all connected repos when repo is omitted or '*'.
        if repo in (None, "", "*", "all") and _repo_names:
            per = max(1, limit // max(1, len(_repo_names)))
            merged = {"repos": _repo_names, "maps": {}}
            for rn in _repo_names:
                r = await _repo_map(repo=rn, name_like=name_like, limit=per)
                merged["maps"][rn] = (
                    {"error": r["error"]} if r.get("error")
                    else {"count": r.get("count"), "total_available": r.get("total_available"),
                          "symbols": r.get("symbols")}
                )
            merged["note"] = ("Map across all connected repos (labelled). Use name_like to "
                              "narrow, or call with a single repo for its full map.")
            return _cap(json.dumps(merged, default=str))
        result = await _repo_map(repo=repo, name_like=name_like, limit=limit)
        return _cap(json.dumps(result, default=str))

    async def _project_brief(repo: str) -> str:
        import json
        return _cap(json.dumps(await _brief(repo=repo), default=str))

    async def _module_doc_tool(repo: str, path: Optional[str] = None) -> str:
        import json
        return _cap(json.dumps(await _module_doc(repo=repo, path=path), default=str))

    async def _coding_standards(repo: str) -> str:
        import json
        return _cap(json.dumps(await _standards(repo=repo), default=str))

    async def _find_feature_tool(repo: str, query: str) -> str:
        import json
        return _cap(json.dumps(await _find_feature(repo=repo, query=query), default=str))

    async def _grep_tool(pattern: str, repo: Optional[str] = None,
                         glob: Optional[str] = None, max_results: int = 80) -> str:
        import json
        # Default scope = all connected repos when repo omitted.
        result = await _grep(pattern, repo=repo,
                             repos=(None if repo else (_repo_names or None)),
                             glob=glob, max_results=max_results)
        return _cap(json.dumps(result, default=str))

    async def _read_file_tool(repo: str, path: str, start: Optional[int] = None,
                              end: Optional[int] = None) -> str:
        import json
        return _cap(json.dumps(await _read_file(repo, path, start=start, end=end), default=str))

    async def _list_files_tool(repo: Optional[str] = None, glob: Optional[str] = None,
                               limit: int = 400) -> str:
        import json
        result = await _list_files(repo=repo,
                                   repos=(None if repo else (_repo_names or None)),
                                   glob=glob, limit=limit)
        return _cap(json.dumps(result, default=str))

    # ── StructuredTool instances ──────────────────────────────────────────────

    tools = [
        StructuredTool.from_function(
            coroutine=_repo_map_tool,
            name="crawler_repo_map",
            description=(
                "Get a cheap, NAMES-ONLY map of an indexed repo's top-level symbols "
                "(class/function/method names + file paths, no source bodies). Call this "
                "FIRST to orient in a codebase, optionally filtered with name_like, then use "
                "crawler_find_symbol / crawler_get_body for the specific symbol you need. "
                "Omit repo (or pass '*') to map ALL connected repos at once. For pinpoint "
                "lookups prefer crawler_find_symbol / crawler_search_semantic (exact, no "
                "sampling). Avoids reading large parts of the codebase. Cached per repo version."
                + repo_hint
            ),
            args_schema=_RepoMapInput,
        ),
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
                "Locate where a named symbol (class/function/method/constant) is defined. "
                "WHEN TO USE: you know the exact (or near-exact) name. Fastest and exact — "
                "knowledge-graph backed, disk-verified, no hallucinations. Returns file, line "
                "and a body_handle. WHEN NOT TO USE: vague/natural-language questions where you "
                "don't know the name — use crawler_search_semantic instead. Next step: call "
                "crawler_get_body on the returned handle to read the code before answering."
                + repo_hint
            ),
            args_schema=_FindInput,
        ),
        StructuredTool.from_function(
            coroutine=_get_body,
            name="crawler_get_body",
            description=(
                "Read the actual numbered source lines for a body_handle returned by "
                "crawler_find_symbol / crawler_search_semantic / crawler_trace_path. "
                "WHEN TO USE: always read the body to confirm relevance BEFORE citing a symbol "
                "as your answer — never conclude from the name alone. Paginated 100 lines/page."
            ),
            args_schema=_BodyInput,
        ),
        StructuredTool.from_function(
            coroutine=_trace_path,
            name="crawler_trace_path",
            description=(
                "Follow the call graph of a known symbol. WHEN TO USE: you already located a "
                "symbol and need 'who calls it' (direction='callers') or 'what it calls' "
                "(direction='callees') — e.g. blast-radius or how data reaches a function. "
                "WHEN NOT TO USE: to first locate a symbol (use crawler_find_symbol). "
                "Returns edges with file, line and body_handles."
                + repo_hint
            ),
            args_schema=_TraceInput,
        ),
        StructuredTool.from_function(
            coroutine=_search_semantic,
            name="crawler_search_semantic",
            description=(
                "Find code by concept or natural-language description. WHEN TO USE: you do NOT "
                "know the exact symbol name — 'where is auth handled?', 'profile assignment "
                "logic', 'how are schedules created'. Knowledge-graph full-text + identifier "
                "search, disk-verified. WHEN NOT TO USE: you know the exact name — prefer "
                "crawler_find_symbol (more precise). Then read hits with crawler_get_body."
                + repo_hint
            ),
            args_schema=_SemanticInput,
        ),
        StructuredTool.from_function(
            coroutine=_investigate_alert,
            name="crawler_investigate_alert",
            description=(
                "Root-cause analysis for an error / stack trace / alert. WHEN TO USE: you have "
                "alert or exception text and want the implicated files plus a structured "
                "analysis and next steps — pass the FULL alert text (message + stack frames). "
                "WHEN NOT TO USE: a plain 'where is X' lookup — use crawler_find_symbol / "
                "crawler_search_semantic instead."
                + repo_hint
            ),
            args_schema=_InvestigateInput,
        ),
        StructuredTool.from_function(
            coroutine=_project_brief,
            name="crawler_project_brief",
            description=(
                "Get the crawler's top-level understanding of a repo: what the product does, its "
                "business domain model, and the architecture narrative (how UI/API/DB/external "
                "systems fit together). WHEN TO USE: at the start of a task to ground yourself in "
                "the project before reasoning about or changing code. Generated once at index "
                "time — cheap to call."
                + repo_hint
            ),
            args_schema=_RepoInput,
        ),
        StructuredTool.from_function(
            coroutine=_coding_standards,
            name="crawler_coding_standards",
            description=(
                "Get a repo's coding-conventions profile: naming, file/folder layout, framework "
                "idioms, error handling, testing, and frontend-vs-backend patterns the codebase "
                "ACTUALLY uses. CALL THIS BEFORE writing or proposing code so your change blends "
                "in with the project's conventions."
                + repo_hint
            ),
            args_schema=_RepoInput,
        ),
        StructuredTool.from_function(
            coroutine=_module_doc_tool,
            name="crawler_module_doc",
            description=(
                "Get the wiki-style doc for a module/directory (responsibility, key components, "
                "data flow, dependencies). Omit 'path' to list documented modules, then call "
                "again with a path. WHEN TO USE: to understand how a part of the codebase works "
                "before editing it."
                + repo_hint
            ),
            args_schema=_ModuleDocInput,
        ),
        StructuredTool.from_function(
            coroutine=_find_feature_tool,
            name="crawler_find_feature",
            description=(
                "Map a user-facing feature/flow to the modules that implement it ('where is X "
                "done?'). WHEN TO USE: you know the feature by name but not the code location, "
                "and want a fast semantic answer before drilling in with crawler_find_symbol / "
                "crawler_get_body."
                + repo_hint
            ),
            args_schema=_FindFeatureInput,
        ),
        StructuredTool.from_function(
            coroutine=_grep_tool,
            name="crawler_grep",
            description=(
                "Regex/text search across repository file CONTENTS (like ripgrep) — any "
                "language, config, or IaC file. WHEN TO USE: find anything by its text when "
                "the knowledge-graph tools don't cover it — a URL/base address, a queue or "
                "topic name, an env/config key, a string literal, a call across services. "
                "Omit repo to search ALL connected repos. Then crawler_read_file the hit."
                + repo_hint
            ),
            args_schema=_GrepInput,
        ),
        StructuredTool.from_function(
            coroutine=_read_file_tool,
            name="crawler_read_file",
            description=(
                "Read any repository file by path, returning numbered lines (optional "
                "start/end range). WHEN TO USE: inspect a file you found via crawler_grep / "
                "crawler_list_files — config (appsettings/yaml/json), IaC (Terraform), or "
                "source in any language not covered by the symbol graph."
            ),
            args_schema=_ReadFileInput,
        ),
        StructuredTool.from_function(
            coroutine=_list_files_tool,
            name="crawler_list_files",
            description=(
                "List repository file paths (optionally filtered by a glob), across one repo "
                "or ALL connected repos. WHEN TO USE: orient in unfamiliar layout or find "
                "config/infra files (e.g. glob '**/*.tf', '*Controller.cs', 'appsettings*.json')."
                + repo_hint
            ),
            args_schema=_ListFilesInput,
        ),
    ]

    logger.info("build_crawler_tools: created %d tools (repos=%s)", len(tools), _repo_names)
    return tools


# ---------------------------------------------------------------------------
# Backwards-compat alias — react.py still calls build_crawler_tools directly,
# but any stale reference to build_code_analyzer_tools raises ImportError early.
# ---------------------------------------------------------------------------
build_code_analyzer_tools = None  # intentionally None — v1 deleted
