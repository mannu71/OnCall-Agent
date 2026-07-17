"""ReAct strategy orchestration — thin coordinator over the three phases of a
run: :mod:`preflight` (config resolution, action-space assembly, spec build),
:mod:`executor` (build the agent, run the bounded supervised loop, engine
routing), and :mod:`finalizer` (synthesis floor, auto-learn, trajectory save,
structured output, PII rehydration, result-envelope assembly).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.privacy.redact import redact
from app.workflow.execution_port import ExecutionPort
from app.workflow.llm_config import LLM_NODE_TYPES
from app.workflow.strategies.base import BaseStrategy
from app.workflow.strategies.react import executor, finalizer, preflight
from app.workflow.strategies.react.learning import exec_fallback
from app.workflow.strategies.react.streaming import StreamCallback
from app.workflow.strategies.react.workflow_config import extract_agent_config

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
            plan_or_early = await preflight.build_run_plan(
                workflow,
                context,
                execution_id=execution_id,
                logger_instance=logger_instance,
                user_query=user_query,
                mcp_manager=mcp_manager,
                stream_callback=stream_callback,
                execution_port=execution_port,
            )
            if isinstance(plan_or_early, preflight.EarlyReturn):
                return plan_or_early.result
            plan = plan_or_early

            result, accum_input_tokens, accum_output_tokens, accum_cache_read_tokens, accum_cache_creation_tokens = (
                await executor.run_plan(
                    plan,
                    context=context,
                    execution_id=execution_id,
                    logger_instance=logger_instance,
                    stream_callback=stream_callback,
                    execution_port=execution_port,
                )
            )

            return await finalizer.finalize(
                plan,
                result,
                (
                    accum_input_tokens, accum_output_tokens,
                    accum_cache_read_tokens, accum_cache_creation_tokens,
                ),
                workflow=workflow,
                context=context,
                execution_id=execution_id,
                logger_instance=logger_instance,
                mcp_manager=mcp_manager,
                execution_start=execution_start,
            )

        except Exception as error:
            logger_instance.error(
                "ReactStrategy: Execution failed",
                extra={"execution_id": execution_id, "error": redact(str(error))},
                exc_info=True,
            )
            await finalizer.cleanup_on_error(execution_id, mcp_manager)
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
