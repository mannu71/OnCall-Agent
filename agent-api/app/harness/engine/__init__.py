"""The agent turn runner — one entry point for running an agent to completion.

The agent loop is ``langgraph.prebuilt.create_react_agent``; this module owns
the run-level concerns wrapped around it (checkpoint durability, the wall-clock
backstop, partial-state recovery).

Every call site that runs an agent turn (the main workflow node, subagent
delegation) should go through :func:`run_agent_once` rather than calling
``build_agent_from_spec``/``execute_agent`` directly — that keeps those
run-level concerns in one place.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

#: Grace beyond the configured deadline before the outer wait_for gives up. The
#: in-loop budget stop (app.harness.run_budget) should always finish first; this
#: only fires when a single model/tool call hangs, where no hook can run.
_DEADLINE_BACKSTOP_GRACE_S = 60.0


def _resolve_run_deadline(agent_config: Optional[Dict[str, Any]]) -> float:
    """Per-workflow override (agent_config / params mirror) over the global
    ``agent_run_deadline_seconds``. Absent/invalid → global default."""
    cfg = agent_config or {}
    params = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
    raw = cfg.get("run_deadline_seconds")
    if raw is None:
        raw = params.get("run_deadline_seconds")
    if raw is None:
        raw = getattr(settings, "agent_run_deadline_seconds", 0.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def _recover_partial_from_checkpointer(
    checkpointer: Any, thread_id: Optional[str],
) -> Dict[str, Any]:
    """Best-effort read-back of a timed-out LangGraph run's partial state by
    thread_id (same mechanism the subagent timeout path uses). Returns the last
    AI answer + tool-call count, or empties when nothing is recoverable."""
    if checkpointer is None or not thread_id:
        return {"answer": "", "n_calls": 0}
    try:
        snap = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
        msgs = ((snap.checkpoint or {}).get("channel_values", {}) or {}).get("messages", []) if snap else []
        answer = ""
        for m in reversed(msgs):
            if getattr(m, "type", "") == "ai" and getattr(m, "content", ""):
                c = m.content
                answer = c if isinstance(c, str) else str(c)
                break
        n_calls = sum(1 for m in msgs if getattr(m, "type", "") == "tool")
        return {"answer": answer, "n_calls": n_calls}
    except Exception:  # noqa: BLE001 — recovery must never mask the timeout
        return {"answer": "", "n_calls": 0}


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
    compiled_agent: Any = None,
    preloaded_skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run one agent to completion.

    Returns the result-dict contract of ``app.harness.agent_runner.execute_agent``
    (``final_answer``, ``messages``, ``tool_calls``, token counts, ...).

    ``compiled_agent``: a pre-built agent graph to run instead of building one
    from ``spec`` — used by the subagent compiled-agent cache to skip
    ``build_agent_from_spec`` on repeated delegations.

    ``preloaded_skills``: skills whose runbook is already in this run's context
    before the model's first call (deterministic ``/slash`` expansion). Seeds the
    per-context skill re-invocation guard so the model isn't handed the same
    runbook twice. Subagents pass nothing — their guard starts empty, which is
    exactly what isolates a child's skill loads from the parent's.
    """
    agent_config = getattr(spec, "agent_config", None) or {}

    from app.harness import build_agent_from_spec
    from app.harness.agent_runner import execute_agent

    agent = compiled_agent if compiled_agent is not None else build_agent_from_spec(
        spec, llm, tools, checkpointer=checkpointer, execution_port=execution_port,
    )

    # Checkpoint durability: use the configured mode, but clamp HITL runs to
    # "async" — a HITL pause/resume relies on interrupt-time checkpoints, which
    # "exit" would skip (it only writes when the run finishes). Non-HITL runs
    # get the full benefit of "exit" when configured.
    _durability = getattr(settings, "agent_durability", "async")
    if agent_config.get("hitl_enabled", False) and _durability == "exit":
        logger.debug(
            "run_agent_once: HITL run — clamping durability '%s' → 'async'", _durability,
        )
        _durability = "async"

    # Install a fresh per-context skill re-invocation guard for THIS run (parent
    # or subagent), seeded with any /slash-preloaded skills. Isolates each
    # context's "already loaded" bookkeeping — see app.harness.skill_tools.
    from app.harness.skill_tools import install_load_guard, reset_load_guard
    _guard_token = install_load_guard(preloaded_skills)
    try:
        _run = execute_agent(
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
            durability=_durability,
            agent_config=agent_config,
        )

        # Engine-level wall-clock deadline — BACKSTOP ONLY.
        #
        # The graceful path lives in the pre-model hook (app.harness.run_budget):
        # it nudges for synthesis at ~90% and stops with a recovered partial at
        # 100%, which is a far better answer than anything reconstructible from
        # outside. But that hook only fires BETWEEN model calls, so it cannot
        # observe a single model or tool call that hangs. This wait_for covers
        # exactly that case, and sits a grace period beyond the deadline so a run
        # concluding gracefully always wins the race. 0 disables.
        _deadline = _resolve_run_deadline(agent_config)
        if _deadline <= 0:
            return await _run
        try:
            return await asyncio.wait_for(_run, timeout=_deadline + _DEADLINE_BACKSTOP_GRACE_S)
        except asyncio.TimeoutError:
            (logger_instance or logger).warning(
                "run_agent_once: LangGraph run exceeded the %.0fs wall-clock deadline "
                "by more than %.0fs of grace (a single model/tool call is hung) — "
                "returning partial (execution_id=%s)",
                _deadline, _DEADLINE_BACKSTOP_GRACE_S, execution_id,
            )
            _partial = await _recover_partial_from_checkpointer(checkpointer, thread_id)
            _prefix = (
                "Investigation was stopped at its time budget before it could "
                "complete. Partial findings so far:\n\n"
            )
            _answer = (_prefix + _partial["answer"]).strip() if _partial["answer"] else (
                _prefix + "(no partial answer was recovered)"
            ).strip()
            return {
                "final_answer": _answer,
                "messages": [],
                "tool_calls": [],
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "stop_reason": "deadline",
                "truncated": True,
            }
    finally:
        reset_load_guard(_guard_token)


__all__ = ["run_agent_once"]
