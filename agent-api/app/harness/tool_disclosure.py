"""Progressive tool disclosure ("tool search") for the agent harness.

Problem: a single MCP server can advertise many tools (Azure DevOps exposes
~90). Binding all of them to the model bloats every request (~5K tokens of
schema) and trips Bedrock guardrail throttling. The previous mitigation —
``app.harness.tool_router.filter_tools`` — keyword-ranked the catalog and kept
only the top-K, which *silently dropped* tools the agent actually needed (e.g.
``wit_get_work_item`` for a "generate test cases for PBI 877" task).

Solution: when the open-ended MCP tool set is large, DON'T bind every tool.
Replace them with two bridge tools — ``search_tools`` and ``call_tool`` — and
let the AGENT discover and invoke any tool on demand. Nothing is dropped; the
model decides what it needs.

Design rules:

* Core/special tools (CloudWatch, Code Crawler/codegraph, DB-schema, playbook,
  planning, filesystem, delegate, edit, …) are NEVER deferred — they stay
  directly bound. Only open-ended MCP tools are candidates for disclosure.
* Threshold gate: if the deferrable tools would cost less than a cutoff
  (~20K tokens of schema, matching the quality cliff), this is a no-op and the
  tools pass through unchanged. So existing CloudWatch/codegraph workflows — and
  the accuracy evals — are untouched; only big MCP servers trigger disclosure.
* The catalog is rebuilt from the live tool list every assembly (no session
  drift).
* ``call_tool`` dispatches through the underlying tool's own ``ainvoke`` so
  pseudonymization wrapping, truncation, and timeouts all fire identically.

This module owns only the substitution logic; the strategy decides when to call
it (replacing the old ``filter_tools`` step).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tool name prefixes for the agent's core families — never deferred.
# ``codegraph_`` matches the codegraph__<tool> prefix; ``repo_`` the generic
# repo_grep/repo_read_file/repo_list_files file tools.
_DEFAULT_KEEP_PREFIXES = "cloudwatch_,code_,codegraph_,repo_,db_,database_,sql_"
# Bridge + agent-writable tool names that must always stay directly bound.
_ALWAYS_KEEP_NAMES = {
    "search_tools", "call_tool",
    "save_playbook", "patch_playbook", "pin_fact",
    "delegate_investigation", "edit_file", "apply_edit",
    "write_todos", "update_todo", "run_command",
    "fs_write", "fs_read", "fs_ls", "fs_grep",
    "fs_append", "fs_upsert", "fs_prune",
    "skill", "search_skills",
}
# char/4 token heuristic for schema size estimation. This is intentionally
# approximate — the gate activates on a soft cliff (20K tokens), not a hard
# limit. Bedrock tokenises closer to 3.5 chars/token for dense JSON, so the
# real token cost can be ~15% higher than this estimate. Acceptable: when the
# threshold is wrong it errs on the side of *not* deferring (binding more
# tools directly), which is the safer direction for accuracy.
_CHARS_PER_TOKEN = 4.0
_DEFAULT_CUTOFF_TOKENS = 20_000
# Count-based trigger: a server like Azure DevOps (~90 tools) is only ~5K tokens
# of schema — under the token cutoff — yet binding all 90 schemas on every call
# is what trips Bedrock guardrail throttling. So we ALSO defer when the open-
# ended tool COUNT exceeds this, regardless of token size. Override via env.
_DEFAULT_MAX_DIRECT_TOOLS = 25


def _tool_schema_chars(tool: Any) -> int:
    """Rough JSON size of a tool's model-visible schema (name + desc + args)."""
    name = getattr(tool, "name", "") or ""
    desc = getattr(tool, "description", "") or ""
    total = len(name) + len(desc)
    args_schema = getattr(tool, "args_schema", None)
    try:
        if args_schema is not None and hasattr(args_schema, "model_json_schema"):
            total += len(json.dumps(args_schema.model_json_schema()))
    except Exception:  # noqa: BLE001
        pass
    return total


def _is_core_tool(tool: Any, keep_prefixes: Tuple[str, ...]) -> bool:
    """A tool is core (never deferred) if its name is pinned or prefix-matched,
    or if an operator tool filter on its node explicitly selected it."""
    name = getattr(tool, "name", "") or ""
    if not name:
        return True  # unnameable → keep (can't search/call it by name)
    if name in _ALWAYS_KEEP_NAMES:
        return True
    if getattr(tool, "router_pinned", False):
        # Operator's explicit per-node allowlist — a deliberate choice; bind it.
        return True
    return any(name.startswith(p) for p in keep_prefixes)


def _enabled() -> str:
    """Disclosure mode: 'auto' (default), 'on', or 'off'."""
    return (os.environ.get("TOOL_DISCLOSURE_MODE", "auto") or "auto").strip().lower()


def _summarize_args(tool: Any) -> str:
    """One-line parameter summary (required first) for a search result."""
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None or not hasattr(args_schema, "model_json_schema"):
        return ""
    try:
        schema = args_schema.model_json_schema()
        props = list((schema.get("properties") or {}).keys())
        required = set(schema.get("required") or [])
        if not props:
            return "(no arguments)"
        ordered = [f"{p}*" if p in required else p for p in props]
        return "args: " + ", ".join(ordered)
    except Exception:  # noqa: BLE001
        return ""


def _build_tool_map(names: List[str]) -> str:
    """Names of the deferred tools, comma-joined — the model-facing tool MAP.

    A search tool the model can't see the shape of is a search tool it won't
    think to use: the map names what was deferred, so the agent knows the
    capability exists and what vocabulary to search with. Names only — the
    schemas are what cost tokens, and those stay deferred. Rides the cached
    prompt prefix (paid once per run, not per turn).

    Truncated to ``tool_map_char_budget`` chars with a ``+K more`` tail; past
    that the list stops being readable anyway and search covers the remainder.
    """
    try:
        from app.config import settings
        budget = int(getattr(settings, "tool_map_char_budget", 2000))
    except Exception:  # noqa: BLE001 — the map must never break tool assembly
        budget = 2000

    ordered = sorted(n for n in names if n)
    if not ordered:
        return ""

    kept: List[str] = []
    used = 0
    for name in ordered:
        cost = len(name) + (2 if kept else 0)  # ", " separator
        if used + cost > budget:
            break
        kept.append(name)
        used += cost

    if not kept:  # pathological: even the first name overflows — count only
        return f"Tool map (deferred, searchable): {len(ordered)} tools — search to find them."
    listing = ", ".join(kept)
    remaining = len(ordered) - len(kept)
    if remaining:
        listing += f" [+{remaining} more — search to find them]"
    return f"Tool map (deferred, searchable): {listing}"


def build_disclosure_tools(deferred: List[Any]) -> List[Any]:
    """Return the [search_tools, call_tool] bridge over *deferred* tools.

    The bridge closes over the live deferred-tool list; ``call_tool`` dispatches
    through each tool's own async invoke so all wrapping still applies.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field
    from app.core.tools.router import rank_tools

    by_name = {getattr(t, "name", ""): t for t in deferred if getattr(t, "name", "")}
    # The ranker tokenizes on word chars and keeps underscores intact, so a name
    # like ``wit_get_work_item`` is one opaque token that never matches a query
    # of plain words ("get work item by id"). Prepend an underscore-split copy of
    # the name to the searchable description so the words rank for BM25.
    # ``name`` stays exact for call dispatch; only the ranking text is enriched.
    catalog_schemas = []
    for t in deferred:
        name = getattr(t, "name", "")
        if not name:
            continue
        words = name.replace("__", " ").replace("_", " ")
        desc = getattr(t, "description", "") or ""
        catalog_schemas.append({"name": name, "description": f"{words} {desc}"})

    class SearchToolsInput(BaseModel):
        query: str = Field(
            description=(
                "What you want to do, in plain words (e.g. 'get a work item by id', "
                "'list test cases', 'read a file from a repo'). Returns the matching "
                "tools with their names and arguments."
            )
        )
        limit: int = Field(
            default=8,
            description="Max number of tools to return (default 8).",
        )

    class CallToolInput(BaseModel):
        tool_name: str = Field(
            description="Exact tool name as returned by search_tools."
        )
        arguments: dict = Field(
            default_factory=dict,
            description="Arguments object for the tool, matching its listed args.",
        )

    async def _search_tools(query: str, limit: int = 8) -> str:
        ranked = rank_tools(catalog_schemas, query)
        top = [s for s, score in ranked[: max(1, min(limit, 25))]]
        if not top:
            return "No tools matched. Try a broader query."
        lines = [f"{len(top)} matching tool(s) for '{query}':", ""]
        for s in top:
            name = s.get("name", "")
            tool = by_name.get(name)
            # Display the ORIGINAL description (catalog text is enriched with
            # split-name words for ranking only — don't show that to the model).
            desc = (getattr(tool, "description", "") if tool else "").strip().replace("\n", " ")
            if len(desc) > 200:
                desc = desc[:200] + "…"
            args = _summarize_args(tool) if tool else ""
            lines.append(f"• {name} — {desc}")
            if args:
                lines.append(f"    {args}")
        lines.append("")
        lines.append("Call one with call_tool(tool_name=..., arguments={...}).")
        return "\n".join(lines)

    async def _call_tool(tool_name: str, arguments: dict) -> str:
        tool = by_name.get(tool_name)
        if tool is None:
            # Be forgiving: suggest near-matches so the model can self-correct.
            hints = [n for n in by_name if tool_name.lower() in n.lower()][:5]
            hint_txt = f" Did you mean: {', '.join(hints)}?" if hints else ""
            return (
                f"[call_tool error] No tool named '{tool_name}'.{hint_txt} "
                f"Use search_tools to find the exact name."
            )
        try:
            # Strip explicit None values — many MCP servers reject null for
            # optional params that they'd happily omit if not provided at all.
            clean = {k: v for k, v in (arguments or {}).items() if v is not None}
            return await tool.ainvoke(clean)
        except Exception as exc:  # noqa: BLE001 — surface to the model, don't crash the loop
            return f"[call_tool error] {tool_name} failed: {exc}"

    _map = _build_tool_map(list(by_name))
    search_tool = StructuredTool.from_function(
        coroutine=_search_tools,
        name="search_tools",
        description=(
            f"Search the catalog of {len(by_name)} available tools by intent and get "
            "back the matching tool names + arguments. Many tools (e.g. an MCP server's "
            "full API) are not all loaded at once to save space — use this to find the "
            "right one, then call it with call_tool. Always search before concluding a "
            "capability is unavailable."
            + (f"\n\n{_map}" if _map else "")
        ),
        args_schema=SearchToolsInput,
    )
    call_tool = StructuredTool.from_function(
        coroutine=_call_tool,
        name="call_tool",
        description=(
            "Invoke a tool discovered via search_tools by its exact name, passing its "
            "arguments. This is how you actually run any tool that is not directly bound."
        ),
        args_schema=CallToolInput,
    )
    return [search_tool, call_tool]


def apply_tool_disclosure(
    tools: List[Any],
    *,
    logger_instance: Optional[Any] = None,
    context_length: Optional[int] = None,
) -> List[Any]:
    """Replace a large open-ended MCP tool set with search_tools + call_tool.

    Returns the (possibly unchanged) tool list to bind. Core/special tools and
    operator-pinned tools are always kept directly bound. A no-op when the
    deferrable set is small (threshold gate) or disclosure is disabled.
    """
    log = logger_instance or logger
    mode = _enabled()
    if mode == "off" or not tools:
        return tools

    keep_prefixes_env = os.environ.get(
        "TOOL_ROUTER_ALWAYS_KEEP_PREFIXES", _DEFAULT_KEEP_PREFIXES
    )
    keep_prefixes = tuple(p.strip() for p in keep_prefixes_env.split(",") if p.strip())

    deferrable = [t for t in tools if not _is_core_tool(t, keep_prefixes)]
    core = [t for t in tools if t not in deferrable]

    if not deferrable:
        return tools

    deferrable_tokens = int(
        sum(_tool_schema_chars(t) for t in deferrable) / _CHARS_PER_TOKEN
    )

    if mode == "auto":
        cutoff = (
            int(context_length * 0.10)
            if context_length and context_length > 0
            else _DEFAULT_CUTOFF_TOKENS
        )
        try:
            max_direct = int(
                os.environ.get("TOOL_DISCLOSURE_MAX_DIRECT", _DEFAULT_MAX_DIRECT_TOOLS)
            )
        except (TypeError, ValueError):
            max_direct = _DEFAULT_MAX_DIRECT_TOOLS
        # Activate on EITHER trigger: too many tools (throttling) or too many
        # tokens (context). Small servers fail both → bind directly (no-op).
        if deferrable_tokens < cutoff and len(deferrable) <= max_direct:
            log.info(
                "tool_disclosure: %d deferrable tool(s) ~%d tokens (<= %d tools, "
                "< %d tokens) — binding directly (no-op)",
                len(deferrable), deferrable_tokens, max_direct, cutoff,
            )
            return tools

    try:
        bridge = build_disclosure_tools(deferrable)
    except Exception as exc:  # noqa: BLE001 — never break a run on disclosure
        log.warning("tool_disclosure: bridge build failed (%s) — binding all tools", exc)
        return tools

    log.info(
        "tool_disclosure: deferring %d MCP tool(s) (~%d tokens) behind "
        "search_tools/call_tool; %d core tool(s) stay bound",
        len(deferrable), deferrable_tokens, len(core),
    )
    return core + bridge
