"""Legacy in-house LangGraph ReAct agent construction (harness=legacy fallback).

Moved here from ``app/workflow/strategies/react/agent_builder.py`` as part of the
deepagents migration. The default harness is now ``deepagents`` (see
``app/harness/deep_agent.py``); this builds the previous ``create_react_agent``
graph and is only used when ``settings.harness == "legacy"``.

The accuracy-critical system-prompt composition stays shared in ``agent_builder.
compose_system_prompt`` — both harnesses use the exact same prompt.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.workflow.strategies.react.agent_builder import compose_system_prompt
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
    verify: bool = False,
) -> Any:
    """Build a LangGraph ReAct agent graph (legacy harness).

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
    """Governance + caching + compaction + create_react_agent (legacy ReAct path)."""
    import uuid
    from langgraph.prebuilt import create_react_agent

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
            "ReactStrategy(legacy): policy engine active (mode=%s, ask=%d, deny=%d, cap=%s, "
            "cost_ceiling=%s, max_tool_calls=%s)",
            permission_mode, len(resolved.effective_ask_patterns()),
            len(resolved.deny_patterns), resolved.output_cap_chars,
            resolved.budget_cost_usd, resolved.max_tool_calls,
        )
    except Exception as _pol_err:  # noqa: BLE001 — never break a run on governance
        logger.warning("ReactStrategy(legacy): policy engine skipped (%s)", _pol_err)

    # ── Prompt caching (provider-aware) ───────────────────────────────
    _provider_name = type(llm).__name__
    _is_bedrock = "Bedrock" in _provider_name
    _is_anthropic = "Anthropic" in _provider_name

    model_for_agent: Any = llm
    prompt_arg: Any = system_prompt
    pre_model_hook: Any = None

    if _is_bedrock:
        model_for_agent = llm.bind_tools(all_tools).bind(
            cache_control={"ttl": "1h"}
        )
        logger.info(
            "ReactStrategy(legacy): Bedrock native prompt caching ENABLED "
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
            "ReactStrategy(legacy): Anthropic prompt caching ENABLED "
            "(system_prompt=%d chars, volatile_tail=%d chars)",
            len(head), len(tail),
        )

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
        if out is msgs:
            return {}
        return {"llm_input_messages": out}

    pre_model_hook = _pre_model_hook

    logger.info(
        "ReactStrategy(legacy): building LangGraph ReAct agent with %d tool(s) (%d built-in), "
        "mode=%s, cloudwatch=%s, code_analyzer=%s",
        len(all_tools), len(playbook_tools), agent_mode, has_cloudwatch, has_code_analyzer,
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
