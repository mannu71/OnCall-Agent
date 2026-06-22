"""ReAct strategy orchestration — delegates to focused submodules."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core import privacy
from app.core.privacy.tool_wrap import wrap_tools_with_pseudonymization
from app.core.redact import redact
from app.core.supervisor import InvestigationSupervisor, SupervisorAction, SupervisorConfig
from app.harness import build_agent_from_spec
from app.harness.context_builder import (
    apply_synthesis_floor,
    build_recall_query,
    seed_context_blocks,
)
from app.harness.spec_factory import build_agent_spec
from app.harness.supervisor_loop import run_supervised
from app.harness.tool_assembler import add_extension_tools, assemble_base_tools
from app.workflow.execution_port import ExecutionPort
from app.workflow.llm_config import LLM_NODE_TYPES
from app.workflow.strategies.base import BaseStrategy
from app.workflow.strategies.react.agent_builder import build_agent
from app.workflow.strategies.react.agent_runner import execute_agent
from app.workflow.strategies.react.hitl import emit_hitl_pause, make_checkpointer
from app.workflow.strategies.react.learning import auto_learn, exec_fallback
from app.workflow.strategies.react.llm_factory import build_llm
from app.workflow.strategies.react.streaming import StreamCallback
from app.workflow.llm_config import (
    resolve_llm_config_for_consumer_port,
    resolve_llm_fallback_chain,
    gather_alt_credentials,
    throttle_target_for,
)
from app.workflow.strategies.react.workflow_config import (
    extract_agent_config,
    extract_cloudwatch_config,
    extract_code_analyzer_config,
    extract_tools_config,
    find_agent_node_id,
    find_code_analyzer_node_id,
    resolve_default_tools_config,
    resolve_llm_config_for_workflow,
)

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


class ReactStrategy(BaseStrategy):
    """Strategy for executing AI agent workflows using LangGraph ReAct pattern."""

    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return False
        has_agent_node = any(node.get("type") == "agent" for node in nodes)
        has_llm_node = any(node.get("type") in LLM_NODE_TYPES for node in nodes)
        return has_agent_node and has_llm_node

    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        execution_id = context.get("execution_id")
        logger_instance = context.get("logger", logger)
        user_query = context.get("user_query") or context.get("inputs", {}).get("user_query", "")
        mcp_manager = context.get("mcp_manager")
        stream_callback: Optional[StreamCallback] = context.get("stream_callback")
        execution_port: Optional[ExecutionPort] = context.get("execution_port")
        execution_start = datetime.now(timezone.utc)

        logger_instance.info(
            "ReactStrategy: Starting execution",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow.get("id"),
                "user_query_preview": (user_query or "")[:100],
                "streaming": stream_callback is not None,
            },
        )

        if not user_query:
            agent_config = extract_agent_config(workflow)
            user_query = agent_config.get("instructions") or agent_config.get("description") or ""

        if not user_query:
            raise ValueError(
                "ReactStrategy requires a user_query in the execution context "
                "or 'instructions' set on the agent node."
            )

        try:
            agent_config = extract_agent_config(workflow)
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
            llm_config = await resolve_llm_config_for_workflow(workflow)

            # Node-level gateway: resolve the models wired to the dedicated model
            # ports. The Code Crawler node carries the crawler model (its own
            # ``lm`` port); the agent node carries the subagent model. Both are
            # optional — when unwired they default to the agent's MAIN model so
            # the whole workflow runs on one model unless a cheaper one is wired.
            crawler_model_id: Optional[str] = llm_config.get("model")
            try:
                ca_node_id = find_code_analyzer_node_id(workflow)
                if ca_node_id:
                    _cw = await resolve_llm_config_for_consumer_port(
                        workflow, ca_node_id, "lm"
                    )
                    if _cw and _cw.get("model"):
                        crawler_model_id = _cw.get("model")
                        logger.info(
                            "ReactStrategy: crawler model from Code Crawler node = %s",
                            crawler_model_id,
                        )

                agent_node_id = find_agent_node_id(workflow)
                if agent_node_id:
                    _sub = await resolve_llm_config_for_consumer_port(
                        workflow, agent_node_id, "subagent"
                    )
                    if _sub:
                        # Stash the full resolved config (provider + creds) so the
                        # delegate tool can build a dedicated sub-LLM directly.
                        # Unwired → subagent.py falls back to the parent (main) LLM.
                        agent_config = {**agent_config, "subagent_llm_config": _sub}
                        logger.info(
                            "ReactStrategy: subagent model from wired port = %s",
                            _sub.get("model"),
                        )
            except Exception as exc:  # noqa: BLE001 — never break on resolution
                logger.warning(
                    "ReactStrategy: crawler/subagent port resolution failed (%s)",
                    exc,
                )

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

            # Prepend a knowledge-base recall block (similar past issues /
            # patterns / skills) so the agent starts with institutional memory.
            augmented_query, recall_hits, selected_skills = await build_recall_query(
                user_query=user_query,
                cloudwatch_config=cloudwatch_config,
                logger_instance=logger_instance,
                execution_id=execution_id,
                code_analyzer_config=code_analyzer_config,
            )

            # Assemble the base action space (MCP + CloudWatch + crawler + DB
            # schema tools, pruned by the relevance router) via the harness.
            tools, _expired_creds_msg = await assemble_base_tools(
                tools_config=tools_config,
                mcp_manager=mcp_manager,
                execution_id=execution_id,
                cloudwatch_config=cloudwatch_config,
                code_analyzer_config=code_analyzer_config,
                db_server_map=context.get("db_server_map") or {},
                user_query=user_query,
                logger_instance=logger_instance,
                crawler_model_id=crawler_model_id,
            )

            if _expired_creds_msg:
                return {
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
                }

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

            def _rebuild_agent():
                return build_agent_from_spec(
                    spec, llm, tools, checkpointer=checkpointer,
                    execution_port=execution_port,
                )

            agent = _rebuild_agent()

            supervisor_enabled = agent_config.get("supervisor_enabled", True)
            supervisor = (
                InvestigationSupervisor(SupervisorConfig.from_settings())
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
            # When a chain exists, let throttles bubble out of execute_agent's
            # in-place retry immediately so the failover loop can switch target;
            # pure-transient errors (502/timeout) still retry in place.
            _retry_predicate = (
                (lambda ce: ce.retryable and not ce.should_fallback)
                if len(_fallback_chain) > 1 else None
            )

            async def _run_agent(_agent, _query):
                _history = (
                    (context.get("inputs") or {}).get("history")
                    if isinstance(context, dict) else None
                )
                last_exc: Optional[Exception] = None
                for _idx, _candidate in enumerate(_fallback_chain):
                    if _idx == 0:
                        _cur = _agent
                    else:
                        from app.core import model_throttle_tracker as _throttle
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
                        _cur = build_agent_from_spec(
                            spec, build_llm(_candidate), tools,
                            checkpointer=checkpointer, execution_port=execution_port,
                        )
                    try:
                        return await execute_agent(
                            _cur,
                            _query,
                            logger_instance,
                            execution_id,
                            stream_callback,
                            thread_id=execution_id,
                            execution_port=execution_port,
                            conversation_history=_history,
                            retry_predicate=_retry_predicate,
                        )
                    except Exception as _exc:  # noqa: BLE001 — decide failover vs raise
                        from app.core.error_classifier import classify_error as _classify
                        _ce = _classify(_exc)
                        if _ce.should_fallback and _idx < len(_fallback_chain) - 1:
                            last_exc = _exc
                            continue
                        raise
                if last_exc is not None:
                    raise last_exc

            # Bounded supervisor loop (run → score → retry/HITL/escalate) lives in
            # the harness now; it enforces iteration / wall-clock / token bounds.
            result, _accum_input_tokens, _accum_output_tokens = await run_supervised(
                agent=agent,
                run_agent=_run_agent,
                rebuild_agent=_rebuild_agent,
                supervisor=supervisor,
                base_query=augmented_query,
                execution_id=execution_id,
                logger_instance=logger_instance,
                wall_clock_budget=float(
                    getattr(settings, "supervisor_wall_clock_seconds", 900.0)
                ),
                execution_port=execution_port,
            )

            # Synthesis-as-floor: if the agent's own answer is empty/refusal/
            # truncated/short-fragment, fall back to the deterministic CloudWatch
            # synthesis seeded above rather than returning a half-finished run.
            result = apply_synthesis_floor(
                result=result,
                cw_synthesis=cw_synthesis,
                logger_instance=logger_instance,
                execution_id=execution_id,
            )

            # Post-run learning is OPT-IN per workflow (agent node `autoLearn`).
            # Off by default so a run never writes to skills/memory unless the
            # workflow asked for it. ``spec.auto_learn`` already parsed the toggle
            # (string 'true'/'false') correctly. Best-effort — never sinks a run.
            if spec.auto_learn:
                try:
                    await auto_learn(
                        user_query, result, execution_id, execution_start, recall_hits,
                        logger_instance, code_analyzer_config=code_analyzer_config,
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

            # Deep-agent session scratch (todos + virtual FS). Capture the final
            # plan for the UI, then drop both stores so nothing outlives the run.
            _todos = []
            try:
                from app.workflow.strategies.react import planning_tools as _pl
                _todos = _pl.get_todos(execution_id)
                _pl.drop_session(execution_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                from app.core import vfs as _vfs
                _vfs.drop_session(execution_id)
            except Exception:  # noqa: BLE001
                pass

            return {
                "type": "react",
                "user_query": user_query,
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
                "input_tokens": _accum_input_tokens,
                "output_tokens": _accum_output_tokens,
                "total_tokens": _accum_input_tokens + _accum_output_tokens,
            }

        except Exception as error:
            logger_instance.error(
                "ReactStrategy: Execution failed",
                extra={"execution_id": execution_id, "error": redact(str(error))},
                exc_info=True,
            )
            if mcp_manager:
                try:
                    await mcp_manager.disconnect_all()
                except Exception:
                    pass
            # Drop the vault so raw PII never outlives a failed run.
            try:
                privacy.drop_vault(execution_id)
            except Exception:  # noqa: BLE001
                pass
            # Drop deep-agent session scratch (todos + virtual FS) too.
            try:
                from app.workflow.strategies.react import planning_tools as _pl
                _pl.drop_session(execution_id)
                from app.core import vfs as _vfs
                _vfs.drop_session(execution_id)
            except Exception:  # noqa: BLE001
                pass
            fallback = exec_fallback(error, user_query, execution_id, logger_instance)
            if fallback is not None:
                return fallback
            raise

    def validate_workflow(self, workflow: Dict[str, Any]) -> bool:
        super().validate_workflow(workflow)
        nodes = workflow.get("nodes", [])
        agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
        llm_node = next((n for n in nodes if n.get("type") in LLM_NODE_TYPES), None)
        if not agent_node:
            raise ValueError("ReAct workflow must have an agent node")
        if not llm_node:
            raise ValueError("ReAct workflow must have an LLM node")
        llm_data = llm_node.get("data", {})
        if not llm_data.get("model") and not llm_data.get("configName") and not llm_data.get("llmConfigId"):
            raise ValueError("LLM node must specify a model or reference an LLM config")
        return True
