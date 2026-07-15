"""The single subagent factory — builds every ``delegate_*`` tool the harness
exposes: one ``delegate_to_<name>`` tool per named specialist definition
(``AgentSpec.subagents``), ``delegate_parallel`` for concurrent fan-out, and the
generic depth-1 ``delegate_investigation`` tool (built whenever code-analyzer
tools are configured, independent of any named-specialist profile). Each child
runs the same in-house harness with a fresh context window — its own role
prompt, capability set and tool subset — and returns only a concise summary,
keeping the parent's context focused.

This module previously coexisted with a second, divergent implementation
(``subagent.py``'s hard-coded ``delegate_investigation``, gated on code-analyzer
presence, with its own wired-port LLM resolution). That module has been
retired: ``build_generic_delegate_tool`` below reuses the exact same
``_run_child`` engine as the named specialists, so there is one LLM-resolution
path, one tool-scoping path, and one result envelope for every delegation tool.

LLM resolution (single precedence chain for every delegation tool) — a child
inherits the parent by default, and only an EXPLICIT signal overrides that:
  1. the global ``subagent_model_override`` setting (env ``SUBAGENT_MODEL_OVERRIDE``)
     — the deliberate operator hammer that forces every child onto one model.
     Off by default.
  2. per-def ``model`` (a DB-registered llm_config name) — or the literal
     sentinel ``"inherit"`` to explicitly reuse the parent's LLM instance.
  3. the parent's LLM instance — the DEFAULT for an unpinned child (also what
     ``"inherit"`` picks). A workflow's wired/named Language Model node reaches
     the child THROUGH this step, so the workflow's model wins.
  4. the global ``subagent`` model-role assignment — a last resort, consulted
     only when there is no parent LLM to inherit. It no longer silently
     outranks an explicitly-configured parent model (the former behaviour that
     made a stale role assignment override a workflow's chosen model).

In addition to the per-specialist ``delegate_to_<name>`` tools (serial), this
module also builds ``delegate_parallel`` — fan out to several specialists in one
call (asyncio.gather + Semaphore + per-child wait_for).

There is intentionally no fire-and-forget / async-handle delegation mode: an
earlier ``delegate_async`` / ``collect_delegations`` pair backed by an in-process
``_ASYNC_REGISTRY`` was removed because nothing cleared the registry on run
teardown, leaking asyncio.Task handles and growing memory across runs. Serial
(``delegate_to_<name>``) and synchronous parallel (``delegate_parallel``) fan-out
cover the concurrency use case without any standing state.

Config bounds (all override-able via env or per-node/profile):
  * ``DELEGATION_MAX_DEPTH`` (default 1) — spawn depth ceiling.
  * ``DELEGATION_MAX_CONCURRENT`` (default 3) — parallel width.
  * ``DELEGATION_CHILD_TIMEOUT_SECONDS`` (default 240.0) — per-child wall-clock cap.
  * ``DELEGATION_OUTPUT_MAX_CHARS`` (default 8000) — output envelope cap.
  * ``DELEGATION_BLOCKED_TOOLS`` (CSV fnmatch) — always stripped from children,
    even if an allow-list or a def's ``disallowedTools`` would have kept them.

A subagent definition dict::

    {"name": "code-specialist",          # required; → delegate_to_code_specialist
     "description": "…",                 # tool description
     "role_prompt": "You are …",         # role-sentence override
     "capabilities": ["code_analyzer"],  # extra capability ids
     "tools": ["codegraph__*", "fs_*"],  # optional fnmatch allow-list; omit or ["*"] = all
     "disallowedTools": ["fs_write*"],   # optional fnmatch subtract-list on top of "tools"
     "output_schema": "generic",         # optional structured-output schema
     "model": "my-cheap-llm-config",     # optional llm_config name, or "inherit" for parent LLM
     "max_turns": 6,                     # optional recursion cap (~turns, default = global setting)
     "permission_mode": "auto_allow"}    # optional; defaults to auto_allow (parent already gated)
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# The top-level run's stream callback, captured once by the agent node handler
# (app/workflow/executor/handlers/agent.py). A delegated child runs under its own
# execution_id, so its tool activity would normally be invisible to the parent's
# chat stream. Reading this here lets each child forward its tool calls onto the
# PARENT's stream, tagged with the child's name — see _ChildStreamCallback.
_parent_stream_cb: ContextVar[Optional[Any]] = ContextVar(
    "subagent_parent_stream_cb", default=None
)


def set_parent_stream_callback(cb: Any) -> None:
    """Record the top-level run's stream callback for child tool attribution.

    Called once by the agent node handler; children never overwrite it (they
    run at depth 1, no further delegation). Best-effort — never raises.
    """
    try:
        _parent_stream_cb.set(cb)
    except Exception:  # noqa: BLE001 — attribution must never break a run
        pass


# ── Compiled-agent cache (opt-in: settings.subagent_compiled_cache_enabled) ──
# Repeated / parallel delegation to the same specialist rebuilds the child agent
# — an async DB model-role lookup, a boto client build, and a create_react_agent
# compile — on every call. When enabled, we build the child graph once and reuse
# it. Scoped to children with an EXPLICIT per-def model so the baked LLM is a
# fixed DB config (children that inherit the parent LLM are never cached: the
# parent's fallback chain may swap model/region/creds mid-run). Bounded LRU so
# it can't grow without limit (the leak that retired the old async registry).
from collections import OrderedDict as _OrderedDict  # noqa: E402

_COMPILED_CHILD_CACHE: "_OrderedDict[tuple, Any]" = _OrderedDict()
_COMPILED_CHILD_CACHE_MAX = 32
_child_checkpointer_singleton: Optional[Any] = None


def _child_cache_key(
    name: str, model_name: str, sub_tools: List[Any],
    role_prompt: Optional[str], output_schema: Optional[str],
    capabilities: List[str], permission_mode: str,
) -> tuple:
    """Everything that affects the compiled child graph. Excludes the per-call
    session/thread id (that is passed at invoke time, not baked into structure)."""
    tools_sig = tuple(sorted(getattr(t, "name", "") or "" for t in (sub_tools or [])))
    caps_sig = tuple(sorted(capabilities or []))
    return (name, model_name, tools_sig, role_prompt or "", output_schema or "",
            caps_sig, permission_mode or "")


def _child_cache_get(key: tuple) -> Optional[Any]:
    agent = _COMPILED_CHILD_CACHE.get(key)
    if agent is not None:
        _COMPILED_CHILD_CACHE.move_to_end(key)
    return agent


def _child_cache_put(key: tuple, agent: Any) -> None:
    _COMPILED_CHILD_CACHE[key] = agent
    _COMPILED_CHILD_CACHE.move_to_end(key)
    while len(_COMPILED_CHILD_CACHE) > _COMPILED_CHILD_CACHE_MAX:
        _COMPILED_CHILD_CACHE.popitem(last=False)


async def _shared_child_checkpointer() -> Any:
    """A stable checkpointer to bake into cached child graphs. Prefers the
    process-wide durable saver (children keyed by their own thread_id); falls
    back to one shared in-memory saver. Shared (not per-call) so a cached agent
    keeps a valid checkpointer across delegations, and timeout recovery can read
    a child's partial state back by thread_id."""
    global _child_checkpointer_singleton
    try:
        from app.harness.runtime import get_saver
        saver = get_saver()
        if saver is not None:
            return saver
    except Exception:  # noqa: BLE001 — fall through to the in-memory singleton
        pass
    if _child_checkpointer_singleton is None:
        from app.harness.hitl import make_checkpointer
        _child_checkpointer_singleton = await make_checkpointer()
    return _child_checkpointer_singleton


class _ChildStreamCallback:
    """Wrap the parent run's stream callback so a delegated child's tool activity
    surfaces on the PARENT's chat stream, tagged with the child (subagent) name.

    The child's LLM tokens and completion are intentionally suppressed — its
    private reasoning must not interleave into the parent's visible answer — but
    its tool calls/results and errors are forwarded. Any callback method not
    defined here degrades to a no-op via ``__getattr__`` so it stays compatible
    with either engine's callback surface.
    """

    def __init__(self, inner: Any, origin: str, model: str = ""):
        self._inner = inner
        self._origin = origin
        self._model = model or None  # None → inner falls back to the parent model

    async def on_tool_call(self, tool_name: str, args: Any = None, *a: Any, **kw: Any) -> None:
        try:
            await self._inner.on_tool_call(
                tool_name, args or {}, agent=self._origin, model=self._model,
            )
        except Exception:  # noqa: BLE001
            pass

    async def on_tool_result(self, tool_name: str, result: Any = "", *a: Any, **kw: Any) -> None:
        try:
            await self._inner.on_tool_result(
                tool_name, result, failed=bool(kw.get("failed", False)),
                agent=self._origin, model=self._model,
            )
        except Exception:  # noqa: BLE001
            pass

    async def on_error(self, error: Any, *a: Any, **kw: Any) -> None:
        try:
            await self._inner.on_error(error)
        except Exception:  # noqa: BLE001
            pass

    def __getattr__(self, _name: str):
        async def _noop(*_a: Any, **_kw: Any) -> None:
            return None
        return _noop


def _tool_name(t: Any) -> str:
    return getattr(t, "name", "") or ""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return s or "subagent"


def _coerce_tool_globs(value: Any) -> Optional[List[str]]:
    """Normalise the ``tools`` field in a subagent def to Optional[List[str]].

    The UI stores tool filters as a comma-separated string; the backend and
    direct API callers may pass a list. Both forms are accepted here.
    """
    if not value:
        return None
    if isinstance(value, list):
        globs = [str(g).strip() for g in value if g]
        return globs or None
    if isinstance(value, str):
        globs = [g.strip() for g in value.split(",") if g.strip()]
        return globs or None
    return None


def _get_settings():
    try:
        from app.config import settings
        return settings
    except Exception:  # noqa: BLE001
        return None


def _delegation_bounds(agent_config: Dict[str, Any]):
    """Return (max_depth, max_concurrent, timeout_s, output_max, blocked_patterns)."""
    s = _get_settings()
    max_depth = int(getattr(s, "delegation_max_depth", 1) if s else 1)
    max_concurrent = int(getattr(s, "delegation_max_concurrent", 3) if s else 3)
    timeout_s = float(getattr(s, "delegation_child_timeout_seconds", 240.0) if s else 240.0)
    output_max = int(getattr(s, "delegation_output_max_chars", 8000) if s else 8000)
    from app.config import DEFAULT_DELEGATION_BLOCKED_TOOLS
    blocked_csv = getattr(s, "delegation_blocked_tools",
                          DEFAULT_DELEGATION_BLOCKED_TOOLS) if s else DEFAULT_DELEGATION_BLOCKED_TOOLS
    blocked_patterns = [p.strip() for p in (blocked_csv or "").split(",") if p.strip()]
    # Per-node overrides
    if isinstance(agent_config, dict):
        if "delegation_max_concurrent" in agent_config:
            try:
                max_concurrent = int(agent_config["delegation_max_concurrent"])
            except Exception:  # noqa: BLE001
                pass
    return max_depth, max_concurrent, timeout_s, output_max, blocked_patterns


def _scope_tools(
    base_tools: List[Any],
    tool_globs: Optional[List[str]],
    blocked_patterns: List[str],
    depth_remaining: int,
    disallowed_globs: Optional[List[str]] = None,
) -> Tuple[List[Any], List[str]]:
    """Return ``(scoped_tools, unmatched_allow_globs)`` — a child-safe tool subset.

    1. Strip ``delegate_*`` unconditionally (depth cap).
    2. Apply the per-subagent fnmatch allow-list. Omitted or ``["*"]`` means
       "every spawnable tool" (``tools`` undefined or ``['*']`` = all).
    3. Subtract the per-def ``disallowedTools`` glob list, if given.
    4. Strip the global BLOCKED_TOOLS floor — always, even if the allow-list or
       a def's ``disallowedTools`` would have kept them.

    ``unmatched_allow_globs`` lists any allow-list glob that matched zero
    spawnable tools, so a mistyped filter is visible in the caller's logs
    instead of silently shrinking the child's tool set with no explanation
    (mirrors the reference's ``invalidTools`` tracking).
    """
    # Depth cap: children never receive delegation tools regardless of allow-list.
    spawnable = [t for t in base_tools if not _tool_name(t).startswith("delegate_")]

    is_wildcard = not tool_globs or (len(tool_globs) == 1 and tool_globs[0] == "*")
    unmatched: List[str] = []
    if is_wildcard:
        scoped = list(spawnable)
    else:
        scoped = []
        for g in tool_globs:
            matches = [t for t in spawnable if fnmatch.fnmatch(_tool_name(t), g)]
            if not matches:
                unmatched.append(g)
            for t in matches:
                if t not in scoped:
                    scoped.append(t)

    # Per-def disallow list — subtracts on top of the allow-list.
    if disallowed_globs:
        scoped = [
            t for t in scoped
            if not any(fnmatch.fnmatch(_tool_name(t), g) for g in disallowed_globs)
        ]

    # BLOCKED_TOOLS floor — always strip, even if the allow-list would include them.
    if blocked_patterns:
        scoped = [
            t for t in scoped
            if not any(fnmatch.fnmatch(_tool_name(t), p) for p in blocked_patterns)
        ]
    return scoped, unmatched


def _summarize_tool_names(sub_tools: List[Any], limit: int = 12) -> str:
    """Human-readable summary of a child's ACTUAL scoped tool names.

    The parent model is shown exactly which tools each specialist holds, so it
    routes by capability rather than by the specialist's (possibly misleading)
    NAME. A squad named
    "CloudWatch Squad" that was scoped to ``codegraph__*``/``postgres-*`` reads
    as such here, so the parent keeps CloudWatch work on its own direct tools.
    """
    names = [n for n in (_tool_name(t) for t in sub_tools) if n]
    if not names:
        return "none"
    if len(names) > limit:
        return ", ".join(names[:limit]) + f", …(+{len(names) - limit} more)"
    return ", ".join(names)


def _format_specialist_line(name: str, description: str, sub_tools: List[Any]) -> str:
    """Format one specialist listing line: ``- <name>: <desc> (tools: …)``.

    The tool list is the child's
    resolved scoped set (post allow/deny/blocked filtering), not the raw glob,
    so the parent sees what the specialist can ACTUALLY do.
    """
    desc = (description or "").strip()
    tools_desc = _summarize_tool_names(sub_tools)
    if desc:
        return f"- {name}: {desc} (tools: {tools_desc})"
    return f"- {name} (tools: {tools_desc})"


# Steering shared by every fan-out tool: keep direct-tool work on the parent,
# reserve delegation for what a specialist's OWN tools can do. Prevents the
# parent from routing a task to a specialist that lacks the needed tool family
# (e.g. delegating CloudWatch work to a code/DB-scoped squad) when the parent
# holds that tool directly.
_DELEGATION_ROUTING_STEER = (
    "Route by the tools listed for each specialist below — NOT by its name. "
    "Use your OWN directly-attached tools for anything they cover. But when a "
    "request needs a capability you do NOT hold directly and a specialist DOES "
    "(e.g. querying live logs / metrics / alarms via cloudwatch__* tools, or "
    "searching code via codegraph__* tools), you MUST delegate that work to the "
    "specialist that holds those tools. In that case do NOT answer that you lack "
    "access, and do NOT ask the user where the data lives or which backend to use "
    "— the specialist already has direct access; hand it the task and use what it "
    "returns."
)


def _infer_child_context_flags(sub_tools: List[Any]) -> Tuple[bool, bool]:
    """Infer (has_code_analyzer, has_cloudwatch) for a child from its ACTUAL
    scoped tool set, so a named specialist scoped to ``codegraph__*`` / ``repo_*`` /
    ``cloudwatch_*`` gets the same code/CloudWatch-aware system-prompt guidance a
    top-level agent with those tools would (agent_builder.py / conversational.py
    branch on these).
    """
    names = [_tool_name(t) for t in sub_tools]
    has_code_analyzer = any(n.startswith(("codegraph__", "repo_")) for n in names)
    has_cloudwatch = any(n.startswith("cloudwatch_") for n in names)
    return has_code_analyzer, has_cloudwatch


def _extract_fallback_answer(result: Dict[str, Any]) -> str:
    """Scan serialized child messages for the last non-empty assistant text.

    ``execute_agent``'s own serializer already backfills ``final_answer`` from
    the last AIMessage content when no terminal (tool-call-free) message
    produced one. This is one more defensive layer for the rare case where
    every assistant turn in the child's history was tool-calls-only with no
    text at all (e.g. the child hit its recursion limit before ever
    synthesizing).
    """
    for entry in reversed(result.get("messages") or []):
        if entry.get("role") == "assistant" and entry.get("content"):
            return str(entry["content"]).strip()
    return ""


async def _combine_context(
    parent_execution_id: Optional[str], explicit_context: Optional[str],
) -> Optional[str]:
    """Prepend the parent session's metamemory handoff (context_summary.txt
    + milestones.txt, read-only — see app.harness.metamemory) to an explicit
    per-call context string. Best-effort: returns ``explicit_context``
    unchanged when metamemory is inactive/empty/unreadable or on any error.
    Used by every delegation tool so a child gets the parent's distilled
    working state without re-deriving it from scratch.
    """
    try:
        from app.harness import metamemory
        block = await metamemory.read_context_block(parent_execution_id)
    except Exception:  # noqa: BLE001 — handoff must never break delegation
        block = None
    if not block:
        return explicit_context
    if explicit_context:
        return f"[Parent session state]\n{block}\n\n[Task-specific context]\n{explicit_context}"
    return f"[Parent session state]\n{block}"


async def _write_child_output(
    parent_execution_id: Optional[str], path: Optional[str], envelope_str: str,
) -> None:
    """Write a child's JSON envelope to a VFS path in the PARENT's session
    (spawn contract's output_contract.write_to) — best-effort, never raises.
    Lets a caller (e.g. delegate_batch) aggregate deterministically from
    files instead of only the tool's own return value.
    """
    if not (parent_execution_id and path):
        return
    try:
        from app.core.vfs.backend import vfs_write
        await vfs_write(parent_execution_id, path, envelope_str)
    except Exception:  # noqa: BLE001 — output persistence must never break delegation
        pass


@dataclass
class OutputContract:
    """Where/how a child's result should land, beyond the tool's return value."""

    format: str = "text"  # "text" | "json" — informational; envelope is always JSON today
    schema_ref: Optional[str] = None
    write_to: Optional[str] = None  # VFS path (parent session) to also write the envelope to


@dataclass
class ContextSlice:
    """What the child sees of the parent's state, beyond its own task text."""

    files: List[str] = field(default_factory=list)  # VFS paths (parent session), read-only
    inline: Optional[str] = None  # short parent-selected excerpt (<=2000 chars enforced)
    inherit_history: bool = False  # reserved for forward-compat; not implemented


@dataclass
class ChildBudget:
    max_turns: Optional[int] = None
    timeout_seconds: Optional[float] = None
    token_energy: Optional[int] = None  # reserved for the metabolic token economy
    depth: int = 1
    max_depth: int = 1


@dataclass
class ChildTrace:
    parent_execution_id: Optional[str] = None
    span_id: Optional[str] = None


@dataclass
class ChildRunSpec:
    """The formal spawn-contract payload for one delegated child run.

    Internalizes what ``_run_child`` already accepts as loose kwargs into one
    typed object with three additions ``_run_child`` itself doesn't know
    about: ``output_contract`` (persist the result to VFS, not just return
    it), ``context_slice`` (read-only parent-state handoff), and ``budget``/
    ``trace`` (grouping the existing depth/timeout/parent-id fields plus
    forward-compat fields for the metabolic token economy). Consumed by
    :func:`run_child_spec`.
    """

    task: str
    name: str = "subagent"
    role_prompt: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    output_schema: Optional[str] = None
    model: Optional[str] = None
    permission_mode: str = "auto_allow"
    output_contract: OutputContract = field(default_factory=OutputContract)
    context_slice: ContextSlice = field(default_factory=ContextSlice)
    budget: ChildBudget = field(default_factory=ChildBudget)
    trace: ChildTrace = field(default_factory=ChildTrace)


async def run_child_spec(
    spec: ChildRunSpec,
    *,
    llm: Any,
    sub_tools: List[Any],
    agent_config: Dict[str, Any],
) -> str:
    """Spawn-contract entrypoint: resolve context_slice, run the child via
    the existing :func:`_run_child` engine, then honor output_contract.

    Bounds default to :func:`_delegation_bounds` when the spec's budget
    leaves them unset, so a caller only needs to override what it cares
    about. Returns the same JSON envelope ``_run_child`` returns.
    """
    _, _, default_timeout, default_output_max, _ = _delegation_bounds(agent_config)
    timeout_s = spec.budget.timeout_seconds if spec.budget.timeout_seconds is not None else default_timeout
    output_max = default_output_max

    context_parts: List[str] = []
    if spec.context_slice.files:
        parent_session = spec.trace.parent_execution_id
        from app.core.vfs.backend import vfs_read
        for path in spec.context_slice.files:
            try:
                content = await vfs_read(parent_session, path)
            except Exception:  # noqa: BLE001 — a missing/unreadable file is skipped, not fatal
                continue
            if content and content.strip():
                context_parts.append(f"[{path}]\n{content.strip()}")
    if spec.context_slice.inline:
        context_parts.append(spec.context_slice.inline[:2000])
    context = "\n\n".join(context_parts) or None
    context = await _combine_context(spec.trace.parent_execution_id, context)

    envelope_str = await _run_child(
        llm=llm, sub_tools=sub_tools, agent_config=agent_config,
        name=spec.name, role_prompt=spec.role_prompt, capabilities=spec.capabilities,
        output_schema=spec.output_schema, model_name=spec.model,
        depth_remaining=spec.budget.depth, parent_execution_id=spec.trace.parent_execution_id,
        timeout_s=timeout_s, output_max=output_max,
        task=spec.task, context=context,
        max_turns=spec.budget.max_turns, permission_mode=spec.permission_mode,
    )
    await _write_child_output(
        spec.trace.parent_execution_id, spec.output_contract.write_to, envelope_str,
    )
    return envelope_str


async def _run_child(
    *,
    llm: Any,
    sub_tools: List[Any],
    agent_config: Dict[str, Any],
    name: str,
    role_prompt: Optional[str],
    capabilities: List[str],
    output_schema: Optional[str],
    model_name: Optional[str],
    depth_remaining: int,
    parent_execution_id: Optional[str],
    timeout_s: float,
    output_max: int,
    task: str,
    context: Optional[str] = None,
    max_turns: Optional[int] = None,
    permission_mode: str = "auto_allow",
) -> str:
    """Shared child-runner used by every delegation tool (serial, parallel, and
    the generic depth-1 delegate). Resolves the child LLM, builds an AgentSpec,
    runs the agent (routed through the same engine flag as the parent, via
    app.harness.engine.run_agent_once) under asyncio.wait_for, and returns a
    uniform JSON envelope capped at output_max.
    """
    from app.harness import AgentSpec
    from app.harness.engine import resolve_engine, run_agent_once
    from app.harness.hitl import make_checkpointer

    q = (task or "").strip()
    if not q:
        return json.dumps({"subagent": name, "task": task, "error": "empty task"})
    if context:
        q += f"\n(Context: {context})"

    sub_id = f"{parent_execution_id or 'sub'}-{name}-{uuid.uuid4().hex[:6]}"
    logger.info("delegation: spawning child %s (depth_remaining=%d)", sub_id, depth_remaining)
    t0 = time.monotonic()

    inherit = isinstance(model_name, str) and model_name.strip().lower() == "inherit"

    from app.config import settings as _settings
    # Explicit global operator override: forces every child onto one model,
    # ahead of per-def and parent alike.
    _override = (getattr(_settings, "subagent_model_override", None) or "").strip()

    # ── Compiled-agent cache (opt-in) ────────────────────────────────────
    # Eligible only for LangGraph children with an explicit per-def model (a
    # fixed DB config). On a hit we skip LLM resolution AND the graph build.
    # Disabled when an override is active: the cache keys on ``model_name`` but
    # the baked LLM would be the override's, so a per-def key could return the
    # wrong compiled agent.
    _explicit_model = bool(model_name) and not inherit
    _cache_eligible = (
        getattr(_settings, "subagent_compiled_cache_enabled", False)
        and _explicit_model
        and not _override
        and resolve_engine(agent_config if isinstance(agent_config, dict) else {}) == "langgraph"
    )
    _cache_key: Optional[tuple] = None
    _cached_compiled: Optional[Any] = None
    if _cache_eligible:
        _cache_key = _child_cache_key(
            name, model_name.strip(), sub_tools, role_prompt, output_schema,
            capabilities, permission_mode or "auto_allow",
        )
        _cached_compiled = _child_cache_get(_cache_key)

    # ── Child LLM resolution (single precedence chain for every delegation
    #    tool — see the module docstring):
    #      1. explicit global override  →  2. per-def model / "inherit"  →
    #      3. inherit the parent LLM (default)  →  4. "subagent" role (last resort)
    # A child inherits the parent by default, so a workflow's wired Language
    # Model node reaches the child unless it pins its own model or an operator
    # has set an explicit override. Skipped on a compiled-cache hit — the LLM
    # is baked into the graph.
    sub_llm = llm
    if _cached_compiled is not None:
        logger.info("delegation: child %s reusing cached compiled agent (model=%s)", sub_id, model_name)
    else:
        try:
            from app.workflow.strategies.react.llm_factory import build_llm
            from app.workflow.llm_config import resolve_llm_config_by_name
            if _override:
                sub_cfg = await resolve_llm_config_by_name(_override)
                sub_llm = build_llm(sub_cfg)
                logger.info("delegation: child %s using global override model=%s", sub_id, _override)
            elif model_name and not inherit:
                sub_cfg = await resolve_llm_config_by_name(model_name)
                sub_llm = build_llm(sub_cfg)
                logger.info("delegation: child %s using per-def model=%s", sub_id, model_name)
            elif llm is not None:
                # Default: inherit the parent's LLM (also what "inherit" picks).
                # The wired/named parent model now wins over the "subagent" role.
                logger.info("delegation: child %s inheriting parent LLM", sub_id)
            else:
                # Last resort — no parent LLM to inherit. Fall back to the
                # "subagent" gateway role (then the global default inside
                # resolve_llm_config_for_role).
                from app.infrastructure.persistence import model_role_repository
                role_name = await model_role_repository.get("subagent")
                if role_name:
                    from app.workflow.llm_config import resolve_llm_config_for_role
                    sub_cfg = await resolve_llm_config_for_role("subagent")
                    sub_llm = build_llm(sub_cfg)
                    logger.info("delegation: child %s using role model=%s (no parent LLM)", sub_id, role_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delegation: child %s LLM resolution failed (%s) — using parent LLM", sub_id, exc)
            sub_llm = llm

    try:
        # A cached child bakes in a SHARED checkpointer (keyed by thread_id per
        # call); an uncached child gets its own fresh one, as before.
        cp = await _shared_child_checkpointer() if _cache_eligible else await make_checkpointer()
        sub_agent_config = agent_config if isinstance(agent_config, dict) else (agent_config or {})
        has_code_analyzer, has_cloudwatch = _infer_child_context_flags(sub_tools)
        sub_spec = AgentSpec(
            agent_config=sub_agent_config,
            has_code_analyzer=has_code_analyzer,
            has_cloudwatch=has_cloudwatch,
            permission_mode=permission_mode or "auto_allow",
            session_id=sub_id,
            capabilities=list(capabilities),
            role_prompt=role_prompt,
            output_schema=output_schema,
            subagents=[],  # depth-capped
        )

        # Build the compiled child once and cache it (miss path). Uses a stable
        # session id (per specialist, not per call) so the compaction closure is
        # consistent across reuse, forces HITL off (children never interrupt),
        # and bakes in the shared checkpointer. The per-call task/thread_id/
        # recursion_limit/stream are still passed at invoke time below.
        if _cache_eligible and _cached_compiled is None:
            from app.harness import build_agent_from_spec
            _cached_spec = AgentSpec(
                agent_config={**sub_agent_config, "hitl_enabled": False},
                has_code_analyzer=has_code_analyzer,
                has_cloudwatch=has_cloudwatch,
                permission_mode=permission_mode or "auto_allow",
                session_id=f"child:{name}",
                capabilities=list(capabilities),
                role_prompt=role_prompt,
                output_schema=output_schema,
                subagents=[],
            )
            _cached_compiled = build_agent_from_spec(
                _cached_spec, sub_llm, sub_tools, checkpointer=cp, execution_port=None,
            )
            _child_cache_put(_cache_key, _cached_compiled)
            logger.info("delegation: child %s compiled + cached (model=%s)", sub_id, model_name)

        # ~2 LangGraph steps per ReAct turn (agent + tool node) — see
        # agent_runner.execute_agent's own recursion_limit comment. Coerce
        # defensively since defs may arrive from raw API calls, not just the UI.
        # The native engine's dispatcher halves this back into a turn count
        # (run_agent_once._run_native), so this stays the one shared unit
        # passed to either engine.
        recursion_limit: Optional[int] = None
        if max_turns:
            try:
                recursion_limit = int(max_turns) * 2
            except (TypeError, ValueError):
                logger.warning(
                    "delegation: child %s ignoring invalid max_turns=%r", sub_id, max_turns,
                )

        # Surface the child's tool calls on the parent's chat stream, tagged with
        # this subagent's name (inline attribution). Suppresses child tokens — see
        # _ChildStreamCallback. No-op when there is no parent stream (e.g. a
        # non-streamed run).
        child_cb: Optional[Any] = None
        _pcb = _parent_stream_cb.get()
        if _pcb is not None:
            # Prefer the child's effective model for attribution: an explicit
            # override wins, else its per-def model. When it inherits the parent
            # LLM (override unset and model_name unset/"inherit"), leave blank so
            # the event falls back to the parent's model label.
            _child_model = _override or ("" if (not model_name or inherit) else str(model_name))
            child_cb = _ChildStreamCallback(_pcb, name, model=_child_model)

        child_engine = resolve_engine(sub_agent_config)

        # Detach the child from the parent's LangChain callback context. When the
        # parent runs via LangGraph ``astream_events`` (e.g. HITL forces it), that
        # stream otherwise ALSO captures the child's nested tool calls and tags
        # them as the main agent — producing duplicate rows (one "agent", one
        # subagent) for every child call. The child reports its own activity via
        # ``child_cb``, so we reset the propagated config to None for the child's
        # run. Best-effort: a missing internal API just leaves the harmless dupes.
        _var_cfg = None
        _cfg_token = None
        try:
            from langchain_core.runnables.config import var_child_runnable_config as _var_cfg
            _cfg_token = _var_cfg.set(None)
        except Exception:  # noqa: BLE001
            _var_cfg = None

        try:
            if timeout_s > 0:
                result = await asyncio.wait_for(
                    run_agent_once(
                        sub_spec, sub_llm, sub_tools, q,
                        logger_instance=logger, execution_id=sub_id, thread_id=sub_id,
                        recursion_limit=recursion_limit, checkpointer=cp, engine=child_engine,
                        stream_callback=child_cb, compiled_agent=_cached_compiled,
                    ),
                    timeout=timeout_s,
                )
            else:
                result = await run_agent_once(
                    sub_spec, sub_llm, sub_tools, q,
                    logger_instance=logger, execution_id=sub_id, thread_id=sub_id,
                    recursion_limit=recursion_limit, checkpointer=cp, engine=child_engine,
                    stream_callback=child_cb, compiled_agent=_cached_compiled,
                )
        finally:
            if _var_cfg is not None and _cfg_token is not None:
                try:
                    _var_cfg.reset(_cfg_token)
                except Exception:  # noqa: BLE001
                    pass

        answer = (result.get("final_answer") or "").strip()
        if not answer:
            answer = _extract_fallback_answer(result)
        answer = answer or "(subagent produced no answer)"
        duration = round(time.monotonic() - t0, 2)
        envelope = json.dumps(
            {
                "subagent": name,
                "task": task,
                "status": "ok",
                "answer": answer,
                "sub_tool_calls": len(result.get("tool_calls", []) or []),
                "duration_s": duration,
                "tokens": int(result.get("total_tokens", 0) or 0),
            },
            default=str,
        )
        return envelope[:output_max]
    except asyncio.TimeoutError:
        logger.warning("delegation: child %s timed out after %.0fs", sub_id, timeout_s)
        # Best-effort: recover what the child accomplished before the cutoff from
        # its checkpointer so the parent can report an accurate PARTIAL result.
        partial = ""
        n_calls = 0
        try:
            _snap = await cp.aget_tuple({"configurable": {"thread_id": sub_id}})
            _msgs = ((_snap.checkpoint or {}).get("channel_values", {}) or {}).get("messages", []) if _snap else []
            for _m in reversed(_msgs):
                if getattr(_m, "type", "") == "ai" and getattr(_m, "content", ""):
                    _c = _m.content
                    partial = _c if isinstance(_c, str) else str(_c)
                    break
            n_calls = sum(1 for _m in _msgs if getattr(_m, "type", "") == "tool")
        except Exception:  # noqa: BLE001 — partial recovery must never mask the timeout
            partial, n_calls = "", 0
        # Steer the parent away from concluding the TOOLS are unavailable: they
        # responded (n_calls completed) but the queries are slow on a large repo
        # and the subagent hit its time budget. This is a partial/slow result.
        guidance = (
            f"The '{name}' subagent hit its {timeout_s:.0f}s time budget. Its tools ARE "
            f"available and responded ({n_calls} tool call(s) completed) — the queries are "
            "just slow on this large codebase, so it could not finish in time. Treat this as "
            "a PARTIAL / slow result: do NOT report the code-analysis tools as unavailable. "
            "If you need more, retry with ONE narrower, specific query."
        )
        return json.dumps({
            "subagent": name, "task": task, "status": "timeout",
            "answer": (partial + "\n\n" + guidance) if partial else guidance,
            "sub_tool_calls": n_calls,
            "error": f"exceeded {timeout_s:.0f}s budget (tools available but slow)",
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("delegation: child %s failed: %s", sub_id, exc)
        return json.dumps({"subagent": name, "task": task, "status": "error", "error": str(exc)})


# ── Serial delegate_to_<name> tools (existing behaviour) ─────────────────────

def build_subagent_tools(
    llm: Any,
    base_tools: List[Any],
    agent_config: Dict[str, Any],
    subagent_defs: List[Dict[str, Any]],
    *,
    parent_execution_id: Optional[str] = None,
    depth_remaining: int = 1,
) -> List[Any]:
    """Build one ``delegate_to_<name>`` StructuredTool per definition."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    _, _, timeout_s, output_max, blocked_patterns = _delegation_bounds(agent_config)

    if depth_remaining <= 0 or not subagent_defs:
        return []

    class _SubInput(BaseModel):
        task: str = PydanticField(description="The focused task to hand to this subagent.")
        context: Optional[str] = PydanticField(
            default=None, description="Optional extra context / hint for the subagent.")

    tools: List[Any] = []
    for _def in subagent_defs:
        if not isinstance(_def, dict) or not _def.get("name"):
            continue
        name = _slug(_def["name"])
        role_prompt = _def.get("role_prompt") or None
        capabilities = _def.get("capabilities") or []
        output_schema = _def.get("output_schema") or None
        tool_globs = _coerce_tool_globs(_def.get("tools"))
        disallowed_globs = _coerce_tool_globs(_def.get("disallowedTools"))
        model_name = _def.get("model") or None
        max_turns = _def.get("max_turns")
        permission_mode = _def.get("permission_mode") or "auto_allow"

        sub_tools, unmatched = _scope_tools(
            base_tools, tool_globs, blocked_patterns, depth_remaining,
            disallowed_globs=disallowed_globs,
        )
        if unmatched:
            logger.warning(
                "subagent def '%s': tool glob(s) matched nothing: %s", name, unmatched,
            )

        # Surface the child's ACTUAL scoped tools + a route-by-capability steer so
        # the parent doesn't delegate work this specialist can't do (e.g. sending
        # CloudWatch work to a code/DB-scoped squad the parent misread by name).
        description = _def.get("description") or (
            f"Delegate a scoped task to the '{name}' subagent, which runs its own loop "
            f"and returns a concise summary. Use for a separable part of a larger task."
        )
        description = (
            f"{description}\n(tools: {_summarize_tool_names(sub_tools)}) "
            f"{_DELEGATION_ROUTING_STEER}"
        )

        tools.append(
            _make_serial_tool(
                StructuredTool, _SubInput, llm, sub_tools, agent_config, name,
                role_prompt, capabilities, output_schema, description,
                parent_execution_id, depth_remaining, timeout_s, output_max,
                model_name=model_name, max_turns=max_turns, permission_mode=permission_mode,
            )
        )
    return tools


def _make_serial_tool(
    StructuredTool, _SubInput, llm, sub_tools, agent_config, name, role_prompt,
    capabilities, output_schema, description, parent_execution_id, depth_remaining,
    timeout_s, output_max, model_name: Optional[str] = None,
    max_turns: Optional[int] = None, permission_mode: str = "auto_allow",
):
    async def _delegate(task: str, context: Optional[str] = None) -> str:
        return await _run_child(
            llm=llm, sub_tools=sub_tools, agent_config=agent_config,
            name=name, role_prompt=role_prompt, capabilities=capabilities,
            output_schema=output_schema, model_name=model_name,
            depth_remaining=depth_remaining, parent_execution_id=parent_execution_id,
            timeout_s=timeout_s, output_max=output_max,
            task=task, context=await _combine_context(parent_execution_id, context),
            max_turns=max_turns, permission_mode=permission_mode,
        )

    return StructuredTool.from_function(
        coroutine=_delegate,
        name=f"delegate_to_{name}",
        description=description,
        args_schema=_SubInput,
    )


# ── Parallel fan-out tool ─────────────────────────────────────────────────────

def _build_specialist_registry(
    base_tools: List[Any],
    subagent_defs: List[Dict[str, Any]],
    blocked_patterns: List[str],
    depth_remaining: int,
) -> Dict[str, Dict[str, Any]]:
    """Pre-build per-specialist scoped tool sets + meta, keyed by slug name.

    Shared by every multi-specialist fan-out tool (delegate_parallel,
    delegate_batch) so the scoping/logging logic lives in exactly one place.
    """
    by_name: Dict[str, Dict[str, Any]] = {}
    for _def in subagent_defs:
        if not isinstance(_def, dict) or not _def.get("name"):
            continue
        n = _slug(_def["name"])
        _sub_tools, _unmatched = _scope_tools(
            base_tools, _coerce_tool_globs(_def.get("tools")), blocked_patterns, depth_remaining,
            disallowed_globs=_coerce_tool_globs(_def.get("disallowedTools")),
        )
        if _unmatched:
            logger.warning(
                "subagent def '%s': tool glob(s) matched nothing: %s", n, _unmatched,
            )
        by_name[n] = {
            "description": _def.get("description") or "",
            "role_prompt": _def.get("role_prompt") or None,
            "capabilities": _def.get("capabilities") or [],
            "output_schema": _def.get("output_schema") or None,
            "model_name": _def.get("model") or None,
            "max_turns": _def.get("max_turns"),
            "permission_mode": _def.get("permission_mode") or "auto_allow",
            "sub_tools": _sub_tools,
        }
    return by_name


def build_delegate_parallel_tool(
    llm: Any,
    base_tools: List[Any],
    agent_config: Dict[str, Any],
    subagent_defs: List[Dict[str, Any]],
    *,
    parent_execution_id: Optional[str] = None,
    depth_remaining: int = 1,
) -> Optional[Any]:
    """Build ``delegate_parallel`` — fan out to several specialists in one call.

    The orchestrator passes a list of ``{specialist, task, context?}`` items;
    all specialists run concurrently under ``asyncio.Semaphore(max_concurrent)``
    with individual ``asyncio.wait_for`` timeouts. One specialist timing out or
    failing never crashes the batch — it returns a per-item error envelope.
    Returns ``None`` when there are no subagent defs to fan out to.
    """
    if not subagent_defs or depth_remaining <= 0:
        return None

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    _, max_concurrent, timeout_s, output_max, blocked_patterns = _delegation_bounds(agent_config)

    _by_name = _build_specialist_registry(base_tools, subagent_defs, blocked_patterns, depth_remaining)
    valid_names = list(_by_name.keys())

    class _ParallelItem(BaseModel):
        specialist: str = PydanticField(
            description=f"Specialist name, one of: {', '.join(valid_names)}")
        task: str = PydanticField(description="The focused task for this specialist.")
        context: Optional[str] = PydanticField(default=None, description="Optional extra context.")

    class _ParallelInput(BaseModel):
        delegations: List[_ParallelItem] = PydanticField(
            description="List of specialist+task pairs to run concurrently.")

    async def _parallel(delegations: List[_ParallelItem]) -> str:
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(item: _ParallelItem) -> str:
            n = _slug(item.specialist)
            meta = _by_name.get(n)
            if not meta:
                return json.dumps({"subagent": n, "task": item.task, "status": "error",
                                   "error": f"unknown specialist '{item.specialist}'"})
            async with sem:
                return await _run_child(
                    llm=llm, sub_tools=meta["sub_tools"], agent_config=agent_config,
                    name=n, role_prompt=meta["role_prompt"], capabilities=meta["capabilities"],
                    output_schema=meta["output_schema"], model_name=meta["model_name"],
                    depth_remaining=depth_remaining, parent_execution_id=parent_execution_id,
                    timeout_s=timeout_s, output_max=output_max,
                    task=item.task, context=await _combine_context(parent_execution_id, item.context),
                    max_turns=meta["max_turns"], permission_mode=meta["permission_mode"],
                )

        results = await asyncio.gather(*[_one(item) for item in delegations], return_exceptions=False)
        parsed = []
        for r in results:
            try:
                parsed.append(json.loads(r))
            except Exception:  # noqa: BLE001
                parsed.append({"raw": r})
        return json.dumps(parsed, default=str)[:output_max * len(delegations)]

    _specialist_listing = "\n".join(
        _format_specialist_line(n, _by_name[n]["description"], _by_name[n]["sub_tools"])
        for n in valid_names
    )
    return StructuredTool.from_function(
        coroutine=_parallel,
        name="delegate_parallel",
        description=(
            "Fan out tasks to multiple specialists concurrently in one call. "
            "Pass a list of {specialist, task, context?} items. All run in "
            "parallel; results are returned together as a JSON array. Use when "
            "tracks are independent.\n\n"
            f"{_DELEGATION_ROUTING_STEER}\n\nSpecialists:\n{_specialist_listing}"
        ),
        args_schema=_ParallelInput,
    )


# ── Batch fan-out (RAH-style: >5 items via a file, not one giant tool call) ──

def build_delegate_batch_tool(
    llm: Any,
    base_tools: List[Any],
    agent_config: Dict[str, Any],
    subagent_defs: List[Dict[str, Any]],
    *,
    parent_execution_id: Optional[str] = None,
    depth_remaining: int = 1,
) -> Optional[Any]:
    """Build ``delegate_batch`` — fan out to MANY specialist tasks at once.

    ``delegate_parallel`` asks the model to type every ``{specialist, task}``
    item directly into one tool call — fine for a handful of items, but
    unwieldy past ~5 (recursive-agent-harness pattern: for fine-grained
    fan-out, write the work items to a file and reference it, rather than
    inlining them all into one call). ``delegate_batch`` instead reads a
    JSON array of ``{specialist?, task, context?}`` objects from a VFS path
    the caller wrote with ``fs_write`` first — factory-instantiated, not
    model-authored code, so this stays within the same closed vocabulary
    ``delegate_parallel`` already offers, just data-driven.

    Each item's result is ALSO written to its own VFS output file
    (``/batch_out/<index>_<specialist>.json``) in addition to being included
    in the tool's own return value, so the parent can aggregate
    deterministically by reading files instead of only re-parsing the
    return string. Returns ``None`` when there are no subagent defs.
    """
    if not subagent_defs or depth_remaining <= 0:
        return None

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    _, max_concurrent, timeout_s, output_max, blocked_patterns = _delegation_bounds(agent_config)
    _by_name = _build_specialist_registry(base_tools, subagent_defs, blocked_patterns, depth_remaining)
    valid_names = list(_by_name.keys())

    class _BatchInput(BaseModel):
        items_ref: str = PydanticField(
            description=(
                "VFS path to a JSON array of {specialist?, task, context?} objects "
                "(write it with fs_write first, e.g. /batch_items.json)."
            )
        )
        template: Optional[str] = PydanticField(
            default=None,
            description=(
                f"Default specialist for items that omit their own 'specialist' field "
                f"(one of: {', '.join(valid_names)})."
            ),
        )

    async def _batch(items_ref: str, template: Optional[str] = None) -> str:
        from app.core.vfs.backend import vfs_read

        try:
            raw = await vfs_read(parent_execution_id, items_ref)
            items = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"failed to read items_ref '{items_ref}': {exc}"})
        if not isinstance(items, list) or not items:
            return json.dumps({"error": "items_ref must contain a non-empty JSON array"})

        default_specialist = _slug(template) if template else None
        sem = asyncio.Semaphore(max_concurrent)

        # Metabolic token economy (4.2, off by default): one ledger per batch
        # call, one private-energy account per specialist branch. Applied
        # between children (no mid-run interruption hook exists) — a branch
        # that's exhausted/stalled from EARLIER items in this same batch is
        # turned over (fresh scout profile) before its next child spawns,
        # rather than blocked outright, since a scout may still find signal.
        from app.harness.budget_ledger import build_budget_ledger
        ledger = build_budget_ledger()

        async def _one(idx: int, item: Any) -> Dict[str, Any]:
            if not isinstance(item, dict):
                return {"index": idx, "status": "error", "error": "item is not an object"}
            n = _slug(item.get("specialist") or default_specialist or "")
            meta = _by_name.get(n)
            if not meta:
                return {"index": idx, "status": "error",
                        "error": f"unknown specialist '{item.get('specialist') or default_specialist}'"}
            task = str(item.get("task") or "").strip()
            if not task:
                return {"index": idx, "specialist": n, "status": "error", "error": "empty task"}
            write_to = f"/batch_out/{idx}_{n}.json"

            turned_over = False
            if ledger.should_turnover(n):
                ledger.turnover(n)
                turned_over = True

            async with sem:
                envelope_str = await _run_child(
                    llm=llm, sub_tools=meta["sub_tools"], agent_config=agent_config,
                    name=n, role_prompt=meta["role_prompt"], capabilities=meta["capabilities"],
                    output_schema=meta["output_schema"], model_name=meta["model_name"],
                    depth_remaining=depth_remaining, parent_execution_id=parent_execution_id,
                    timeout_s=timeout_s, output_max=output_max,
                    task=task, context=await _combine_context(parent_execution_id, item.get("context")),
                    max_turns=meta["max_turns"], permission_mode=meta["permission_mode"],
                )
            await _write_child_output(parent_execution_id, write_to, envelope_str)
            try:
                parsed = json.loads(envelope_str)
            except Exception:  # noqa: BLE001
                parsed = {"raw": envelope_str}

            ledger.spend(n, int(parsed.get("tokens") or 0))
            if parsed.get("status") == "ok":
                ledger.on_success(n)
            else:
                ledger.on_failure(n)

            parsed["index"] = idx
            parsed["output_ref"] = write_to
            if turned_over:
                parsed["turnover"] = True
            return parsed

        results = await asyncio.gather(*[_one(i, item) for i, item in enumerate(items)])
        summary = {
            "count": len(results),
            "ok": sum(1 for r in results if r.get("status") == "ok"),
            "results": results,
            "budget": ledger.snapshot(),
        }
        return json.dumps(summary, default=str)[: output_max * max(1, len(items))]

    _specialist_listing = "\n".join(
        _format_specialist_line(n, _by_name[n]["description"], _by_name[n]["sub_tools"])
        for n in valid_names
    )
    return StructuredTool.from_function(
        coroutine=_batch,
        name="delegate_batch",
        description=(
            "Fan out MANY specialist tasks at once (better than delegate_parallel past "
            "~5 items). Write a JSON array of {specialist?, task, context?} objects to a "
            "virtual file with fs_write, then call this with that path. Each result is "
            "also saved to its own /batch_out/<index>_<specialist>.json file for "
            "deterministic aggregation.\n\n"
            f"{_DELEGATION_ROUTING_STEER}\n\nSpecialists:\n{_specialist_listing}"
        ),
        args_schema=_BatchInput,
    )


# ── Generic ad hoc delegate (replaces the retired subagent.py module) ────────

def build_generic_delegate_tool(
    llm: Any,
    base_tools: List[Any],
    agent_config: Dict[str, Any],
    *,
    parent_execution_id: Optional[str] = None,
    depth_remaining: int = 1,
) -> Optional[Any]:
    """Build ``delegate_investigation`` — an unnamed, depth-1 delegate that hands
    a SCOPED sub-question to a fresh instance of the SAME agent (full current
    toolset, no role override). Complements the named ``delegate_to_<name>``
    specialists: use this for ad hoc sub-questions that don't map to a
    pre-defined specialist role.

    This replaces the standalone ``subagent.py`` module. It now runs on the
    exact same ``_run_child`` engine as every other delegation tool here —
    unified LLM resolution, tool scoping, and result envelope — instead of a
    second implementation with its own wired-port LLM precedence.

    Built whenever code-analyzer tools are configured for the parent (same
    trigger ``subagent.py`` used), independent of any named ``subagents``
    profile list — delegation is useful even for agents with no named
    specialists defined.
    """
    if depth_remaining <= 0:
        return None

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    _, _, timeout_s, output_max, blocked_patterns = _delegation_bounds(agent_config)
    sub_tools, _unmatched = _scope_tools(base_tools, None, blocked_patterns, depth_remaining)

    class _DelegateInput(BaseModel):
        subtask: str = PydanticField(
            description="The focused sub-question to investigate independently.")
        repo: Optional[str] = PydanticField(
            default=None, description="Repository to scope the sub-investigation to.")
        focus: Optional[str] = PydanticField(
            default=None, description="Optional extra focus / hint for the sub-agent.")

    async def _delegate(subtask: str, repo: Optional[str] = None, focus: Optional[str] = None) -> str:
        hints = [h for h in (f"Repository: {repo}" if repo else None,
                             f"Focus: {focus}" if focus else None) if h]
        context = "; ".join(hints) or None
        return await _run_child(
            llm=llm, sub_tools=sub_tools, agent_config=agent_config,
            name="investigation", role_prompt=None, capabilities=[],
            output_schema=None, model_name=None,
            depth_remaining=depth_remaining, parent_execution_id=parent_execution_id,
            timeout_s=timeout_s, output_max=output_max,
            task=subtask, context=await _combine_context(parent_execution_id, context),
        )

    return StructuredTool.from_function(
        coroutine=_delegate,
        name="delegate_investigation",
        description=(
            "Delegate a SCOPED sub-question to an independent sub-agent that runs its own "
            "find/read/trace loop and returns ONLY a concise summary. WHEN TO USE: a broad "
            "question with separable parts (e.g. several services / modules / areas) — delegate "
            "each part so your own context stays focused on synthesis. The sub-agent cannot "
            "delegate further (depth 1). WHEN NOT TO USE: a single direct lookup — just use "
            "codegraph__find_symbol / codegraph__search_semantic yourself."
        ),
        args_schema=_DelegateInput,
    )
