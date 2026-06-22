"""LangGraph ReAct agent construction."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.workflow.strategies.react.tool_setup import build_playbook_tools

logger = logging.getLogger(__name__)

def build_agent(
    llm: Any,
    tools: List[Any],
    agent_config: Dict[str, Any],
    has_cloudwatch: bool = False,
    has_code_analyzer: bool = False,
    checkpointer: Any = None,
    session_id: Optional[str] = None,
    permission_mode: str = "default",
    execution_port: Any = None,
    policies: Optional[List[Dict[str, Any]]] = None,
    capabilities: Optional[List[str]] = None,
    role_prompt: Optional[str] = None,
    planning: bool = False,
    filesystem: bool = False,
    subagents: Optional[List[Dict[str, Any]]] = None,
    sandbox: bool = False,
) -> Any:
    """
    Build a LangGraph ReAct agent graph.

    Uses langgraph.prebuilt.create_react_agent which implements the full
    Thought → Action → Observation loop as a compiled StateGraph.

    Args:
        llm: LangChain chat model instance.
        tools: List of LangChain BaseTool instances.
        agent_config: Agent node data with optional system prompt / instructions.
        has_cloudwatch: If True, add CloudWatch-specific instructions.
        has_code_analyzer: If True, add code-analysis investigation instructions.

    Returns:
        Compiled LangGraph agent (CompiledGraph).
    """
    from langgraph.prebuilt import create_react_agent

    # Build system prompt from agent instructions if provided
    instructions = agent_config.get("instructions", "")
    agent_mode = agent_config.get("agentMode", "single")

    # Determine which capability groups are actually present so the role
    # sentence and instructions accurately reflect what the agent can do.
    _cw_tool_prefix = "cloudwatch_"
    _code_tool_prefix = "code_"
    _playbook_tool_names = {"save_playbook", "execute_skill"}
    has_db_tools = any(
        not t.name.startswith(_cw_tool_prefix)
        and not t.name.startswith(_code_tool_prefix)
        and t.name not in _playbook_tool_names
        for t in tools
        if hasattr(t, "name")
    )

    # ── Resolve active capabilities (composable; see app.harness.capabilities) ──
    # The investigation trio is derived from the runtime flags so existing
    # workflows are unchanged; a profile may declare extra capability ids via
    # ``capabilities`` (Phase 1), which append after the builtins in registry order.
    from app.harness import capabilities as _caps
    _active_ids: List[str] = []
    if has_db_tools:
        _active_ids.append("database")
    if has_cloudwatch:
        _active_ids.append("cloudwatch")
    if has_code_analyzer:
        _active_ids.append("code_analyzer")
    for _cid in (capabilities or []):
        if _cid not in _active_ids:
            _active_ids.append(_cid)
    active_caps = _caps.resolve(_active_ids)

    # ── Role sentence ──────────────────────────────────────────────────
    if role_prompt:
        # Profile-supplied full role override (Phase 1).
        role_sentence = role_prompt
    else:
        role_fragments = [c.role_fragment for c in active_caps if c.role_fragment]
        if role_fragments:
            capability_str = ", ".join(role_fragments)
            role_sentence = (
                f"You are an expert engineering assistant "
                f"with access to {capability_str}. Adapt to whatever the user is "
                f"trying to do — root-cause analysis, log retrieval, code inspection, "
                f"code analysis, or general questions."
            )
        else:
            role_sentence = "You are an expert engineering assistant."

    # ── System prompt assembly ─────────────────────────────────────────
    # CACHE CONTRACT: this prompt MUST stay deterministic given agent_config.
    # Never inject recalled memory, retry guidance, timestamps, or any other
    # run-specific data here — those belong in the query (see strategy.py
    # `augmented_query`). The composed string is the Bedrock cachePoint prefix
    # (system + tools), so any per-run variation busts the prompt cache on
    # every model call. Keep the section order and tool ordering stable too.
    system_parts = [role_sentence]

    # § Doing tasks — shared discipline that governs every capability below.
    system_parts.append(
        "# Doing tasks\n"
        "- Reason step by step and call the available tools to get facts — never guess "
        "or invent data.\n"
        "- The ONLY exception: if the user's message is purely a greeting, a thank-you, or "
        "other small talk with nothing to investigate, just reply conversationally without "
        "calling tools. Any actual question or task still uses the tools.\n"
        "- Answer exactly what was asked and back every claim with specific evidence from the "
        "tool results (repo/file/line, log_group/timestamp, table/column). Don't gold-plate or "
        "pad with unrequested analysis.\n"
        "- Stop as soon as the evidence supports a conclusion — every tool call has cost and "
        "latency, so don't keep digging once you can answer.\n"
        "- NEVER end a turn by announcing a next step (e.g. 'Let me search…', 'Now I'll check…'). "
        "If you say you will do something, call the tool in the SAME turn. If a focused search "
        "finds nothing, say so plainly rather than inventing an answer or trailing off."
    )

    # § Plan → execute → verify. Multi-step only, so trivial
    # single-tool runs are not bloated. Gated on a stable config flag + agent_mode
    # so the cache prefix stays deterministic per run (re-baseline evals on change).
    try:
        from app.config import settings as _settings
        _planning_on = bool(getattr(_settings, "agent_planning_enabled", True))
    except Exception:  # noqa: BLE001
        _planning_on = True
    if _planning_on and agent_mode == "multi":
        system_parts.append(
            "# Plan, then execute, then verify\n"
            "- For a multi-step investigation, FIRST write a short markdown task list "
            "(GitHub checkboxes: '- [ ] step') of the concrete steps you intend to take.\n"
            "- Then work the list top to bottom, calling tools as you go. Keep it honest: "
            "only check an item ('- [x]') once the tool evidence actually supports it.\n"
            "- Close with a brief '## Verification' section confirming each item was done and "
            "citing the evidence. Skip the plan for a trivial single-lookup question — it is a "
            "tool for genuinely multi-step work, not ceremony."
        )

    # § Using your tools — shared steering: prefer the specific tool, reuse work.
    system_parts.append(
        "# Using your tools\n"
        "- Prefer the most specific tool over a generic one, and fetch only what the question "
        "needs — never read whole files or dump an entire schema when a targeted lookup will do.\n"
        "- When you reach a useful conclusion or resolution worth reusing, call save_playbook to "
        "record it so future investigations can benefit from it.\n"
        "- If the memory-context block at the start of the query lists 'Executable Skill' entries "
        "that match the current issue, prefer calling execute_skill with the skill's name before "
        "running manual tool calls — this reuses proven remediation steps and is faster."
    )

    # ── Capability sections (registry-driven; stable order) ──────────────
    # Each active capability contributes its system-prompt section in registry
    # order (database → cloudwatch → code_analyzer → any profile extras). The
    # text lives in app.harness.capabilities; this loop reproduces the previous
    # inline ordering exactly so the Bedrock cache prefix is unchanged.
    for _cap in active_caps:
        if _cap.section:
            system_parts.append(_cap.section)

    # ── Deep-agent capability instructions (profile-gated, config-stable) ──────
    if planning:
        system_parts.append(
            "# Planning\n"
            "You have write_todos and update_todo. For a genuinely multi-step task, call "
            "write_todos FIRST with the concrete steps, then work the list top to bottom, "
            "calling update_todo to mark each item in_progress then completed as the tool "
            "evidence supports it. Skip the plan for a single-lookup question."
        )
    # Scratch filesystem is always available (no toggle). Keep this section
    # unconditional so the prompt matches the always-present fs_* tools.
    system_parts.append(
        "# Scratch filesystem\n"
        "You have a session-scoped virtual filesystem (fs_write, fs_read, fs_ls, fs_grep). "
        "When a tool returns a large result you only partly need, fs_write it to a file and "
        "keep working from a short note, then fs_read/fs_grep just the part you need later. "
        "This keeps your context lean. The files vanish when the run ends."
    )
    if sandbox:
        system_parts.append(
            "# Sandboxed shell\n"
            "You have run_command, which executes a shell command inside an isolated "
            "sandbox (no network by default; writes confined to a scratch working "
            "directory). Use it for safe, self-contained commands; it returns "
            "stdout/stderr/exit_code. It requires operator approval before each run."
        )
    if subagents:
        _names = ", ".join(
            f"delegate_to_{re.sub(r'[^a-z0-9]+', '_', str(s.get('name', '')).lower()).strip('_')}"
            for s in subagents if isinstance(s, dict) and s.get("name")
        )
        if _names:
            system_parts.append(
                "# Delegation\n"
                f"You can delegate scoped subtasks to specialized subagents ({_names}). Each runs "
                "its own loop with a fresh context and returns only a concise summary. Use them "
                "for separable parts of a larger task so your own context stays focused on "
                "synthesis; they cannot delegate further."
            )

    if instructions:
        system_parts.append(f"\nAdditional instructions:\n{instructions}")

    if agent_mode == "multi":
        system_parts.append(
            "\nYou are coordinating multiple sub-tasks. "
            "Break down the investigation into logical steps and address each systematically."
        )

    system_prompt = "\n".join(system_parts)

    # Add agent-writable playbook tools so the agent can persist resolutions.
    playbook_tools = build_playbook_tools()
    all_tools = list(tools) + playbook_tools

    # ── Governance: declarative policy engine (single evaluation point) ───
    # Resolve the workflow's policy set (or platform defaults when none) into a
    # ResolvedPolicy, then apply tool gating + the universal output cap in one
    # pass. Permission gate runs first so the model-facing schema (and the
    # prompt-cache prefix) is preserved; read-only tools pass through unchanged.
    # The resolved policy is stashed on a per-task contextvar so the runner can
    # honour runtime quotas (cost ceiling, tool-call limit, loop guardrails).
    try:
        from app.core import policy as _policy
        resolved = _policy.resolve_with_platform_defaults(policies)
        _policy.set_current(resolved)
        all_tools = _policy.apply_to_tools(
            all_tools, resolved,
            mode=permission_mode,
            execution_id=session_id, execution_port=execution_port,
        )
        logger.info(
            "ReactStrategy: policy engine active (mode=%s, ask=%d, deny=%d, cap=%s, "
            "cost_ceiling=%s, max_tool_calls=%s)",
            permission_mode, len(resolved.effective_ask_patterns()),
            len(resolved.deny_patterns), resolved.output_cap_chars,
            resolved.budget_cost_usd, resolved.max_tool_calls,
        )
    except Exception as _pol_err:  # noqa: BLE001 — never break a run on governance
        logger.warning("ReactStrategy: policy engine skipped (%s)", _pol_err)

    # ── Prompt caching (provider-aware) ───────────────────────────────
    # AWS Bedrock (ChatBedrockConverse) is the only live provider. It does
    # NOT honour Anthropic content-block ``cache_control`` — that mechanism
    # is silently ignored. Native Bedrock prompt caching is enabled by
    # passing ``cache_control`` as a *bound invocation kwarg*; the model
    # then auto-inserts ``cachePoint`` markers after the system prompt, the
    # tools array, and the last message, giving rolling prefix caching
    # across ReAct iterations (cached input bills at a fraction of base
    # rate). The ChatAnthropic branch below is retained for completeness but
    # is not exercised by this deployment.
    _provider_name = type(llm).__name__
    _is_bedrock = "Bedrock" in _provider_name
    _is_anthropic = "Anthropic" in _provider_name

    model_for_agent: Any = llm
    prompt_arg: Any = system_prompt
    pre_model_hook: Any = None

    if _is_bedrock:
        # Bind the tools so the ``cache_control`` kwarg survives, then pass
        # the same ``all_tools`` to create_react_agent. create_react_agent
        # detects the tools are already bound (matching) and skips
        # re-binding, preserving our kwarg on every model invocation.
        model_for_agent = llm.bind_tools(all_tools).bind(
            cache_control={"ttl": "1h"}
        )
        logger.info(
            "ReactStrategy: Bedrock native prompt caching ENABLED "
            "(cachePoint on system+tools+last_message, system_prompt=%d chars)",
            len(system_prompt),
        )
    elif _is_anthropic:
        from langchain_core.messages import SystemMessage
        from app.core.prompt_caching import split_system_prompt
        head, tail = split_system_prompt(system_prompt)
        blocks: list = [{"type": "text", "text": head, "cache_control": {"type": "ephemeral"}}]
        if tail:
            blocks.append({"type": "text", "text": tail})
        prompt_arg = SystemMessage(content=blocks)
        logger.info(
            "ReactStrategy: Anthropic prompt caching ENABLED "
            "(system_prompt=%d chars, volatile_tail=%d chars)",
            len(head), len(tail),
        )

    # ── Proactive mid-loop compaction ─────────────────────────────────
    # The ReAct loop replays the full message history to the model on every
    # iteration. _execute_agent compacts once before invocation, but a long
    # multi-tool investigation (or an HITL re-entry that pre-loads history)
    # can cross the context-window budget mid-loop. This pre_model_hook
    # compacts just-in-time before each model call, returning
    # ``llm_input_messages`` so persisted graph state is untouched. It is a
    # no-op below the 85% threshold, so normal short runs keep the full
    # prompt-cache prefix (Bedrock cachePoint / Anthropic ephemeral) intact.
    # For Anthropic it also re-annotates the (possibly compacted) tail so the
    # growing conversation prefix bills at the cached rate.
    _compaction_session = session_id or "react-agent"
    # Best-effort model id so compaction sizes its window to the actual model
    # (long-context models compact later). Falls back to the 200K default.
    _compaction_model = (
        getattr(llm, "model_id", None) or getattr(llm, "model", None) or None
    )

    async def _pre_model_hook(state: Dict[str, Any]) -> Dict[str, Any]:
        msgs = state.get("messages") or []
        if not msgs:
            return {}
        out: Any = msgs
        try:
            from app.core.memory.compaction_manager import ContextCompactionManager
            from app.core.transport import get_transport
            mgr = ContextCompactionManager(
                transport=get_transport(),
                session_id=_compaction_session,
                model=_compaction_model,
            )
            out = await mgr.compact_if_needed(list(msgs))
        except Exception:  # noqa: BLE001 — compaction must never break a run
            out = msgs
        # Repair any empty-content messages a failed/empty compaction summary
        # may have produced — Bedrock rejects empty messages ("messages.N: ...
        # must have non-empty content"), which would hard-fail this model call.
        # sanitize_messages_for_model is identity-preserving when nothing needs
        # repair, so the no-op fast path (and prompt cache) below is unaffected.
        try:
            from app.workflow.strategies.react.agent_runner import sanitize_messages_for_model
            out = sanitize_messages_for_model(out)
        except Exception:  # noqa: BLE001 — a guard must never break a run
            pass
        if _is_anthropic:
            try:
                from app.core.prompt_caching import apply_anthropic_cache_control
                out = apply_anthropic_cache_control(out, cache_ttl="5m")
            except Exception:  # noqa: BLE001 — caching must never break a run
                pass
        # Only override when we actually changed the messages, so unchanged
        # turns leave graph state — and the cached prefix — untouched.
        if out is msgs:
            return {}
        return {"llm_input_messages": out}

    pre_model_hook = _pre_model_hook

    logger.info(
        "ReactStrategy: building LangGraph ReAct agent with %d tool(s) (%d built-in), "
        "mode=%s, cloudwatch=%s, code_analyzer=%s",
        len(all_tools),
        len(playbook_tools),
        agent_mode,
        has_cloudwatch,
        has_code_analyzer,
    )

    hitl_enabled = agent_config.get("hitl_enabled", False)

    if hitl_enabled and checkpointer is not None:
        # Build a custom outer graph that wraps the react agent with a
        # HITL synthesis node.  The synthesis node calls interrupt() with
        # the draft answer so an engineer can approve or reject before the
        # result is returned to the caller.
        from langchain_core.messages import AIMessage
        from langgraph.graph import StateGraph, END
        from langgraph.graph.message import add_messages
        from langgraph.types import interrupt
        from typing import Annotated  # already imported via __future__ + typing

        inner_agent = create_react_agent(
            model=model_for_agent, tools=all_tools, prompt=prompt_arg,
            pre_model_hook=pre_model_hook,
        )

        class _OuterState(Dict):  # type: ignore[misc]
            pass

        async def _run_inner(state: Dict[str, Any]) -> Dict[str, Any]:
            result = await inner_agent.ainvoke({"messages": state.get("messages", [])})
            return {"messages": result["messages"]}

        def _hitl_synthesis(state: Dict[str, Any]) -> Dict[str, Any]:
            msgs = state.get("messages", [])
            draft = next(
                (m.content for m in reversed(msgs)
                 if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None)),
                "",
            )
            decision = interrupt({
                "request_id": str(uuid.uuid4()),
                "draft_answer": draft,
                "message": "Engineer approval required before delivering this investigation result.",
            })
            approved = (decision or {}).get("approved", False)
            if not approved:
                rejection_note = (decision or {}).get("reason", "Rejected by engineer.")
                return {"messages": [AIMessage(content=f"[HITL Rejected] {rejection_note}")]}
            return {}

        builder: Any = StateGraph(dict)
        builder.add_node("run_agent", _run_inner)
        builder.add_node("hitl_synthesis", _hitl_synthesis)
        builder.set_entry_point("run_agent")
        builder.add_edge("run_agent", "hitl_synthesis")
        builder.add_edge("hitl_synthesis", END)

        agent = builder.compile(checkpointer=checkpointer)
    else:
        agent = create_react_agent(
            model=model_for_agent,
            tools=all_tools,
            prompt=prompt_arg,
            checkpointer=checkpointer,
            pre_model_hook=pre_model_hook,
        )

    return agent
