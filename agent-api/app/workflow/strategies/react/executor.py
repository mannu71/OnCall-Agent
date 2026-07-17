"""ReAct strategy — executor: build the agent + supervisor, run the bounded
supervised loop (run -> score -> retry/HITL/escalate), with a Bedrock
fallback-chain failover on throttles.

Split out of the former ``ReactStrategy.execute`` god-orchestrator (see
:mod:`preflight` for the setup half and :mod:`finalizer` for the post-run
half). This module is the SOLE engine-routing point: the actual agent turn
runs via ``app.harness.engine.run_agent_once``, which dispatches to either
the LangGraph ReAct agent (default) or the native turn loop, keyed off
``AGENT_ENGINE`` / the per-workflow ``engine`` override.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.core.quality.supervisor import InvestigationSupervisor, SupervisorConfig
from app.harness import build_agent_from_spec
from app.harness.engine import resolve_engine, run_agent_once
from app.harness.spec_factory import _as_bool
from app.harness.supervisor_loop import run_supervised
from app.workflow.execution_port import ExecutionPort
from app.workflow.strategies.react.llm_factory import build_llm
from app.workflow.strategies.react.preflight import RunPlan
from app.workflow.strategies.react.streaming import StreamCallback
from app.workflow.llm_config import (
    gather_alt_credentials,
    resolve_llm_fallback_chain,
    throttle_target_for,
)

logger = logging.getLogger(__name__)


async def run_plan(
    plan: RunPlan,
    *,
    context: Dict[str, Any],
    execution_id: Optional[str],
    logger_instance: Any,
    stream_callback: Optional[StreamCallback],
    execution_port: Optional[ExecutionPort],
) -> Tuple[Dict[str, Any], int, int, int, int]:
    """Run the supervised agent loop for ``plan``.

    Returns ``(result, accum_input_tokens, accum_output_tokens,
    accum_cache_read_tokens, accum_cache_creation_tokens)`` — the exact tuple
    shape ``ReactStrategy.execute`` used to unpack directly from
    ``run_supervised``.
    """
    spec = plan.spec
    llm = plan.llm
    tools = plan.tools
    checkpointer = plan.checkpointer
    agent_config = plan.agent_config
    llm_config = plan.llm_config

    def _rebuild_agent():
        return build_agent_from_spec(
            spec, llm, tools, checkpointer=checkpointer,
            execution_port=execution_port,
        )

    agent = _rebuild_agent()

    # Per-workflow autonomy: turning the supervisor OFF on the agent node
    # runs the agent fully autonomously (no scoring / HITL pause). Coerce
    # via _as_bool because the UI writes the string 'true'/'false', and a
    # bare ``'false'`` would otherwise be truthy. Check both the node data
    # and its ``params`` mirror (same as the autoLearn/sandbox toggles).
    _sup_params = agent_config.get("params") if isinstance(agent_config.get("params"), dict) else {}
    _sup_raw = agent_config.get("supervisor_enabled")
    if _sup_raw is None:
        _sup_raw = _sup_params.get("supervisor_enabled")
    supervisor_enabled = _as_bool(_sup_raw) if _sup_raw is not None else True
    supervisor_cfg = SupervisorConfig.from_settings()
    # Per-workflow grader override: agent_config["verification_grader"] or
    # deep_features["verification_grader"] wins over the global setting.
    # Null/absent → global default (False for existing workflows), EXCEPT that
    # an auto-learn workflow defaults LLM scoring ON: auto-learn persists
    # findings to the KB, so it should earn a real quality verdict (which also
    # feeds the durable-write gate in learning.auto_learn) rather than only the
    # heuristic confidence estimate. Cost stays bounded by supervisor_token_budget.
    # An explicit verification_grader value still overrides this default.
    _vg_raw = agent_config.get("verification_grader")
    if _vg_raw is None:
        _vg_raw = (agent_config.get("deep_features") or {}).get("verification_grader")
    if _vg_raw is not None:
        supervisor_cfg.llm_scoring_enabled = _as_bool(_vg_raw)
    elif getattr(spec, "auto_learn", False):
        supervisor_cfg.llm_scoring_enabled = True
    supervisor = (
        InvestigationSupervisor(supervisor_cfg)
        if supervisor_enabled
        else None
    )

    # ── Bedrock fallback-chain (Phase 1) ──────────────────────────
    # On a throttle, fail over to an alternate credential → region →
    # model instead of exhausting retries on the same target. The chain
    # is computed once per run; cooled targets (from a prior throttle
    # this process) are de-prioritised by the resolver.
    _alt_creds = await gather_alt_credentials(llm_config)
    _fallback_chain = resolve_llm_fallback_chain(
        llm_config, alt_credentials=_alt_creds
    )
    # When a chain exists, let throttles bubble out of the engine's
    # in-place retry immediately so the failover loop can switch target;
    # pure-transient errors (502/timeout) still retry in place.
    _retry_predicate = (
        (lambda ce: ce.retryable and not ce.should_fallback)
        if len(_fallback_chain) > 1 else None
    )

    _engine = resolve_engine(agent_config, context)

    async def _run_agent(_agent, _query):
        # plan.conversation_history is the tool-inclusive replay built in
        # preflight (app.harness.chat_history) — None for non-chat runs or
        # when the rebuild failed/was skipped, in which case this falls back
        # to the UI's text-only history exactly as before.
        _history = (
            plan.conversation_history
            if plan.conversation_history is not None
            else ((context.get("inputs") or {}).get("history") if isinstance(context, dict) else None)
        )
        last_exc: Optional[Exception] = None
        for _idx, _candidate in enumerate(_fallback_chain):
            if _idx == 0:
                _cur_llm = llm
            else:
                from app.core.llm import model_throttle_tracker as _throttle
                _tgt = throttle_target_for(_fallback_chain[_idx - 1])
                _throttle.mark_throttled(_tgt)
                logger_instance.warning(
                    "ReactStrategy: LLM target throttled (%s/%s/%s) — failing over "
                    "to candidate %d/%d (model=%s region=%s) [execution_id=%s]",
                    _tgt.provider, _tgt.region, _tgt.model_id,
                    _idx + 1, len(_fallback_chain),
                    _candidate.get("model"), _candidate.get("region"),
                    execution_id,
                )
                _cur_llm = build_llm(_candidate)
            try:
                return await run_agent_once(
                    spec,
                    _cur_llm,
                    tools,
                    _query,
                    logger_instance=logger_instance,
                    execution_id=execution_id,
                    stream_callback=stream_callback,
                    thread_id=execution_id,
                    execution_port=execution_port,
                    conversation_history=_history,
                    retry_predicate=_retry_predicate,
                    model_name=llm_config.get("model"),
                    checkpointer=checkpointer,
                    engine=_engine,
                )
            except Exception as _exc:  # noqa: BLE001 — decide failover vs raise
                from app.core.llm.error_classifier import classify_error as _classify
                _ce = _classify(_exc)
                if _ce.should_fallback and _idx < len(_fallback_chain) - 1:
                    last_exc = _exc
                    continue
                raise
        if last_exc is not None:
            raise last_exc

    # Bounded supervisor loop (run → score → retry/HITL/escalate) lives in
    # the harness now; it enforces iteration / wall-clock / token bounds.
    return await run_supervised(
        agent=agent,
        run_agent=_run_agent,
        rebuild_agent=_rebuild_agent,
        supervisor=supervisor,
        base_query=plan.augmented_query,
        execution_id=execution_id,
        logger_instance=logger_instance,
        wall_clock_budget=float(
            getattr(settings, "supervisor_wall_clock_seconds", 900.0)
        ),
        execution_port=execution_port,
    )


__all__ = ["run_plan"]
