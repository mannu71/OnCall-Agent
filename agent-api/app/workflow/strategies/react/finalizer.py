"""ReAct strategy — finalizer: synthesis-as-floor, auto-learn, trajectory
save, structured "Data Query Mode" output, PII rehydration, and the final
result-envelope assembly.

Split out of the former ``ReactStrategy.execute`` god-orchestrator (see
:mod:`preflight` for setup and :mod:`executor` for the supervised run).
Also hosts ``cleanup_on_error`` — the teardown a failed run needs regardless
of how far it got (vault, deep-agent scratch, MCP connections).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.core import privacy
from app.core.llm.model_metadata import window_size_for_model
from app.core.privacy.redact import redact
from app.harness.context_builder import apply_synthesis_floor
from app.workflow.strategies.react.learning import auto_learn
from app.workflow.strategies.react.preflight import RunPlan

logger = logging.getLogger(__name__)


def _rehydrate_structured(obj: Any, session_id: Optional[str]) -> Any:
    """Deep-rehydrate placeholder strings in a structured-output value."""
    if isinstance(obj, str):
        return privacy.rehydrate(obj, session_id)
    if isinstance(obj, dict):
        return {k: _rehydrate_structured(v, session_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rehydrate_structured(v, session_id) for v in obj]
    return obj


async def finalize(
    plan: RunPlan,
    result: Dict[str, Any],
    tokens: tuple,
    *,
    workflow: Dict[str, Any],
    context: Dict[str, Any],
    execution_id: Optional[str],
    logger_instance: Any,
    mcp_manager: Any,
    execution_start: datetime,
) -> Dict[str, Any]:
    (
        accum_input_tokens, accum_output_tokens,
        accum_cache_read_tokens, accum_cache_creation_tokens,
    ) = tokens

    user_query = plan.user_query
    agent_config = plan.agent_config
    llm_config = plan.llm_config
    llm = plan.llm
    spec = plan.spec
    code_analyzer_config = plan.code_analyzer_config
    # Skills to surface on the UI badge: the skills actually invoked this run —
    # the model's ``skill`` tool and any user /slash-command
    # (``plan.invoked_skills`` was mutated live by the skill tool's sink). A
    # ``search_skills`` hit is not an invocation and never lands here. Deduped,
    # order-preserving; the metadata key stays ``selected_skills`` (UI contract).
    selected_skills = list(dict.fromkeys(plan.invoked_skills or []))
    recall_hits = plan.recall_hits

    # Synthesis-as-floor: if the agent's own answer is empty/refusal/
    # truncated/short-fragment, fall back to the deterministic CloudWatch
    # synthesis seeded above rather than returning a half-finished run.
    result = apply_synthesis_floor(
        result=result,
        cw_synthesis=plan.cw_synthesis,
        logger_instance=logger_instance,
        execution_id=execution_id,
    )

    # Terminal state (app.harness.terminal_state): "was this run actually a
    # success" — computed HERE (before auto_learn) so learning can refuse to
    # persist findings from a run that didn't cleanly succeed. Also reused for
    # the return payload below. Reads the planning-tool todo list for the
    # verified-completion signal. Best-effort; a derivation failure must never
    # block the result.
    _todos = []
    _completion = {}
    terminal_state = None
    try:
        from app.harness import planning_tools as _pl
        _todos = await _pl.get_todos(execution_id)
    except Exception:  # noqa: BLE001
        _todos = []
    try:
        from app.harness.completion_check import check_completion
        _completion = check_completion(_todos)
    except Exception:  # noqa: BLE001
        _completion = {}
    try:
        from app.core.quality.intent import is_conversational
        from app.harness.terminal_state import terminal_state_for_result
        terminal_state = terminal_state_for_result(
            result, is_conversational=is_conversational(user_query),
            plan_incomplete=bool(_completion.get("plan_incomplete")),
        )
    except Exception as _ts_err:  # noqa: BLE001
        logger_instance.debug("ReactStrategy: terminal_state derivation skipped (%s)", _ts_err)

    # Post-run learning is OPT-IN per workflow (agent node `autoLearn`).
    # Off by default so a run never writes to skills/memory unless the
    # workflow asked for it. ``spec.auto_learn`` already parsed the toggle
    # (string 'true'/'false') correctly. Best-effort — never sinks a run.
    if spec.auto_learn:
        try:
            await auto_learn(
                user_query, result, execution_id, execution_start, recall_hits,
                logger_instance, code_analyzer_config=code_analyzer_config,
                workflow_name=workflow.get("name") or workflow.get("id") or "",
                memory_enabled=spec.memory,
                terminal_state=terminal_state,
            )
        except Exception as _learn_call_err:
            logger_instance.warning(
                "ReactStrategy: auto_learn failed (non-fatal): %s",
                redact(str(_learn_call_err)),
                extra={"execution_id": execution_id},
            )
    else:
        logger_instance.debug(
            "ReactStrategy: auto-learn off for this workflow (execution_id=%s)",
            execution_id,
        )

    if execution_id is not None:
        try:
            from app.services.trajectory_service import trajectory_service

            await trajectory_service.save_trajectory(
                execution_id=str(execution_id),
                trajectory=result.get("messages", []),
            )
        except Exception as traj_err:
            logger_instance.warning(
                "ReactStrategy: failed to save trajectory: %s",
                traj_err,
                extra={"execution_id": execution_id},
            )

    # ── Structured "Data Query Mode" ────────────────────────────────
    # When requested, bind the model to InvestigationReport for ONE final
    # synthesis over the agent's answer so callers get validated JSON
    # alongside (not instead of) the prose. The ReAct loop itself stays
    # freeform — only this last step is schema-constrained.
    _ctx = context if isinstance(context, dict) else {}
    _output_mode = str(
        _ctx.get("output_mode")
        or (_ctx.get("inputs") or {}).get("output_mode")
        or agent_config.get("outputMode")
        or (agent_config.get("params") or {}).get("outputMode")
        or "text"
    ).lower()
    # Resolve which structured schema the agent/profile explicitly chose
    # (outputSchema). Blank = none chosen. See output_registry.
    _chosen_schema = str(
        _ctx.get("output_schema")
        or (_ctx.get("inputs") or {}).get("output_schema")
        or agent_config.get("outputSchema")
        or (agent_config.get("params") or {}).get("outputSchema")
        or ""
    ).lower().strip()
    # Structured output runs when EITHER the legacy outputMode=='structured'
    # flag is set OR a concrete schema was picked in the node dropdown. When
    # only the legacy flag is set (no schema), default to investigation so
    # pre-existing workflows are unchanged.
    _want_structured = (
        _output_mode == "structured"
        or (_chosen_schema not in ("", "text", "none"))
    )
    _schema_name = _chosen_schema or "investigation"
    if _want_structured:
        _output_mode = "structured"  # report accurately downstream
    structured_output = None
    if _want_structured and (result.get("final_answer") or "").strip():
        try:
            from app.workflow.strategies.react.output_registry import resolve_output_schema
            _schema_model = resolve_output_schema(_schema_name)
            _struct_llm = llm.with_structured_output(_schema_model)
            # This is another Bedrock call — keep it on placeholders. The
            # agent answer already contains placeholders; pseudonymize the
            # raw user question too. The whole result is re-hydrated below.
            # The investigation prompt is preserved verbatim (cache/eval
            # stability); other schemas use a schema-agnostic instruction.
            if _schema_name == "investigation":
                _struct_raw = (
                    "Convert the following code-investigation answer into the structured "
                    "report schema. Use ONLY facts present in the answer; cite file:line "
                    "evidence exactly as written; do not invent fields.\n\n"
                    f"User question:\n{user_query}\n\n"
                    f"Investigation answer:\n{result.get('final_answer')}"
                )
            else:
                _struct_raw = (
                    "Convert the following answer into the structured report schema. "
                    "Use ONLY facts present in the answer; do not invent fields.\n\n"
                    f"User question:\n{user_query}\n\n"
                    f"Answer:\n{result.get('final_answer')}"
                )
            _struct_prompt = privacy.pseudonymize(_struct_raw, execution_id)
            _report = await _struct_llm.ainvoke(_struct_prompt)
            structured_output = (
                _report.model_dump() if hasattr(_report, "model_dump") else dict(_report)
            )
            logger_instance.info(
                "ReactStrategy: structured output produced (execution_id=%s)", execution_id,
            )
        except Exception as _se:  # noqa: BLE001 — structured synth is additive
            logger_instance.warning(
                "ReactStrategy: structured synthesis failed (%s); returning text only "
                "(execution_id=%s)", redact(str(_se)), execution_id,
            )

    if mcp_manager:
        await mcp_manager.disconnect_all()

    # ── Re-hydrate placeholders for the user-facing surfaces ─────────
    # The agent reasoned over placeholders; restore real PII in the final
    # answer + structured output so the operator sees true values. The
    # redaction summary is UI-safe (counts/placeholders/masked previews,
    # never raw values). Then drop the vault so raw PII does not outlive
    # the run. All no-ops when the feature is disabled.
    _privacy_redactions = privacy.redaction_summary(execution_id)
    final_answer = privacy.rehydrate(result.get("final_answer"), execution_id)
    if structured_output:
        structured_output = _rehydrate_structured(structured_output, execution_id)
    privacy.drop_vault(execution_id)

    # Deep-agent session scratch (todos + virtual FS). ``_todos`` was already
    # captured above (for the terminal-state completion signal); just drop the
    # store now so nothing outlives the run.
    try:
        from app.harness import planning_tools as _pl
        await _pl.drop_session(execution_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        # Metamemory session persistence: sync BEFORE the drop below removes
        # this execution's VFS row — otherwise there's nothing left to sync.
        from app.harness import metamemory as _metamemory
        _chat_session_id = (context.get("inputs") or {}).get("_chat_session_id")
        await _metamemory.sync_session_persistence_out(execution_id, _chat_session_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.core import vfs as _vfs
        await _vfs.vfs_drop_session(execution_id)
    except Exception:  # noqa: BLE001
        pass

    # Context-window usage (for the Chat UI's context bar): how full
    # the model's context window is RIGHT NOW, based on this turn's
    # true total input (fresh + cache_read + cache_creation — see
    # agent_runner.py's TokenUsageCallback notes on why these three
    # are additive, not overlapping). Current-turn fullness, not the
    # session's cumulative spend (that's tracked separately per
    # chat_sessions.total_* — see session_repository.append_message).
    _context_window_size = window_size_for_model(llm_config.get("model", ""))
    _context_used_tokens = accum_input_tokens + accum_cache_read_tokens + accum_cache_creation_tokens
    _context_used_pct = (
        min(100, round(_context_used_tokens / _context_window_size * 100))
        if _context_window_size else 0
    )

    # terminal_state was derived above (before auto_learn, which consumes it).
    return {
        "type": "react",
        "user_query": user_query,
        "terminal_state": terminal_state,
        "final_answer": final_answer,
        "structured_output": structured_output,
        "output_mode": _output_mode,
        "privacy_redactions": _privacy_redactions,
        "todos": _todos,
        "selected_skills": selected_skills,
        "messages": result.get("messages", []),
        "message_count": len(result.get("messages", [])),
        "tool_calls": result.get("tool_calls", []),
        "model": llm_config.get("model", "unknown"),
        "provider": llm_config.get("provider", "unknown"),
        "supervisor_escalated": result.get("supervisor_escalated", False),
        "supervisor_reason": result.get("supervisor_reason"),
        "input_tokens": accum_input_tokens,
        "output_tokens": accum_output_tokens,
        "total_tokens": accum_input_tokens + accum_output_tokens,
        "cache_read_tokens": accum_cache_read_tokens,
        "cache_creation_tokens": accum_cache_creation_tokens,
        "context_window_size": _context_window_size,
        "context_used_tokens": _context_used_tokens,
        "context_used_pct": _context_used_pct,
    }


async def cleanup_on_error(execution_id: Optional[str], mcp_manager: Any) -> None:
    """Best-effort teardown for a failed run — MCP, vault, deep-agent scratch.

    Mirrors the ``except`` block's cleanup from the pre-split
    ``ReactStrategy.execute``. Never raises.
    """
    if mcp_manager:
        try:
            await mcp_manager.disconnect_all()
        except Exception:  # noqa: BLE001
            pass
    try:
        privacy.drop_vault(execution_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.harness import planning_tools as _pl
        await _pl.drop_session(execution_id)
        from app.core import vfs as _vfs
        await _vfs.vfs_drop_session(execution_id)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["finalize", "cleanup_on_error"]
