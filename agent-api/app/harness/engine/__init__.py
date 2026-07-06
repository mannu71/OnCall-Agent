"""The agent turn-loop engine — dispatch point between the LangGraph ReAct
agent (``langgraph.prebuilt.create_react_agent``, today's default) and the
native hand-rolled turn loop being built out incrementally behind the
``AGENT_ENGINE`` flag.

Every call site that runs an agent turn (the main workflow node, subagent
delegation) should go through :func:`run_agent_once` rather than choosing
between ``build_agent_from_spec``/``execute_agent`` and the native loop
directly — that keeps engine selection in one place.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

ENGINE_LANGGRAPH = "langgraph"
ENGINE_NATIVE = "native"
_VALID_ENGINES = (ENGINE_LANGGRAPH, ENGINE_NATIVE)


def resolve_engine(
    agent_config: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve which engine should drive this run.

    Precedence (highest first): an explicit per-request override — passed as
    ``context["engine"]`` or ``context["inputs"]["engine"]``, same pattern as
    :func:`app.harness.spec_factory.resolve_permission_mode` — lets a single
    chat turn pick ReAct/Native without touching the saved workflow. Falling
    back from there: per-workflow ``agent_config["engine"]`` (or its
    ``params`` mirror, same coercion pattern as ``supervisor_enabled``), then
    the global ``AGENT_ENGINE`` setting.

    HITL runs are clamped to ``langgraph`` until the native engine grows its
    own pause/resume + durable persistence story — resuming a HITL-paused run
    currently relies on the LangGraph Postgres checkpointer, which the native
    loop does not yet have.
    """
    agent_config = agent_config or {}
    ctx = context or {}
    params = agent_config.get("params") if isinstance(agent_config.get("params"), dict) else {}

    raw = (
        ctx.get("engine")
        or (ctx.get("inputs") or {}).get("engine")
        or agent_config.get("engine")
        or params.get("engine")
    )
    engine = str(raw).strip().lower() if raw else settings.agent_engine

    if engine not in _VALID_ENGINES:
        logger.warning(
            "resolve_engine: unknown engine '%s', falling back to '%s'",
            engine, ENGINE_LANGGRAPH,
        )
        engine = ENGINE_LANGGRAPH

    if engine == ENGINE_NATIVE and agent_config.get("hitl_enabled", False):
        logger.warning(
            "resolve_engine: HITL is not yet supported on the native engine — "
            "forcing '%s' for this run", ENGINE_LANGGRAPH,
        )
        engine = ENGINE_LANGGRAPH

    return engine


async def run_agent_once(
    spec: Any,
    llm: Any,
    tools: List[Any],
    user_query: str,
    *,
    logger_instance: Any = None,
    execution_id: Optional[str] = None,
    stream_callback: Any = None,
    thread_id: Optional[str] = None,
    execution_port: Any = None,
    conversation_history: Optional[list] = None,
    retry_predicate: Optional[Any] = None,
    recursion_limit: Optional[int] = None,
    model_name: Optional[str] = None,
    checkpointer: Any = None,
    engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one agent turn-loop to completion, routed by ``engine``.

    ``engine`` defaults to ``resolve_engine(spec.agent_config)`` when omitted.
    Both branches return the same result-dict contract as
    ``app.harness.agent_runner.execute_agent``
    (``final_answer``, ``messages``, ``tool_calls``, token counts, ...).
    """
    agent_config = getattr(spec, "agent_config", None) or {}
    resolved = engine or resolve_engine(agent_config)

    if resolved == ENGINE_NATIVE:
        return await _run_native(
            spec, llm, tools, user_query,
            logger_instance=logger_instance or logger,
            execution_id=execution_id,
            stream_callback=stream_callback,
            execution_port=execution_port,
            conversation_history=conversation_history,
            retry_predicate=retry_predicate,
            recursion_limit=recursion_limit,
            model_name=model_name,
        )

    from app.harness import build_agent_from_spec
    from app.harness.agent_runner import execute_agent

    agent = build_agent_from_spec(
        spec, llm, tools, checkpointer=checkpointer, execution_port=execution_port,
    )
    return await execute_agent(
        agent,
        user_query,
        logger_instance or logger,
        execution_id,
        stream_callback,
        thread_id=thread_id,
        execution_port=execution_port,
        conversation_history=conversation_history,
        retry_predicate=retry_predicate,
        recursion_limit=recursion_limit,
        model_name=model_name,
    )


async def _run_native(
    spec: Any,
    llm: Any,
    tools: List[Any],
    user_query: str,
    *,
    logger_instance: Any,
    execution_id: Optional[str],
    stream_callback: Any,
    execution_port: Any,
    conversation_history: Optional[list],
    retry_predicate: Optional[Any] = None,
    recursion_limit: Optional[int],
    model_name: Optional[str],
) -> Dict[str, Any]:
    from app.harness.engine.turn_loop import TurnLoop
    from app.harness.agent_builder import compose_system_prompt
    from app.harness.agent_runner import build_initial_messages

    agent_config = spec.agent_config or {}
    system_prompt = compose_system_prompt(
        tools=tools,
        agent_config=agent_config,
        has_cloudwatch=spec.has_cloudwatch,
        has_code_analyzer=spec.has_code_analyzer,
        capabilities=spec.capabilities,
        role_prompt=spec.role_prompt,
        planning=spec.planning,
        filesystem=spec.filesystem,
        sandbox=spec.sandbox,
        verify=bool(spec.verify_command),
        subagents=spec.subagents,
    )
    # Native loop's turn budget is turn-based, not LangGraph-step-based; a
    # caller-supplied recursion_limit (e.g. a subagent's max_turns*2) is
    # halved back to turns so both engines get an equivalent bound.
    max_turns = (recursion_limit // 2) if recursion_limit is not None else None

    loop = TurnLoop(
        llm, tools, system_prompt,
        agent_config=agent_config,
        permission_mode=spec.permission_mode,
        session_id=spec.session_id,
        execution_id=execution_id,
        execution_port=execution_port,
        policies=spec.policies,
        stream_callback=stream_callback,
        model_name=model_name,
        max_turns=max_turns,
        logger_instance=logger_instance,
        retry_predicate=retry_predicate,
    )
    initial_messages = build_initial_messages(conversation_history, user_query, logger_instance)
    return await loop.run(initial_messages)


__all__ = [
    "ENGINE_LANGGRAPH",
    "ENGINE_NATIVE",
    "resolve_engine",
    "run_agent_once",
]
