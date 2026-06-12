"""ReAct strategy orchestration — delegates to focused submodules."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.redact import redact
from app.core.supervisor import InvestigationSupervisor, SupervisorAction, SupervisorConfig
from app.harness import harness
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
from app.workflow.llm_config import resolve_llm_config_for_consumer_port
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
            augmented_query, recall_hits = await build_recall_query(
                user_query=user_query,
                cloudwatch_config=cloudwatch_config,
                logger_instance=logger_instance,
                execution_id=execution_id,
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

            def _rebuild_agent():
                return harness.build_agent(
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

            async def _run_agent(_agent, _query):
                return await execute_agent(
                    _agent,
                    _query,
                    logger_instance,
                    execution_id,
                    stream_callback,
                    thread_id=execution_id,
                    execution_port=execution_port,
                    conversation_history=(
                        (context.get("inputs") or {}).get("history")
                        if isinstance(context, dict) else None
                    ),
                )

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

            # Post-run learning is best-effort and must never sink a successful
            # investigation. Any failure here is logged and swallowed.
            try:
                await auto_learn(
                    user_query, result, execution_id, execution_start, recall_hits, logger_instance
                )
            except Exception as _learn_call_err:
                logger_instance.warning(
                    "ReactStrategy: auto_learn failed (non-fatal): %s",
                    redact(str(_learn_call_err)),
                    extra={"execution_id": execution_id},
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
            structured_output = None
            if _output_mode == "structured" and (result.get("final_answer") or "").strip():
                try:
                    from app.workflow.strategies.react.output_schemas import InvestigationReport
                    _struct_llm = llm.with_structured_output(InvestigationReport)
                    _report = await _struct_llm.ainvoke(
                        "Convert the following code-investigation answer into the structured "
                        "report schema. Use ONLY facts present in the answer; cite file:line "
                        "evidence exactly as written; do not invent fields.\n\n"
                        f"User question:\n{user_query}\n\n"
                        f"Investigation answer:\n{result.get('final_answer')}"
                    )
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

            return {
                "type": "react",
                "user_query": user_query,
                "final_answer": result.get("final_answer"),
                "structured_output": structured_output,
                "output_mode": _output_mode,
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
