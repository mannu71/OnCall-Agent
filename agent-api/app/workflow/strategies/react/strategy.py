"""ReAct strategy orchestration — delegates to focused submodules."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.redact import redact
from app.core.supervisor import InvestigationSupervisor, SupervisorAction, SupervisorConfig
from app.workflow.execution_port import ExecutionPort
from app.workflow.llm_config import LLM_NODE_TYPES
from app.workflow.strategies.base import BaseStrategy
from app.workflow.strategies.react.agent_builder import build_agent
from app.workflow.strategies.react.agent_runner import execute_agent
from app.workflow.strategies.react.helpers import (
    build_recall_context,
    cap_context_block,
    estimate_confidence,
    looks_like_midthought,
)
from app.workflow.strategies.react.hitl import emit_hitl_pause, make_checkpointer
from app.workflow.strategies.react.learning import auto_learn, exec_fallback
from app.workflow.strategies.react.llm_factory import build_llm
from app.workflow.strategies.react.streaming import StreamCallback
from app.workflow.strategies.react.tool_setup import setup_tools
from app.workflow.strategies.react.workflow_config import (
    extract_agent_config,
    extract_cloudwatch_config,
    extract_code_analyzer_config,
    extract_tools_config,
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
            tools_config = extract_tools_config(workflow)
            cloudwatch_config = extract_cloudwatch_config(workflow)
            code_analyzer_config = extract_code_analyzer_config(workflow)

            recall_hits: int = 0
            augmented_query = user_query
            try:
                from app.services.knowledge_base import knowledge_base as _kb

                _issues = await _kb.search_known_issues(user_query, limit=3, threshold=0.65)
                _patterns = await _kb.search_similar_patterns(user_query, limit=3, threshold=0.65)
                if cloudwatch_config:
                    _lg_query = " ".join(cloudwatch_config.get("log_groups") or [])
                    if _lg_query:
                        _cw_pat = await _kb.search_similar_patterns(
                            _lg_query, limit=2, threshold=0.55,
                        )
                        seen = {p.get("id") for p in _patterns}
                        for _p in _cw_pat:
                            if _p.get("id") not in seen:
                                _patterns.append(_p)
                                seen.add(_p.get("id"))
                _skills = await _kb.recall_skills_for_agent(user_query, limit=3)
                recall_hits = len(_issues) + len(_patterns) + len(_skills)
                recall_block = build_recall_context(_issues, _patterns, _skills)
                if recall_block:
                    augmented_query = f"{recall_block}\n\n---\n\n{user_query}"
            except Exception as _recall_err:
                logger_instance.warning(
                    "ReactStrategy: KB recall failed (non-fatal): %s",
                    redact(str(_recall_err)),
                    extra={"execution_id": execution_id},
                )

            tools = await setup_tools(tools_config, mcp_manager, execution_id)

            _expired_creds_msg: Optional[str] = None

            if cloudwatch_config:
                try:
                    from app.core.aws_credentials import resolve_aws_credentials
                    from app.workflow.tools.cloudwatch_agent_tools import build_cloudwatch_agent_tools

                    cw_creds, cw_region = await resolve_aws_credentials(
                        aws_profile=cloudwatch_config.get("aws_profile"),
                        aws_region=cloudwatch_config.get("aws_region", "us-east-1"),
                    )

                    # Pre-flight: validate credentials before invoking the LLM.
                    # A cheap STS call costs nothing vs. a full agent loop.
                    try:
                        import boto3
                        from botocore.exceptions import ClientError as _BotoClientError
                        from app.core.thread_pools import run_in_aws_pool

                        _sts_kwargs: Dict[str, Any] = {"region_name": cw_region}
                        if cw_creds.get("aws_profile"):
                            _sts_session = boto3.Session(
                                profile_name=cw_creds["aws_profile"], region_name=cw_region
                            )
                            _sts_client = _sts_session.client("sts")
                        else:
                            if cw_creds.get("access_key_id"):
                                _sts_kwargs["aws_access_key_id"] = cw_creds["access_key_id"]
                                _sts_kwargs["aws_secret_access_key"] = cw_creds.get("secret_access_key", "")
                                if cw_creds.get("session_token"):
                                    _sts_kwargs["aws_session_token"] = cw_creds["session_token"]
                            _sts_client = boto3.client("sts", **_sts_kwargs)
                        await run_in_aws_pool(_sts_client.get_caller_identity)
                    except _BotoClientError as _sts_err:
                        _ec = _sts_err.response.get("Error", {}).get("Code", "")
                        if _ec == "ExpiredTokenException" or "ExpiredToken" in str(_sts_err):
                            _expired_creds_msg = (
                                "AWS credentials are expired. Please refresh your AWS session token "
                                "in the LLM configuration settings and retry the workflow."
                            )
                            logger_instance.warning(
                                "ReactStrategy: AWS credentials expired — aborting before LLM invocation (exec=%s)",
                                execution_id,
                                extra={"execution_id": execution_id},
                            )
                    except Exception as _sts_probe_err:
                        logger_instance.debug(
                            "ReactStrategy: STS credential pre-flight skipped (%s)",
                            redact(str(_sts_probe_err)),
                            extra={"execution_id": execution_id},
                        )

                    if not _expired_creds_msg:
                        tools.extend(
                            build_cloudwatch_agent_tools(
                                region=cw_region,
                                credentials=cw_creds,
                                log_groups=cloudwatch_config.get("log_groups"),
                                severity_excludes=cloudwatch_config.get("severity_excludes"),
                            )
                        )
                except Exception as _cw_err:
                    logger_instance.warning(
                        "ReactStrategy: failed to build CloudWatch tools (non-fatal): %s",
                        redact(str(_cw_err)),
                        extra={"execution_id": execution_id},
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

            if code_analyzer_config:
                try:
                    from app.workflow.tools.code_analyzer_tools import build_crawler_tools

                    tools.extend(
                        build_crawler_tools(repos=code_analyzer_config.get("repos"))
                    )
                except Exception as _cr_err:
                    logger_instance.warning(
                        "ReactStrategy: failed to build Crawler tools (non-fatal): %s",
                        redact(str(_cr_err)),
                        extra={"execution_id": execution_id},
                    )

            if tools and user_query:
                try:
                    from app.core.tools.router import ToolRouter

                    _keep_prefixes_env = os.environ.get(
                        "TOOL_ROUTER_ALWAYS_KEEP_PREFIXES",
                        "cloudwatch_,code_,db_,database_,sql_",
                    )
                    _keep_prefixes = tuple(
                        p.strip() for p in _keep_prefixes_env.split(",") if p.strip()
                    )
                    _top_k = int(os.environ.get("TOOL_ROUTER_TOP_K", "12"))
                    _mcp_tools = [
                        t for t in tools
                        if hasattr(t, "name")
                        and not any(t.name.startswith(p) for p in _keep_prefixes)
                    ]
                    _special_tools = [t for t in tools if t not in _mcp_tools]
                    if _mcp_tools:
                        _schemas = [
                            {"name": t.name, "description": getattr(t, "description", "")}
                            for t in _mcp_tools
                        ]
                        _filtered = ToolRouter(top_k=_top_k).filter(_schemas, query=user_query)
                        _allowed = {s["name"] for s in _filtered}
                        tools = [t for t in _mcp_tools if t.name in _allowed] + _special_tools
                except Exception as _tr_err:
                    logger_instance.warning(
                        "ReactStrategy: ToolRouter skipped (non-fatal): %s",
                        redact(str(_tr_err)),
                        extra={"execution_id": execution_id},
                    )

            cw_context = context.get("cloudwatch_context")
            # The deterministic pipeline's synthesis, kept uncapped as a guaranteed
            # floor for the final answer if the agent's own answer comes back empty
            # or truncated (see synthesis-as-floor fallback after the agent loop).
            cw_synthesis: str = ""
            if cw_context:
                block = cap_context_block("Pre-computed CloudWatch Analysis", cw_context)
                augmented_query = f"{block}{augmented_query}"
                try:
                    if isinstance(cw_context, dict):
                        for _entry in cw_context.values():
                            _out = (_entry or {}).get("output") if isinstance(_entry, dict) else None
                            if _out and len(str(_out)) > len(cw_synthesis):
                                cw_synthesis = str(_out)
                except Exception:  # noqa: BLE001 — fallback extraction is best-effort
                    cw_synthesis = ""

            code_analyzer_context = context.get("code_analyzer_context")
            if code_analyzer_context:
                block = cap_context_block("Pre-computed Code Analysis", code_analyzer_context)
                augmented_query = f"{block}{augmented_query}"

            anomaly_correlation = context.get("anomaly_code_correlation")
            if anomaly_correlation:
                block = cap_context_block("Anomaly-Code Correlation", anomaly_correlation)
                augmented_query = f"{block}{augmented_query}"

            llm = build_llm(llm_config)
            checkpointer = await make_checkpointer()
            agent = build_agent(
                llm,
                tools,
                agent_config,
                has_cloudwatch=bool(cloudwatch_config),
                has_code_analyzer=bool(code_analyzer_config),
                checkpointer=checkpointer,
                session_id=execution_id,
            )

            supervisor_enabled = agent_config.get("supervisor_enabled", True)
            supervisor = (
                InvestigationSupervisor(SupervisorConfig.from_settings())
                if supervisor_enabled
                else None
            )
            supervisor_retry_count = 0
            current_query = augmented_query
            result: Dict[str, Any] = {}
            _accum_input_tokens = 0
            _accum_output_tokens = 0
            _token_budget = supervisor._cfg.token_budget if supervisor else 0

            while True:
                result = await execute_agent(
                    agent,
                    current_query,
                    logger_instance,
                    execution_id,
                    stream_callback,
                    thread_id=execution_id,
                    execution_port=execution_port,
                )
                _accum_input_tokens += result.get("input_tokens", 0) or 0
                _accum_output_tokens += result.get("output_tokens", 0) or 0

                _final_answer = result.get("final_answer") or ""
                _confidence = estimate_confidence(_final_answer, result.get("tool_calls", []))

                if supervisor is None:
                    break

                _total_tokens = _accum_input_tokens + _accum_output_tokens
                if _token_budget and _total_tokens >= _token_budget:
                    logger_instance.warning(
                        "ReactStrategy: supervisor token budget exhausted "
                        "(%d >= %d) — stopping retry loop",
                        _total_tokens,
                        _token_budget,
                        extra={"execution_id": execution_id},
                    )
                    result["supervisor_token_budget_exhausted"] = True
                    break

                verdict = supervisor.evaluate(
                    final_answer=_final_answer,
                    tool_calls=result.get("tool_calls", []),
                    confidence=_confidence,
                    retry_count=supervisor_retry_count,
                )

                if verdict.action == SupervisorAction.PASS:
                    break

                if verdict.action == SupervisorAction.RETRY:
                    supervisor_retry_count += 1
                    current_query = (
                        f"{verdict.retry_guidance}\n\n---\n\nOriginal query:\n{augmented_query}"
                    )
                    agent = build_agent(
                        llm,
                        tools,
                        agent_config,
                        has_cloudwatch=bool(cloudwatch_config),
                        has_code_analyzer=bool(code_analyzer_config),
                        checkpointer=checkpointer,
                        session_id=execution_id,
                    )
                    continue

                if verdict.action == SupervisorAction.HITL:
                    await emit_hitl_pause(
                        execution_id,
                        {
                            "request_id": str(uuid.uuid4()),
                            "draft_answer": _final_answer,
                            "message": (
                                f"Supervisor quality score {verdict.score:.2f} — "
                                f"engineer review requested. {verdict.reason}"
                            ),
                        },
                        execution_port=execution_port,
                    )
                    break

                if verdict.action == SupervisorAction.ESCALATE:
                    result["supervisor_escalated"] = True
                    result["supervisor_reason"] = verdict.reason
                    break
                break

            # ── Synthesis-as-floor ──────────────────────────────────────────
            # The deterministic CloudWatch pipeline already produced a complete,
            # non-fragmentary synthesis (seeded above). If the agent's own answer
            # came back empty or was cut off mid-thought, return that synthesis
            # rather than a half-finished investigation ("Now let me search…").
            _final_text = (result.get("final_answer") or "").strip()
            # Treat provider refusals ("…cannot answer this question") as non-answers
            # too, so the agent never overrides the deterministic CloudWatch report
            # with a guardrail stub.
            try:
                from app.workflow.executor.cloudwatch_analysis import is_usable_synthesis
                _agent_unusable = not is_usable_synthesis(_final_text)
            except Exception:  # noqa: BLE001
                _agent_unusable = not _final_text
            # Be CONSERVATIVE: only fall back when the agent clearly failed to
            # produce a usable answer — empty, a provider refusal/stub, a real
            # token-limit truncation, or a SHORT mid-thought fragment. A long,
            # substantial narrative is never clobbered, even if it happens to
            # contain a phrase like "let me …" somewhere in its prose. (Previously
            # looks_like_midthought alone fired on complete reports and replaced
            # them with the raw deterministic report.)
            _short_fragment = looks_like_midthought(_final_text) and len(_final_text) < 400
            if cw_synthesis and (
                not _final_text
                or result.get("truncated")
                or _agent_unusable
                or _short_fragment
            ):
                logger_instance.info(
                    "ReactStrategy: agent answer empty/refusal/truncated/short-fragment "
                    "(len=%d) — falling back to pre-computed CloudWatch synthesis "
                    "(execution_id=%s)",
                    len(_final_text), execution_id,
                    extra={"execution_id": execution_id},
                )
                result["final_answer"] = cw_synthesis
                result["cloudwatch_synthesis_fallback"] = True
                result.pop("truncated", None)

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

            if mcp_manager:
                await mcp_manager.disconnect_all()

            return {
                "type": "react",
                "user_query": user_query,
                "final_answer": result.get("final_answer"),
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
