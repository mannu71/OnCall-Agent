"""Post-execution learning and graceful fallback for ReAct agents."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.privacy.redact import redact
from app.harness.helpers import collect_failed_tools, estimate_confidence

logger = logging.getLogger(__name__)

async def auto_learn(
    user_query: str,
    result: Dict[str, Any],
    execution_id: Optional[str],
    execution_start: "datetime",
    recall_hits: int,
    logger_instance: Any,
    code_analyzer_config: Optional[Dict[str, Any]] = None,
    workflow_name: str = "",
    memory_enabled: bool = False,
    terminal_state: Optional[str] = None,
) -> None:
    """Persist what this execution found to the knowledge base.

    Phase 1 — record_analysis (lightweight, always runs).
    Phase 2 — AutoLearnService.learn() (closed learning loop):
        • auto-approve gate (confidence threshold)
        • KB upsert + pattern bump
        • trajectory JSONL
        • skill distillation via LLM when enough tool calls exist

    Durable-write gate: the KB/semantic writes (Phases 2, 4, 4b) are skipped
    when the run did not cleanly succeed — either it cited ungrounded IDs
    (likely fabrication, the harness sets ``result["ungrounded_ids"]``) or its
    terminal_state is anything other than ``success`` (exhausted/blocked/
    unverified/stalled). Learning from a shaky run poisons future recall, so a
    non-success run is recorded (Phase 1) but not learned from.

    Any failure in any phase is caught and logged as a warning — it must never
    propagate to the caller.
    """
    final_answer = result.get("final_answer") or ""
    execution_end: Optional["datetime"] = None

    # Durable-write gate (see docstring). None terminal_state = caller didn't
    # derive it → don't block on that signal alone (back-compat).
    _ungrounded = result.get("ungrounded_ids") or []
    _state_ok = terminal_state in (None, "success")
    _learn_ok = _state_ok and not _ungrounded
    if not _learn_ok:
        logger_instance.info(
            "ReactStrategy: durable learning skipped — terminal_state=%s ungrounded_ids=%d "
            "(recording analysis only, not learning from this run)",
            terminal_state, len(_ungrounded), extra={"execution_id": execution_id},
        )

    # ── Phase 1: lightweight record_analysis ─────────────────────────
    try:
        execution_end = datetime.now(timezone.utc)
        from app.services.knowledge_base import knowledge_base as _kb
        await _kb.record_analysis(
            log_group=str(execution_id or "unknown"),
            analysis_type="react_agent",
            start_time=execution_start,
            end_time=execution_end,
            summary=final_answer[:2000],
            anomalies_found=0,
            patterns_matched=recall_hits,
        )
    except Exception as _rec_err:
        logger_instance.warning(
            "ReactStrategy: record_analysis failed (non-fatal): %s",
            redact(str(_rec_err)),
            extra={"execution_id": execution_id},
        )

    # Durable-write gate: a non-success/ungrounded run is recorded (Phase 1
    # above) but never LEARNED from — skip the KB upsert, semantic capture and
    # fact extraction so shaky findings don't poison future recall.
    if not _learn_ok:
        return

    # ── Phase 2: closed learning loop (AutoLearnService) ─────────────
    try:
        from app.core.improvement.auto_learn import AutoLearnService, AutoLearnConfig
        from app.core.database import AsyncSessionLocal

        _config = AutoLearnConfig()

        # Resolve a DB-configured auxiliary model for distillation. No model is
        # hardcoded — ``resolve_llm_config_for_role`` falls back to the default
        # DB LLM config when no 'auxiliary' role is assigned (same pattern as the
        # subagent path). Without this the service can still upsert the KB and
        # write trajectories; only LLM-backed skill/node distillation is skipped.
        _aux_llm = None
        try:
            from app.workflow.llm_config import resolve_llm_config_for_role
            from app.workflow.strategies.react.llm_factory import build_llm

            _aux_cfg = await resolve_llm_config_for_role("auxiliary")
            _aux_llm = build_llm(_aux_cfg)
        except Exception as _aux_err:
            logger_instance.warning(
                "ReactStrategy: auto-learn aux LLM unavailable (%s); "
                "skill distillation will be skipped",
                redact(str(_aux_err)),
                extra={"execution_id": execution_id},
            )

        # Derive a rough confidence score: high when a resolution keyword
        # was found, moderate otherwise.  The AutoLearnService gate uses
        # this to decide whether to auto-approve.
        _confidence = estimate_confidence(final_answer, result.get("tool_calls", []))

        # Build the state dict AutoLearnService expects. ``root_cause`` is the
        # trigger for IncidentKBSink (the OKF known-issue write); populate it from
        # the run's finding so the sink actually fires — historically it was never
        # set, so the KB write was dead. The whole learn pass is still gated on
        # confidence (auto-approve threshold), so only confident, resolution-
        # bearing runs produce a known-issue doc.
        _learn_state = {
            "execution_id": execution_id,
            "user_query": user_query,
            "final_answer": final_answer,
            "root_cause": final_answer.strip(),
            "workflow_name": result.get("workflow_name") or workflow_name or "",
            "tool_calls": result.get("tool_calls", []),
            "confidence_score": _confidence,
            "recall_hits": recall_hits,
            "execution_start": execution_start.isoformat(),
            "execution_end": execution_end.isoformat(),
        }

        # A real DB session is required for the KB upsert + pattern bump to run;
        # the service commits/rolls back internally, so a scoped session is
        # correct. Without it the entire loop was a silent no-op.
        async with AsyncSessionLocal() as _db:
            _svc = AutoLearnService(db=_db, llm=_aux_llm, config=_config)
            _learn_result = await _svc.learn(
                execution_id=str(execution_id or "unknown"),
                state=_learn_state,
            )

        logger_instance.info(
            "ReactStrategy: AutoLearnService completed — "
            "kb=%s pattern=%s trajectory=%s skipped=%s",
            _learn_result.kb_upserted,
            _learn_result.pattern_bumped,
            _learn_result.trajectory_saved,
            _learn_result.skipped_reason or "none",
            extra={"execution_id": execution_id},
        )

    except Exception as _learn_err:
        logger_instance.warning(
            "ReactStrategy: AutoLearnService.learn failed (non-fatal): %s",
            redact(str(_learn_err)),
            extra={"execution_id": execution_id},
        )

    # ── Phase 4: semantic memory capture (opt-in, Phase 2 borrow) ────
    # Store a confident, resolution-bearing finding into the bank-scoped
    # semantic memory so future investigations recall it. Gated by the enable
    # flag + a confidence floor so we don't bloat memory with uncertain runs.
    try:
        from app.config import settings
        # Capture is driven by a Memory node on the agent (``memory_enabled``);
        # the global flag stays a master override for non-graph callers.
        if (memory_enabled or settings.semantic_memory_enabled) and final_answer.strip():
            _conf = estimate_confidence(final_answer, result.get("tool_calls", []))
            if _conf >= settings.memory_capture_min_confidence:
                from app.services.semantic_memory import semantic_memory
                from app.harness.context_builder import repos_from_code_analyzer

                repos = repos_from_code_analyzer(code_analyzer_config)
                _q = (user_query or "").strip().replace("\n", " ")
                if len(_q) > 300:
                    _q = _q[:300] + "…"
                _ans = final_answer.strip()
                if len(_ans) > 1200:
                    _ans = _ans[:1200] + "…"
                content = f"Issue: {_q}\nFinding: {_ans}"
                mem_id = await semantic_memory.remember(
                    content,
                    repo=(repos[0] if repos else None),
                    source="agent",
                    importance=_conf,
                    veracity=_conf,
                )
                logger_instance.info(
                    "ReactStrategy: semantic memory captured (id=%s repo=%s conf=%.2f)",
                    mem_id, (repos[0] if repos else "global"), _conf,
                    extra={"execution_id": execution_id},
                )
    except Exception as _mem_err:
        logger_instance.warning(
            "ReactStrategy: semantic memory capture failed (non-fatal): %s",
            redact(str(_mem_err)),
            extra={"execution_id": execution_id},
        )

    # ── Phase 4b: per-turn durable-fact extraction ──────────────────
    # Opt-in (``memory_fact_extraction_enabled``) and gated on the semantic tier
    # (``memory_enabled`` == spec.memory). Extracts a couple of short, durable
    # facts from this turn and stores them in ``semantic_memory`` (deduped by the
    # store's sha256 index). Previously this only ran through the removed
    # MemoryManager provider stack, so the flag was dead — this is the live path.
    try:
        from app.config import settings
        if (
            getattr(settings, "memory_fact_extraction_enabled", False)
            and memory_enabled
            and final_answer.strip()
        ):
            from app.core.memory.fact_extractor import extract_and_store
            from app.harness.context_builder import repos_from_code_analyzer

            repos = repos_from_code_analyzer(code_analyzer_config)
            n_stored = await extract_and_store(
                user_query or "",
                final_answer,
                repo=(repos[0] if repos else None),
            )
            if n_stored:
                logger_instance.info(
                    "ReactStrategy: durable-fact extraction stored %d fact(s)",
                    n_stored, extra={"execution_id": execution_id},
                )
    except Exception as _fact_err:
        logger_instance.warning(
            "ReactStrategy: durable-fact extraction failed (non-fatal): %s",
            redact(str(_fact_err)),
            extra={"execution_id": execution_id},
        )

    # ── Phase 5: log failed tools (diagnostic only) ──────────────────
    try:
        failed_tools = collect_failed_tools(result)
        if failed_tools:
            logger_instance.debug(
                "ReactStrategy: tool failures detected in execution: %s",
                failed_tools,
                extra={"execution_id": execution_id},
            )
    except Exception:
        pass

# ------------------------------------------------------------------
# Exec fallback — graceful degradation when all retries are exhausted
# ------------------------------------------------------------------

def exec_fallback(
    exc:             Exception,
    user_query:      str,
    execution_id:    Optional[str],
    logger_instance: Any,
) -> Optional[Dict[str, Any]]:
    """Return a structured partial result when the agent fails unrecoverably.

    Instead of propagating an exception to the caller, returns a well-formed
    result dict that the UI can render as a degraded-mode response.

    Returns ``None`` for exception types that should still propagate
    (e.g. ``ValueError`` from bad config — those are programmer errors,
    not runtime failures worth swallowing).
    """
    # Don't swallow configuration/validation errors — those should surface.
    if isinstance(exc, (ValueError, TypeError, ImportError)):
        return None

    err_str = redact(str(exc))
    logger_instance.warning(
        "ReactStrategy: exec_fallback triggered — returning partial result. "
        "error=%s execution_id=%s",
        err_str, execution_id,
    )

    # Build a user-facing degraded answer.
    if "timeout" in err_str.lower():
        answer = (
            "The investigation could not be completed within the time limit. "
            "This may indicate the query requires too many tool calls or the "
            "target services are slow to respond. Please try a more specific "
            "query or retry during off-peak hours."
        )
    elif "context" in err_str.lower() and "window" in err_str.lower():
        answer = (
            "The investigation accumulated more information than the model "
            "context window can hold. Please narrow the query scope — for "
            "example, target a specific service or shorter time window."
        )
    else:
        answer = (
            f"The investigation could not be completed due to an unexpected error. "
            f"The on-call system recorded the failure for review. "
            f"Error reference: {err_str[:200]}"
        )

    return {
        "type":           "react",
        "user_query":     user_query,
        "final_answer":   answer,
        "messages":       [],
        "message_count":  0,
        "tool_calls":     [],
        "model":          "unknown",
        "provider":       "unknown",
        "fallback":       True,
        "fallback_error": err_str,
    }
