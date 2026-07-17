"""In-house LangGraph ReAct agent construction — the platform's single harness.

Builds a ``create_react_agent`` graph with full governance (policy engine),
Bedrock native prompt caching (cachePoint), proactive mid-loop compaction,
and optional HITL approval gating. The accuracy-critical system-prompt
composition stays shared in ``agent_builder.compose_system_prompt``.

:func:`prepare_action_space` factors out the governance + prompt-caching
setup (playbook tools, policy engine, provider-aware cache binding) so the
native turn-loop engine (``app.harness.engine``) can build an equivalent
action space without depending on LangGraph.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.harness.agent_builder import compose_system_prompt
from app.harness.tool_setup import build_playbook_tools

logger = logging.getLogger(__name__)


@dataclass
class ActionSpace:
    """The governed, cache-bound tool-calling surface for one agent run."""

    model_for_agent: Any    # llm, tool-bound + (Bedrock) cache-bound
    all_tools: List[Any]    # policy-filtered tools + playbook tools
    prompt_arg: Any         # str, or a cache-annotated SystemMessage (Anthropic)
    is_bedrock: bool
    is_anthropic: bool
    playbook_tool_count: int


def prepare_action_space(
    llm: Any,
    tools: List[Any],
    system_prompt: str,
    *,
    permission_mode: str = "default",
    session_id: Optional[str] = None,
    execution_port: Any = None,
    policies: Optional[List[Dict[str, Any]]] = None,
) -> ActionSpace:
    """Governance (policy engine) + provider-aware prompt caching.

    Shared by every engine that runs a ReAct loop over ``tools`` — appends
    agent-writable playbook tools, applies the declarative policy engine
    (single evaluation point), and binds provider-native prompt caching
    (Bedrock ``cachePoint`` / Anthropic ``cache_control`` blocks).
    """
    # Add agent-writable playbook tools so the agent can persist resolutions.
    playbook_tools = build_playbook_tools()
    all_tools = list(tools) + playbook_tools

    # ── Governance: declarative policy engine (single evaluation point) ───
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
            "ReactAgent: policy engine active (mode=%s, ask=%d, deny=%d, cap=%s, "
            "cost_ceiling=%s, max_tool_calls=%s)",
            permission_mode, len(resolved.effective_ask_patterns()),
            len(resolved.deny_patterns), resolved.output_cap_chars,
            resolved.budget_cost_usd, resolved.max_tool_calls,
        )
    except Exception as _pol_err:  # noqa: BLE001 — never break a run on governance
        logger.warning("ReactAgent: policy engine skipped (%s)", _pol_err)

    # ── Prompt caching (provider-aware) ───────────────────────────────
    _provider_name = type(llm).__name__
    _is_bedrock = "Bedrock" in _provider_name
    _is_anthropic = "Anthropic" in _provider_name

    model_for_agent: Any = llm
    prompt_arg: Any = system_prompt

    if _is_bedrock:
        model_for_agent = llm.bind_tools(all_tools).bind(
            cache_control={"ttl": "1h"}
        )
        logger.info(
            "ReactAgent: Bedrock native prompt caching ENABLED "
            "(cachePoint on system+tools+last_message, system_prompt=%d chars)",
            len(system_prompt),
        )
    elif _is_anthropic:
        from langchain_core.messages import SystemMessage
        from app.core.llm.prompt_caching import split_system_prompt
        head, tail = split_system_prompt(system_prompt)
        blocks: list = [{"type": "text", "text": head, "cache_control": {"type": "ephemeral"}}]
        if tail:
            blocks.append({"type": "text", "text": tail})
        prompt_arg = SystemMessage(content=blocks)
        logger.info(
            "ReactAgent: Anthropic prompt caching ENABLED "
            "(system_prompt=%d chars, volatile_tail=%d chars)",
            len(head), len(tail),
        )

    return ActionSpace(
        model_for_agent=model_for_agent,
        all_tools=all_tools,
        prompt_arg=prompt_arg,
        is_bedrock=_is_bedrock,
        is_anthropic=_is_anthropic,
        playbook_tool_count=len(playbook_tools),
    )


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
    verify: bool = False,
) -> Any:
    """Build a LangGraph ReAct agent graph.

    Uses ``langgraph.prebuilt.create_react_agent`` which implements the full
    Thought → Action → Observation loop as a compiled StateGraph.
    """
    agent_mode = agent_config.get("agentMode", "single")
    system_prompt = compose_system_prompt(
        tools=tools,
        agent_config=agent_config,
        has_cloudwatch=has_cloudwatch,
        has_code_analyzer=has_code_analyzer,
        capabilities=capabilities,
        role_prompt=role_prompt,
        planning=planning,
        filesystem=filesystem,
        sandbox=sandbox,
        verify=verify,
        subagents=subagents,
    )
    return _finish_build_agent(
        llm, tools, agent_config, system_prompt, agent_mode,
        has_cloudwatch=has_cloudwatch, has_code_analyzer=has_code_analyzer,
        checkpointer=checkpointer, session_id=session_id,
        permission_mode=permission_mode, execution_port=execution_port,
        policies=policies,
    )


def _finish_build_agent(
    llm: Any,
    tools: List[Any],
    agent_config: Dict[str, Any],
    system_prompt: str,
    agent_mode: str,
    *,
    has_cloudwatch: bool = False,
    has_code_analyzer: bool = False,
    checkpointer: Any = None,
    session_id: Optional[str] = None,
    permission_mode: str = "default",
    execution_port: Any = None,
    policies: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Governance + caching + compaction + create_react_agent."""
    import uuid
    from langgraph.prebuilt import create_react_agent

    action_space = prepare_action_space(
        llm, tools, system_prompt,
        permission_mode=permission_mode, session_id=session_id,
        execution_port=execution_port, policies=policies,
    )
    all_tools = action_space.all_tools
    model_for_agent = action_space.model_for_agent
    prompt_arg = action_space.prompt_arg
    _is_anthropic = action_space.is_anthropic
    playbook_tools_count = action_space.playbook_tool_count

    # ── Proactive mid-loop compaction ─────────────────────────────────
    _compaction_session = session_id or "react-agent"
    _compaction_model = (
        getattr(llm, "model_id", None) or getattr(llm, "model", None) or None
    )

    async def _pre_model_hook(state: Dict[str, Any]) -> Dict[str, Any]:
        msgs = state.get("messages") or []
        if not msgs:
            return {}
        out: Any = msgs
        try:
            from app.core.context.compaction_manager import ContextCompactionManager
            from app.core.transport import get_transport
            mgr = ContextCompactionManager(
                transport=get_transport(),
                session_id=_compaction_session,
                model=_compaction_model,
            )
            out = None
            if session_id:
                from app.harness.engine.compression import maybe_metamemory_summary
                out = await maybe_metamemory_summary(
                    list(msgs),
                    vfs_session_id=session_id,
                    compaction_threshold_tokens=mgr.compaction_threshold_tokens,
                    keep_recent_tokens=mgr.keep_recent_tokens,
                    estimate_tokens=mgr.estimate_tokens,
                )
            if out is None:
                out = await mgr.compact_if_needed(list(msgs))
        except Exception:  # noqa: BLE001 — compaction must never break a run
            out = msgs
        try:
            from app.harness.agent_runner import sanitize_messages_for_model
            out = sanitize_messages_for_model(out)
        except Exception:  # noqa: BLE001 — a guard must never break a run
            pass
        if _is_anthropic:
            try:
                from app.core.llm.prompt_caching import apply_anthropic_cache_control
                out = apply_anthropic_cache_control(out, cache_ttl="5m")
            except Exception:  # noqa: BLE001 — caching must never break a run
                pass
        if out is msgs:
            return {}
        return {"llm_input_messages": out}

    pre_model_hook = _pre_model_hook

    logger.info(
        "ReactAgent: building LangGraph ReAct agent with %d tool(s) (%d built-in), "
        "mode=%s, cloudwatch=%s, code_analyzer=%s",
        len(all_tools), playbook_tools_count, agent_mode, has_cloudwatch, has_code_analyzer,
    )

    hitl_enabled = agent_config.get("hitl_enabled", False)

    if hitl_enabled and checkpointer is not None:
        from langchain_core.messages import AIMessage
        from langgraph.graph import StateGraph, END
        from langgraph.types import interrupt

        inner_agent = create_react_agent(
            model=model_for_agent, tools=all_tools, prompt=prompt_arg,
            pre_model_hook=pre_model_hook,
        )

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
