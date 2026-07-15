"""ReAct strategy — pre-flight: resolve config, assemble the action space, and
build the declarative :class:`~app.harness.spec.AgentSpec` for one run.

Split out of the former ``ReactStrategy.execute`` god-orchestrator. This module
owns everything up to (but not including) building the runnable agent /
supervisor loop — that lives in :mod:`executor`. Post-run synthesis floor,
auto-learn, trajectory save, structured output, and PII rehydration live in
:mod:`finalizer`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from app.config import settings
from app.core import privacy
from app.core.privacy.tool_wrap import wrap_tools_with_pseudonymization
from app.core.redact import redact
from app.harness.context_builder import build_recall_query, seed_context_blocks
from app.harness.spec import AgentSpec
from app.harness.spec_factory import build_agent_spec, resolve_profile_fields
from app.harness.tool_assembler import (
    add_extension_tools,
    assemble_base_tools,
    verify_backends_before_llm,
)
from app.workflow.execution_port import ExecutionPort
from app.harness.hitl import make_checkpointer
from app.workflow.strategies.react.llm_factory import build_llm
from app.workflow.strategies.react.streaming import StreamCallback
from app.workflow.strategies.react.workflow_config import (
    connect_agent_mcp_nodes,
    wired_mcp_server_names,
    extract_agent_config,
    extract_cloudwatch_config,
    extract_code_analyzer_config,
    extract_subagents_config,
    extract_tools_config,
    has_memory_node,
    resolve_default_tools_config,
    resolve_llm_config_for_workflow,
)

logger = logging.getLogger(__name__)


def _conversational_skill_names(allowed: Optional[set]) -> List[str]:
    """Invocable skill names (scoped) for the conversational capability prompt.

    Best-effort — a "what can you do?" turn should never fail on skill lookup.
    """
    try:
        from app.config import settings
        if not getattr(settings, "skill_tool_enabled", True):
            return []
        from app.core.skills import get_default_skill_manager
        return [
            s["name"]
            for s in get_default_skill_manager().list_skills()
            if not s.get("disable_model_invocation")
            and (not allowed or s["name"] in allowed)
        ]
    except Exception:  # noqa: BLE001
        return []


@dataclass
class RunPlan:
    """Everything :mod:`executor` needs to build and run the agent, plus
    everything :mod:`finalizer` needs to assemble the post-run result."""

    user_query: str
    augmented_query: str
    agent_config: Dict[str, Any]
    llm_config: Dict[str, Any]
    llm: Any
    tools: List[Any]
    spec: AgentSpec
    checkpointer: Any
    cw_synthesis: Any
    recall_hits: int
    selected_skills: List[str]
    code_analyzer_config: Dict[str, Any]
    conversation_history: Optional[List[Dict[str, Any]]] = None
    # Skills actually invoked this run — via a user /slash-command (recorded at
    # build time) and/or the model's ``skill`` tool (appended live during the
    # run through the tool's mutable sink). Deduped into ``selected_skills`` for
    # the UI badge by the finalizer.
    invoked_skills: List[str] = field(default_factory=list)


@dataclass
class EarlyReturn:
    """A pre-flight short-circuit (conversational fast-path, expired creds)
    that already IS the final result dict — no agent run needed."""

    result: Dict[str, Any]


async def build_run_plan(
    workflow: Dict[str, Any],
    context: Dict[str, Any],
    *,
    execution_id: Optional[str],
    logger_instance: Any,
    user_query: str,
    mcp_manager: Any,
    stream_callback: Optional[StreamCallback],
    execution_port: Optional[ExecutionPort],
) -> Union[RunPlan, EarlyReturn]:
    agent_config = extract_agent_config(workflow)

    # Merge specialist subagents from any connected ``subagents`` node into
    # agent_config so spec_factory._coerce_subagents picks them up.  Node-level
    # subagents (already in agent_config) take precedence; wired node definitions
    # are appended for names not already defined.
    _wired_subs = extract_subagents_config(workflow)
    if _wired_subs:
        import json as _json
        _existing_subs = agent_config.get("subagents") or []
        if isinstance(_existing_subs, str):
            try:
                _existing_subs = _json.loads(_existing_subs) or []
            except Exception:  # noqa: BLE001
                _existing_subs = []
        _existing_names = {s.get("name") for s in _existing_subs if isinstance(s, dict)}
        _merged_subs = list(_existing_subs) + [
            s for s in _wired_subs if s.get("name") not in _existing_names
        ]
        agent_config = {**agent_config, "subagents": _merged_subs}

    # Fold a referenced agent profile (if any) into the node config so the
    # spec picks up its role prompt / output schema / policies / deep
    # features. Node config always wins; no `profile` = unchanged. Best
    # effort — a profile lookup never breaks a run.
    try:
        from app.infrastructure.persistence.agent_profile_repository import (
            agent_profile_repository,
        )
        agent_config = await agent_profile_repository.merge_into_agent_config(
            agent_config
        )
    except Exception as _prof_err:  # noqa: BLE001
        logger.warning("ReactStrategy: profile merge skipped (%s)", _prof_err)

    # Per-agent skill scoping (Skills picker on the Agent node). Empty = no
    # scoping; every invocable skill is listed/available (backward-compatible).
    try:
        _agent_skills = resolve_profile_fields(agent_config).get("skills") or []
    except Exception:  # noqa: BLE001 — scoping must never block a run
        _agent_skills = []
    _allowed_skills = set(_agent_skills) or None

    # Skills invoked this run: a user /slash-command (recorded here) and/or the
    # model's ``skill`` tool (appended live via the tool's mutable sink below).
    _invoked_skills: List[str] = []
    _skill_slash_matched = False

    # Deterministic slash-command expansion: a user message that starts with
    # "/<skill-name> [args]" is expanded into that skill's runbook up front —
    # BEFORE the conversational fast-path, since an explicit slash command is a
    # request, never small talk. No-op when disabled or nothing matches.
    if getattr(settings, "skill_slash_commands_enabled", True):
        try:
            from app.harness.skill_tools import expand_slash_command
            _slash = expand_slash_command(user_query, _allowed_skills)
            if _slash:
                user_query, _slash_name = _slash
                _invoked_skills.append(_slash_name)
                _skill_slash_matched = True
                logger_instance.info(
                    "ReactStrategy: expanded /%s slash-command into its runbook",
                    _slash_name, extra={"execution_id": execution_id},
                )
        except Exception as _slash_err:  # noqa: BLE001 — never block a run
            logger_instance.warning(
                "ReactStrategy: slash-command expansion skipped (%s)",
                redact(str(_slash_err)), extra={"execution_id": execution_id},
            )

    llm_config = await resolve_llm_config_for_workflow(workflow)

    # Tag the stream callback with the main agent's resolved model so the chat
    # can show which model ran each tool call (delegated subagents override this
    # per-event with their own model). Best-effort — never breaks a run.
    if stream_callback is not None:
        try:
            setattr(stream_callback, "model_name", llm_config.get("model") or "")
        except Exception:  # noqa: BLE001
            pass

    tools_config = extract_tools_config(workflow)
    if tools_config:
        logger.info(
            "ReactStrategy: using %d workflow-wired MCP tool node(s)",
            len(tools_config),
        )
    else:
        # Gateway: no tool nodes wired — fall back to the agent's default
        # MCP servers. Wired tool nodes always win over these defaults.
        tools_config = await resolve_default_tools_config()
        logger.info(
            "ReactStrategy: no wired tool nodes — using %d gateway-default MCP server(s)",
            len(tools_config),
        )
    cloudwatch_config = extract_cloudwatch_config(workflow)
    code_analyzer_config = extract_code_analyzer_config(workflow)

    # ── Conversational fast-path ──────────────────────────────────────
    # A purely conversational turn (greeting / thanks / "what can you do?"
    # / "what tools are available?") needs no logs, code search, or DB. The
    # full agent build would still hand the model the entire investigation
    # system prompt + every bound tool schema (~28K input tokens for a
    # one-word "Hi"). Short-circuit with a single cheap, tool-less model
    # call against a tiny capability-aware prompt. Any failure falls through
    # to the full path so a real query is never dropped.
    from app.core.intent import is_conversational
    if is_conversational(user_query) and not _skill_slash_matched:
        try:
            from app.harness.conversational import conversational_reply
            privacy.bind_session(execution_id)
            _conv = await conversational_reply(
                llm=build_llm(llm_config),
                user_query=user_query,
                llm_config=llm_config,
                has_cloudwatch=bool(cloudwatch_config),
                has_code_analyzer=bool(code_analyzer_config),
                has_db=bool(context.get("db_server_map")),
                skill_names=_conversational_skill_names(_allowed_skills),
                stream_callback=stream_callback,
                execution_id=execution_id,
                logger_instance=logger_instance,
            )
            if mcp_manager:
                try:
                    await mcp_manager.disconnect_all()
                except Exception:  # noqa: BLE001
                    pass
            privacy.drop_vault(execution_id)
            return EarlyReturn(_conv)
        except Exception as _conv_err:  # noqa: BLE001 — use the full path
            logger_instance.warning(
                "ReactStrategy: conversational fast-path failed (%s) — "
                "using full path", redact(str(_conv_err)),
                extra={"execution_id": execution_id},
            )
            try:
                privacy.drop_vault(execution_id)
            except Exception:  # noqa: BLE001
                pass

    # Tool-inclusive chat history replay: a follow-up turn's history
    # normally carries only user/assistant TEXT (see build_initial_messages),
    # so a follow-up re-runs every tool from scratch. When this run is
    # chat-triggered, rebuild the session's history from prior turns'
    # persisted trajectories (tool_calls + tool_results included) instead of
    # the UI's text-only ``history``. Fails open: any error or a non-chat
    # run leaves ``conversation_history`` None, and the caller (executor.py)
    # falls back to ``context.inputs.history`` exactly as today.
    _chat_session_id = (context.get("inputs") or {}).get("_chat_session_id")
    conversation_history: Optional[List[Dict[str, Any]]] = None
    if _chat_session_id:
        from app.harness.chat_history import rebuild_chat_history
        conversation_history = await rebuild_chat_history(
            _chat_session_id,
            current_user_query=user_query,
            logger_instance=logger_instance,
        )

    # Expand @file / @folder / @url / @git references in the query into
    # inline context (best-effort, no-op when none present). File access
    # is restricted to the configured root for safety.
    if getattr(settings, "context_references_enabled", True) and "@" in (user_query or ""):
        try:
            import os as _os
            from app.core.context_references import preprocess_context_references_async
            _ref_root = getattr(settings, "context_reference_root", "") or _os.getcwd()
            _ref_res = await preprocess_context_references_async(
                user_query,
                cwd=_ref_root,
                context_length=200_000,
                allowed_root=_ref_root,
            )
            if _ref_res.expanded:
                user_query = _ref_res.message
                logger_instance.info(
                    "ReactStrategy: expanded %d context reference(s) (%d tokens)",
                    len(_ref_res.references), _ref_res.injected_tokens,
                    extra={"execution_id": execution_id},
                )
            for _w in _ref_res.warnings:
                logger_instance.warning("ReactStrategy: context-ref: %s", _w)
        except Exception as _ref_err:  # noqa: BLE001 — never block on references
            logger_instance.warning(
                "ReactStrategy: context-reference expansion skipped (%s)",
                redact(str(_ref_err)), extra={"execution_id": execution_id},
            )

    # Prepend a knowledge-base recall block (similar past issues / patterns) and
    # the per-turn skill listing so the agent starts with institutional memory
    # and knows which skills it can load. Skill scoping (``_allowed_skills``) was
    # resolved above alongside slash-command expansion.
    _has_history = bool(conversation_history or (context.get("inputs") or {}).get("history"))
    augmented_query, recall_hits, selected_skills = await build_recall_query(
        user_query=user_query,
        cloudwatch_config=cloudwatch_config,
        logger_instance=logger_instance,
        execution_id=execution_id,
        code_analyzer_config=code_analyzer_config,
        memory_enabled=has_memory_node(workflow),
        allowed_skills=list(_allowed_skills) if _allowed_skills else None,
        has_history=_has_history,
    )

    # MCP barrier: connect every agent-reachable ``mcp_server`` node (including
    # those contained in a ``subagent_window``, which the edge-ordered scheduler
    # runs concurrently with the agent) BEFORE we snapshot the tool set below.
    # Without this, a slow-cold-starting server like CloudWatch loses the race
    # and its tools are silently absent — subagent globs (``cloudwatch__*``)
    # match nothing and the capability vanishes for the turn. Failures come back
    # as server names so we can tell the agent what's missing instead of letting
    # it discover the gap by claiming it has no such tool.
    _mcp_barrier_failed: List[str] = []
    if mcp_manager:
        try:
            _mcp_barrier_failed = await connect_agent_mcp_nodes(
                workflow, mcp_manager, execution_id=execution_id,
            )
        except Exception as _bar_err:  # noqa: BLE001 — never block a run on the barrier
            logger_instance.warning(
                "ReactStrategy: MCP pre-connect barrier failed (non-fatal): %s",
                redact(str(_bar_err)), extra={"execution_id": execution_id},
            )

    # Assemble the base action space (MCP + CloudWatch + crawler + DB
    # schema tools, pruned by the relevance router) via the harness.
    # Every builder degrades independently on failure (expired creds,
    # dead connection, ...) rather than aborting the turn — see
    # tool_assembler.py's module docstring. `degraded` lists what
    # couldn't be built this turn; `_expired_creds_msg` is set only
    # when NOTHING could be built or the model's own credentials are
    # confirmed dead.
    tools, _expired_creds_msg, _degraded = await assemble_base_tools(
        tools_config=tools_config,
        mcp_manager=mcp_manager,
        execution_id=execution_id,
        cloudwatch_config=cloudwatch_config,
        code_analyzer_config=code_analyzer_config,
        db_server_map=context.get("db_server_map") or {},
        user_query=user_query,
        logger_instance=logger_instance,
        llm_config=llm_config,
    )

    # Hard credential/connection gate — runs BEFORE any LLM call. Directive:
    # never invoke the model when a wired backend can't be verified. Unlike the
    # graceful-degrade path in assemble_base_tools (which continues with whatever
    # built), this aborts with a deterministic message so the model is never
    # asked to work against — or narrate a failure of — an unverified backend
    # (which is how an expired AWS session produced a hallucinated "refresh your
    # creds" essay instead of a clean, cheap short-circuit). Reuses the
    # _expired_creds_msg short-circuit envelope below.
    if not _expired_creds_msg:
        _expired_creds_msg = await verify_backends_before_llm(
            wired_mcp_servers=wired_mcp_server_names(workflow),
            cloudwatch_config=cloudwatch_config,
            mcp_barrier_failed=_mcp_barrier_failed,
            execution_id=execution_id,
            logger_instance=logger_instance,
        )

    if _expired_creds_msg:
        return EarlyReturn({
            "type": "react",
            "user_query": user_query,
            "final_answer": _expired_creds_msg,
            "messages": [],
            "message_count": 0,
            "tool_calls": [],
            "model": "none",
            "provider": "none",
            "supervisor_escalated": False,
            "supervisor_reason": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        })

    # Fail loud on a genuinely-dead MCP server: with the barrier above, a
    # reachable server that STILL isn't connected here failed for real (TLS,
    # expired creds, crashed subprocess) — not a cold-start race. Fold it into
    # the degrade notice so the agent says so explicitly rather than shipping an
    # answer that pretends the capability was never wired.
    if _mcp_barrier_failed:
        _degraded = list(_degraded or []) + [
            "The following MCP server(s) could not be reached this turn and their "
            "tools are unavailable: " + ", ".join(sorted(set(_mcp_barrier_failed))) + "."
        ]

    if _degraded:
        # Tell the agent what's missing and why, up front — instead of
        # letting it discover a gap by calling a tool that was never
        # bound (or, worse, one that silently isn't there at all).
        augmented_query = (
            "[System notice] " + " ".join(_degraded)
            + " Use the tools that ARE available; if the question genuinely "
            "requires something unavailable, say so explicitly instead of "
            "guessing.\n\n" + augmented_query
        )

    # Prepend any pre-computed analysis blocks the executor seeded
    # (CloudWatch synthesis, code analysis, anomaly↔code correlation).
    # cw_synthesis is surfaced as a guaranteed answer floor (used by the
    # synthesis-as-floor fallback after the agent loop).
    augmented_query, cw_synthesis = seed_context_blocks(
        augmented_query=augmented_query,
        context=context,
    )

    # ── PII pseudonymization (privacy boundary before Bedrock) ───────
    # Bind this run's vault, then swap PII in everything bound for the
    # model (query + all seeded context) for stable placeholders. Tool
    # output produced inside the loop is scrubbed via the same vault
    # (wrap below + MCP sanitize choke point). The final answer is
    # re-hydrated before return. No-op when the feature is disabled.
    privacy.bind_session(execution_id)
    augmented_query = privacy.pseudonymize(augmented_query, execution_id)

    llm = build_llm(llm_config)

    # Bind the model-invoked ``skill`` tool (stage two of skill disclosure).
    # Added BEFORE add_extension_tools so subagent snapshots inherit it and
    # pseudonymization wraps it; it is pinned in tool_disclosure so it is never
    # deferred. The mutable ``_invoked_skills`` sink records which skills the
    # model actually loads, for the post-run UI badge.
    if getattr(settings, "skill_tool_enabled", True):
        try:
            from app.harness.skill_tools import build_skill_tool
            tools = list(tools) + [build_skill_tool(
                allowed_skills=_allowed_skills,
                execution_id=execution_id,
                logger_instance=logger_instance,
                invoked_sink=_invoked_skills,
            )]
        except Exception as _skill_tool_err:  # noqa: BLE001 — never block a run
            logger_instance.warning(
                "ReactStrategy: skill tool binding skipped (%s)",
                redact(str(_skill_tool_err)), extra={"execution_id": execution_id},
            )

    # LLM-dependent extension tools (depth-1 delegate + gated edit),
    # added via the harness so it owns the complete action space.
    tools = add_extension_tools(
        tools=tools,
        llm=llm,
        agent_config=agent_config,
        code_analyzer_config=code_analyzer_config,
        execution_id=execution_id,
        logger_instance=logger_instance,
    )
    # Metamemory: hydrate this execution's VFS from the chat session's
    # persisted metamemory (opt-in, postgres-backend only — a no-op
    # otherwise), then seed /plan.txt, /milestones.txt, /context_summary.txt
    # if still absent (add_extension_tools already bound the VFS session
    # above when the filesystem profile flag is on). Idempotent — a
    # hydrated or resumed session with existing files is left untouched.
    # Off by default; requires both the global flag and the profile's
    # filesystem flag.
    try:
        from app.harness import metamemory as _metamemory
        from app.harness.spec_factory import resolve_profile_fields as _resolve_profile_fields
        _mm_flags = _resolve_profile_fields(agent_config)
        if _metamemory.is_active(bool(_mm_flags.get("filesystem"))):
            await _metamemory.sync_session_persistence_in(execution_id, _chat_session_id)
            await _metamemory.seed_if_absent(execution_id, user_query)
    except Exception as _mm_err:  # noqa: BLE001 — metamemory must never break a run
        logger_instance.warning("ReactStrategy: metamemory seed skipped (%s)", _mm_err)
    # Pseudonymize coroutine-tool output before it re-enters the LLM (MCP
    # tools are scrubbed at their own choke point). No-op when disabled.
    tools = wrap_tools_with_pseudonymization(tools)
    checkpointer = await make_checkpointer()

    # Declarative agent spec — single source of truth for building the
    # agent (initial build + supervisor-retry rebuild go through it).
    spec = build_agent_spec(
        agent_config=agent_config,
        context=context,
        has_cloudwatch=bool(cloudwatch_config),
        has_code_analyzer=bool(code_analyzer_config),
        session_id=execution_id,
        has_memory=has_memory_node(workflow),
        logger_instance=logger_instance,
    )

    # Expand any named-policy-set references (DB-backed) into inline
    # entries before the agent is built. No-op when policies are inline
    # or absent. Best-effort — never fails the run.
    try:
        from app.core import policy as _policy
        spec.policies = await _policy.expand_policy_refs(spec.policies)
    except Exception as _pol_exc:  # noqa: BLE001
        logger_instance.warning(
            "ReactStrategy: policy-set expansion skipped (%s)", _pol_exc
        )

    return RunPlan(
        user_query=user_query,
        augmented_query=augmented_query,
        agent_config=agent_config,
        llm_config=llm_config,
        llm=llm,
        tools=tools,
        spec=spec,
        checkpointer=checkpointer,
        cw_synthesis=cw_synthesis,
        recall_hits=recall_hits,
        selected_skills=selected_skills,
        code_analyzer_config=code_analyzer_config,
        conversation_history=conversation_history,
        invoked_skills=_invoked_skills,
    )


__all__ = ["RunPlan", "EarlyReturn", "build_run_plan"]
