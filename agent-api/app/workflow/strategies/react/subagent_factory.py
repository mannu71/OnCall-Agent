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

LLM resolution (single precedence chain for every delegation tool):
  1. per-def ``model`` (a DB-registered llm_config name) — or the literal
     sentinel ``"inherit"`` to explicitly reuse the parent's LLM instance.
  2. the global ``subagent`` model-role assignment.
  3. the parent's LLM instance (implicit fallback, also what ``"inherit"`` picks).

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
  * ``DELEGATION_CHILD_TIMEOUT_SECONDS`` (default 180.0) — per-child wall-clock cap.
  * ``DELEGATION_OUTPUT_MAX_CHARS`` (default 8000) — output envelope cap.
  * ``DELEGATION_BLOCKED_TOOLS`` (CSV fnmatch) — always stripped from children,
    even if an allow-list or a def's ``disallowedTools`` would have kept them.

A subagent definition dict::

    {"name": "code-specialist",          # required; → delegate_to_code_specialist
     "description": "…",                 # tool description
     "role_prompt": "You are …",         # role-sentence override
     "capabilities": ["code_analyzer"],  # extra capability ids
     "tools": ["crawler_*", "fs_*"],     # optional fnmatch allow-list; omit or ["*"] = all
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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
    timeout_s = float(getattr(s, "delegation_child_timeout_seconds", 180.0) if s else 180.0)
    output_max = int(getattr(s, "delegation_output_max_chars", 8000) if s else 8000)
    blocked_csv = getattr(s, "delegation_blocked_tools",
                          "delegate_*,apply_fix,edit_file,fs_write*,run_command,write_todos,send_*,wiki_*") if s else ""
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
       "every spawnable tool" — mirrors claude-code-main's ``resolveAgentTools``
       wildcard semantics (``tools`` undefined or ``['*']`` = all).
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


def _infer_child_context_flags(sub_tools: List[Any]) -> Tuple[bool, bool]:
    """Infer (has_code_analyzer, has_cloudwatch) for a child from its ACTUAL
    scoped tool set, so a named specialist scoped to ``crawler_*`` / ``cloudwatch_*``
    gets the same code/CloudWatch-aware system-prompt guidance a top-level agent
    with those tools would (agent_builder.py / conversational.py branch on these).
    """
    names = [_tool_name(t) for t in sub_tools]
    has_code_analyzer = any(n.startswith("crawler_") for n in names)
    has_cloudwatch = any(n.startswith("cloudwatch_") for n in names)
    return has_code_analyzer, has_cloudwatch


def _extract_fallback_answer(result: Dict[str, Any]) -> str:
    """Scan serialized child messages for the last non-empty assistant text.

    ``execute_agent``'s own serializer already backfills ``final_answer`` from
    the last AIMessage content when no terminal (tool-call-free) message
    produced one. This is one more defensive layer for the rare case where
    every assistant turn in the child's history was tool-calls-only with no
    text at all (e.g. the child hit its recursion limit before ever
    synthesizing) — mirrors claude-code-main's ``extractPartialResult`` fallback.
    """
    for entry in reversed(result.get("messages") or []):
        if entry.get("role") == "assistant" and entry.get("content"):
            return str(entry["content"]).strip()
    return ""


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
    from app.workflow.strategies.react.hitl import make_checkpointer

    q = (task or "").strip()
    if not q:
        return json.dumps({"subagent": name, "task": task, "error": "empty task"})
    if context:
        q += f"\n(Context: {context})"

    sub_id = f"{parent_execution_id or 'sub'}-{name}-{uuid.uuid4().hex[:6]}"
    logger.info("delegation: spawning child %s (depth_remaining=%d)", sub_id, depth_remaining)
    t0 = time.monotonic()

    # ── Child LLM resolution (single precedence chain for every delegation
    #    tool: per-def model → "inherit" sentinel → subagent role → parent) ──
    sub_llm = llm
    inherit = isinstance(model_name, str) and model_name.strip().lower() == "inherit"
    try:
        from app.workflow.strategies.react.llm_factory import build_llm
        if model_name and not inherit:
            from app.workflow.llm_config import resolve_llm_config_by_name
            sub_cfg = await resolve_llm_config_by_name(model_name)
            sub_llm = build_llm(sub_cfg)
            logger.info("delegation: child %s using per-def model=%s", sub_id, model_name)
        elif inherit:
            logger.info("delegation: child %s explicitly inheriting parent LLM", sub_id)
        else:
            from app.infrastructure.persistence import model_role_repository
            role_name = await model_role_repository.get("subagent")
            if role_name:
                from app.workflow.llm_config import resolve_llm_config_for_role
                sub_cfg = await resolve_llm_config_for_role("subagent")
                sub_llm = build_llm(sub_cfg)
                logger.info("delegation: child %s using role model=%s", sub_id, role_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delegation: child %s LLM resolution failed (%s) — using parent LLM", sub_id, exc)
        sub_llm = llm

    try:
        cp = await make_checkpointer()
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

        child_engine = resolve_engine(sub_agent_config)
        if timeout_s > 0:
            result = await asyncio.wait_for(
                run_agent_once(
                    sub_spec, sub_llm, sub_tools, q,
                    logger_instance=logger, execution_id=sub_id, thread_id=sub_id,
                    recursion_limit=recursion_limit, checkpointer=cp, engine=child_engine,
                ),
                timeout=timeout_s,
            )
        else:
            result = await run_agent_once(
                sub_spec, sub_llm, sub_tools, q,
                logger_instance=logger, execution_id=sub_id, thread_id=sub_id,
                recursion_limit=recursion_limit, checkpointer=cp, engine=child_engine,
            )

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
            },
            default=str,
        )
        return envelope[:output_max]
    except asyncio.TimeoutError:
        logger.warning("delegation: child %s timed out after %.0fs", sub_id, timeout_s)
        return json.dumps({"subagent": name, "task": task, "status": "timeout",
                           "error": f"child timed out after {timeout_s:.0f}s"})
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
        description = _def.get("description") or (
            f"Delegate a scoped task to the '{name}' subagent, which runs its own loop "
            f"and returns a concise summary. Use for a separable part of a larger task."
        )

        sub_tools, unmatched = _scope_tools(
            base_tools, tool_globs, blocked_patterns, depth_remaining,
            disallowed_globs=disallowed_globs,
        )
        if unmatched:
            logger.warning(
                "subagent def '%s': tool glob(s) matched nothing: %s", name, unmatched,
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
            task=task, context=context,
            max_turns=max_turns, permission_mode=permission_mode,
        )

    return StructuredTool.from_function(
        coroutine=_delegate,
        name=f"delegate_to_{name}",
        description=description,
        args_schema=_SubInput,
    )


# ── Parallel fan-out tool ─────────────────────────────────────────────────────

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

    # Pre-build per-name tool sets and meta.
    _by_name: Dict[str, Dict[str, Any]] = {}
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
        _by_name[n] = {
            "role_prompt": _def.get("role_prompt") or None,
            "capabilities": _def.get("capabilities") or [],
            "output_schema": _def.get("output_schema") or None,
            "model_name": _def.get("model") or None,
            "max_turns": _def.get("max_turns"),
            "permission_mode": _def.get("permission_mode") or "auto_allow",
            "sub_tools": _sub_tools,
        }

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
                    task=item.task, context=item.context,
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

    return StructuredTool.from_function(
        coroutine=_parallel,
        name="delegate_parallel",
        description=(
            "Fan out tasks to multiple specialists concurrently in one call. "
            f"Pass a list of {{specialist, task, context?}} items (specialists: "
            f"{', '.join(valid_names)}). All run in parallel; results are returned "
            "together as a JSON array. Use when tracks are independent."
        ),
        args_schema=_ParallelInput,
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
            task=subtask, context=context,
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
            "crawler_find_symbol / crawler_search_semantic yourself."
        ),
        args_schema=_DelegateInput,
    )
