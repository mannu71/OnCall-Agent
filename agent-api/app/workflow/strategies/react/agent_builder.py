"""LangGraph ReAct agent construction."""
from __future__ import annotations

import logging
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

    # ── Role sentence ──────────────────────────────────────────────────
    capabilities = []
    if has_db_tools:
        capabilities.append("database and MCP tools")
    if has_cloudwatch:
        capabilities.append("AWS CloudWatch logs and metrics")
    if has_code_analyzer:
        capabilities.append("source-code analysis")

    if capabilities:
        capability_str = ", ".join(capabilities)
        role_sentence = (
            f"You are an expert engineering assistant "
            f"with access to {capability_str}. Adapt to whatever the user is "
            f"trying to do — root-cause analysis, log retrieval, code inspection, "
            f"code analysis, or general questions."
        )
    else:
        role_sentence = "You are an expert engineering assistant."

    # ── Base instructions ──────────────────────────────────────────────
    system_parts = [
        role_sentence,
        "Always reason step by step and use the available tools to find accurate answers.",
        "Present your findings clearly with specific data from the tool results.",
        "When you reach a useful conclusion or resolution worth reusing, use the save_playbook "
        "tool to record it so future investigations can benefit from it.",
        "If the memory-context block at the start of the query lists 'Executable Skill' entries "
        "that match the current issue, prefer calling execute_skill with the skill's name before "
        "running manual tool calls — this reuses proven remediation steps and is faster.",
    ]

    # DB-specific guidance only when SQL/MCP tools are actually present.
    if has_db_tools:
        system_parts.append(
            "When querying databases, prefer targeted queries over full table scans. "
            "Use WHERE clauses, date ranges, and LIMIT to avoid expensive full scans."
        )

    # ── CloudWatch instructions (refine, don't redo) ──────────────────────
    if has_cloudwatch:
        system_parts.append(
            "A deterministic CloudWatch log scan has ALREADY run — its results "
            "(alarms, anomalies, error patterns, any drill-down, and a data_quality "
            "coverage block) are in the 'Pre-computed CloudWatch Analysis' block at the "
            "start of this query. Treat that as your starting evidence. Do NOT re-run the "
            "full scan. Use the live tools only to VERIFY or DRILL DEEPER into specific "
            "findings: cloudwatch_search_logs (drill_down=true) for raw events behind a "
            "pattern/anomaly, cloudwatch_correlate_logs to trace one request across groups, "
            "cloudwatch_discover_log_groups only if a referenced group is missing. "
            "If the user gives a correlation id / request id / trace id (or the pre-computed "
            "block is a 'correlation-lookup'), LEAD with cloudwatch_correlate_logs for that "
            "id and build the cross-service timeline before anything else. "
            "Cite log_group, timestamp, z_score/occurrence_count, and normalized_pattern. "
            "Respect data_quality: if it reports partial/sampled results or failed groups, "
            "say so and confirm with a targeted cloudwatch_search_logs before concluding; "
            "if coverage is full and nothing was found, state that explicitly rather than "
            "implying a problem. Stop as soon as the evidence supports a conclusion — each "
            "Insights query has cost and latency."
        )

    # ── Code Analyzer instructions (compact) ──────────────────────────
    if has_code_analyzer:
        system_parts.append(
            "Code analysis tools available over the connected repositories. To find code by "
            "natural-language description, use crawler_search_semantic(query, repo). If you are "
            "tracing a specific error message / stack trace / CloudWatch anomaly back to its "
            "source, crawler_investigate_alert(alert, repo) locates the offending code for that "
            "alert. To read a specific symbol: crawler_find_symbol(symbol, repo) returns a body "
            "handle, then crawler_get_body(handle) prints the source. Use "
            "crawler_trace_path(symbol, repo, direction='callers'|'callees', depth) to follow the call "
            "graph. Only call crawler_index_repo(repo) if a repository appears unindexed. Pass the exact "
            "repo name shown in the tool descriptions, and cite repo, file path, symbol, and line in "
            "your findings."
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
            )
            out = await mgr.compact_if_needed(list(msgs))
        except Exception:  # noqa: BLE001 — compaction must never break a run
            out = msgs
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
