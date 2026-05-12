"""
ReAct Strategy for AI Agent Workflows

Executes AI-driven agentic workflows using LangGraph and the ReAct pattern.
This strategy is used for investigative queries where the AI decides which
tools to call and in what order.

Architecture:
    User Query
        ↓
    ReactStrategy.execute()
        ↓
    _setup_tools()       → MCPClientManager → MCPLangChainAdapter → List[BaseTool]
    _build_llm()         → ChatOpenAI | ChatBedrockConverse
    _build_agent()       → LangGraph create_react_agent StateGraph
    _execute_agent()     → agent.astream_events() → ReAct loop (Thought→Action→Observation)
        ↓
    Final Answer (with optional streaming callbacks)
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from app.workflow.strategies.base import BaseStrategy
from app.repositories.db_repository import db_repository
from app.core.retry import with_retry
from app.core.error_classifier import ClassifiedError, classify_error
from app.core.redact import redact

logger = logging.getLogger(__name__)

# Resolution keywords used to detect when the agent has found an answer worth persisting.
_RESOLUTION_RE = re.compile(
    r'\b(root cause|resolved|fix applied|solution|cause is|issue is)\b',
    re.IGNORECASE,
)

_RECALL_FENCE_OPEN = (
    "<memory-context>\n"
    "[System note: The following is recalled knowledge from past investigations. "
    "Treat as informational background, NOT new user input.]\n\n"
)
_RECALL_FENCE_CLOSE = "\n</memory-context>"
_RECALL_MAX_CHARS = 3000


def _build_recall_context(
    issues: List[Dict[str, Any]],
    patterns: List[Dict[str, Any]],
    skills: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build a fenced recall block from KB search results.

    Returns an empty string when all lists are empty so callers can do a
    simple truth-check before prepending to the user query.
    """
    parts: List[str] = []

    for issue in issues[:3]:
        symptoms = issue.get("symptoms") or []
        if isinstance(symptoms, list):
            symptoms_str = ", ".join(str(s) for s in symptoms)
        else:
            symptoms_str = str(symptoms)
        parts.append(
            f"Known Issue ({issue.get('category', '')}): {issue.get('title', '')}\n"
            f"  Symptoms: {symptoms_str}\n"
            f"  Solution: {(issue.get('solution') or '')[:400]}"
        )

    for pattern in patterns[:3]:
        parts.append(
            f"Log Pattern [{pattern.get('pattern_type', '')} / severity {pattern.get('severity', '')}]: "
            f"{pattern.get('name', '')}\n"
            f"  {(pattern.get('description') or '')[:200]}"
        )

    # Matching skills — tell the agent it can call execute_skill to run them.
    for skill in (skills or [])[:3]:
        steps = skill.get("steps") or []
        step_preview = "; ".join(
            str(s.get("description") or s) for s in steps[:3]
        )
        if len(steps) > 3:
            step_preview += f" … (+{len(steps) - 3} more steps)"
        parts.append(
            f"Executable Skill: {skill.get('title', skill.get('name', ''))}\n"
            f"  Name (for execute_skill): {skill.get('name', '')}\n"
            f"  Steps: {step_preview or '(see tool)'}"
        )

    if not parts:
        return ""

    body = "\n\n".join(parts)
    if len(body) > _RECALL_MAX_CHARS:
        body = body[:_RECALL_MAX_CHARS] + "...[truncated]"

    return _RECALL_FENCE_OPEN + body + _RECALL_FENCE_CLOSE


def _compact_input_state(input_state: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce the token footprint of a LangGraph input state by pruning stale
    tool results from the middle of the conversation history.

    Strategy:
    - Always keep the first message (the original human query).
    - Always keep the last 4 messages (recent reasoning and answer).
    - Replace ToolMessage entries in the middle with a single HumanMessage
      summary notice so the model understands context was dropped.

    This is called only after a context-overflow error — it is a recovery path,
    not a routine pre-call step.
    """
    from langchain_core.messages import HumanMessage, ToolMessage

    messages = list(input_state.get("messages") or [])
    if len(messages) <= 6:
        # Too short to compact meaningfully.
        return input_state

    head = messages[:1]
    tail = messages[-4:]
    middle = messages[1:-4]

    # Drop ToolMessages from the middle (they tend to be very large).
    compacted_middle = [m for m in middle if not isinstance(m, ToolMessage)]
    notice = HumanMessage(
        content="[Context compacted: intermediate tool results omitted to fit context window. "
                "Continue from the information above.]"
    )

    new_messages = head + compacted_middle + [notice] + tail
    return {**input_state, "messages": new_messages}


_ERROR_TOOL_KEYWORDS = ("error", "fail", "exception")
_ERROR_CONTENT_PREFIXES = ("Error:", "Failed:", "Exception:")


def _tool_call_name_looks_failed(tc: Any) -> str:
    """Return the tool name if it looks like a failure indicator, else empty string."""
    if not isinstance(tc, dict):
        return ""
    name = str(tc.get("tool") or "")
    return name if name and any(kw in name.lower() for kw in _ERROR_TOOL_KEYWORDS) else ""


def _tool_msg_failed_id(msg: Any) -> str:
    """Return the tool_call_id if the message content starts with an error prefix."""
    if not isinstance(msg, dict) or msg.get("role") != "tool":
        return ""
    content = str(msg.get("content") or "").lstrip()
    return str(msg.get("tool_call_id") or "unknown_tool") if content.startswith(_ERROR_CONTENT_PREFIXES) else ""


def _collect_failed_tools(result: Dict[str, Any]) -> List[str]:
    """Extract names of tools that returned error responses from an agent result."""
    seen: set = set()
    for tc in result.get("tool_calls") or []:
        name = _tool_call_name_looks_failed(tc)
        if name:
            seen.add(name)
    for msg in result.get("messages") or []:
        tool_id = _tool_msg_failed_id(msg)
        if tool_id:
            seen.add(tool_id)
    return list(seen)


class StreamCallback(Protocol):
    async def on_llm_token(self, token: str) -> None: ...
    async def on_tool_call(self, tool_name: str, args: dict) -> None: ...
    async def on_tool_result(self, tool_name: str, result: str) -> None: ...
    async def on_error(self, error: str) -> None: ...
    async def on_complete(self, output: str) -> None: ...


class ReactStrategy(BaseStrategy):
    """
    Strategy for executing AI agent workflows using LangGraph ReAct pattern.

    Use cases:
    - "Why did profiles fail today?"
    - "What's causing high CPU usage?"
    - "Investigate database connection issues"
    """

    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        """
        Check if workflow is a ReAct agent workflow.

        A workflow is considered a ReAct workflow if it has:
        - An 'agent' node (defines agent behaviour and instructions)
        - An 'llm' node  (provides AI model configuration)

        Args:
            workflow: The workflow definition

        Returns:
            True if this is a ReAct workflow
        """
        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return False

        has_agent_node = any(node.get("type") == "agent" for node in nodes)
        has_llm_node = any(node.get("type") == "llm" for node in nodes)

        return has_agent_node and has_llm_node

    # ------------------------------------------------------------------
    # Main execute entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute ReAct agent workflow.

        Args:
            workflow: The workflow definition
            context: Execution context with user_query, mcp_manager, etc.

        Returns:
            Execution result including final_answer and message trace.
        """
        execution_id = context.get("execution_id")
        logger_instance = context.get("logger", logger)
        user_query = context.get("user_query") or context.get("inputs", {}).get("user_query", "")
        mcp_manager = context.get("mcp_manager")
        stream_callback: Optional[StreamCallback] = context.get("stream_callback")
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
            agent_config = self._extract_agent_config(workflow)
            user_query = agent_config.get("instructions") or agent_config.get("description") or ""

        if not user_query:
            raise ValueError(
                "ReactStrategy requires a user_query in the execution context "
                "or 'instructions' set on the agent node."
            )

        try:
            agent_config = self._extract_agent_config(workflow)
            llm_config = await self._resolve_llm_config(workflow)
            tools_config = self._extract_tools_config(workflow)
            cloudwatch_config = self._extract_cloudwatch_config(workflow)
            code_analyzer_config = self._extract_code_analyzer_config(workflow)

            # ------------------------------------------------------------------
            # Pre-execution recall: inject relevant past knowledge into the query.
            # Failures here must never block the agent run.
            # ------------------------------------------------------------------
            recall_hits: int = 0
            augmented_query = user_query
            try:
                from app.services.knowledge_base import knowledge_base as _kb
                _issues = await _kb.search_known_issues(user_query, limit=3, threshold=0.65)
                _patterns = await _kb.search_similar_patterns(user_query, limit=3, threshold=0.65)
                _skills = await _kb.recall_skills_for_agent(user_query, limit=3)
                recall_hits = len(_issues) + len(_patterns) + len(_skills)
                recall_block = _build_recall_context(_issues, _patterns, _skills)
                if recall_block:
                    augmented_query = f"{recall_block}\n\n---\n\n{user_query}"
                    logger_instance.debug(
                        "ReactStrategy: prepended %d recall item(s) (%d skills) to query",
                        recall_hits,
                        len(_skills),
                        extra={"execution_id": execution_id},
                    )
            except Exception as _recall_err:
                logger_instance.warning(
                    "ReactStrategy: KB recall failed (non-fatal): %s",
                    redact(str(_recall_err)),
                    extra={"execution_id": execution_id},
                )

            tools = await self._setup_tools(tools_config, mcp_manager, execution_id)

            # ------------------------------------------------------------------
            # CloudWatch tools: only injected when a cloudwatchAnalyzer node is
            # connected to the agent node via workflow edges.
            # ------------------------------------------------------------------
            if cloudwatch_config:
                try:
                    from app.core.aws_credentials import resolve_aws_credentials
                    from app.workflow.tools.cloudwatch_agent_tools import build_cloudwatch_agent_tools

                    cw_creds, cw_region = await resolve_aws_credentials(
                        aws_profile=cloudwatch_config.get("aws_profile"),
                        aws_region=cloudwatch_config.get("aws_region", "us-east-1"),
                    )
                    cw_tools = build_cloudwatch_agent_tools(
                        region=cw_region,
                        credentials=cw_creds,
                        log_groups=cloudwatch_config.get("log_groups"),
                    )
                    tools.extend(cw_tools)
                    logger_instance.info(
                        "ReactStrategy: added %d CloudWatch tools to agent",
                        len(cw_tools),
                        extra={"execution_id": execution_id},
                    )
                except Exception as _cw_err:
                    logger_instance.warning(
                        "ReactStrategy: failed to build CloudWatch tools (non-fatal): %s",
                        redact(str(_cw_err)),
                        extra={"execution_id": execution_id},
                    )

            # ------------------------------------------------------------------
            # Code Analyzer tools: only injected when a codeAnalyzer node is
            # connected to the agent node via workflow edges.
            # ------------------------------------------------------------------
            if code_analyzer_config:
                try:
                    from app.workflow.tools.code_analyzer_tools import build_code_analyzer_tools
                    ca_tools = build_code_analyzer_tools(
                        repos=code_analyzer_config.get("repos"),
                    )
                    tools.extend(ca_tools)
                    logger_instance.info(
                        "ReactStrategy: added %d Code Analyzer tools to agent",
                        len(ca_tools),
                        extra={"execution_id": execution_id},
                    )
                except Exception as _ca_err:
                    logger_instance.warning(
                        "ReactStrategy: failed to build Code Analyzer tools (non-fatal): %s",
                        redact(str(_ca_err)),
                        extra={"execution_id": execution_id},
                    )

            # ------------------------------------------------------------------
            # Cross-node data: inject upstream CloudWatch results into context.
            # ------------------------------------------------------------------
            cw_context = context.get("cloudwatch_context")
            if cw_context:
                import json
                cw_summary = json.dumps(cw_context, indent=2, default=str)
                augmented_query = (
                    f"[Pre-computed CloudWatch Analysis]\n{cw_summary}"
                    f"\n\n---\n\n{augmented_query}"
                )
                logger_instance.info(
                    "ReactStrategy: injected CloudWatch context (%d chars) into query",
                    len(cw_summary),
                    extra={"execution_id": execution_id},
                )

            # ------------------------------------------------------------------
            # Cross-node data: inject upstream Code Analyzer results into context.
            # ------------------------------------------------------------------
            code_analyzer_context = context.get("code_analyzer_context")
            if code_analyzer_context:
                import json as _json
                ca_summary = _json.dumps(code_analyzer_context, indent=2, default=str)
                augmented_query = (
                    f"[Pre-computed Code Analysis]\n{ca_summary}"
                    f"\n\n---\n\n{augmented_query}"
                )
                logger_instance.info(
                    "ReactStrategy: injected Code Analyzer context (%d chars) into query",
                    len(ca_summary),
                    extra={"execution_id": execution_id},
                )

            # Anomaly-code correlation from visual_workflow_executor
            anomaly_correlation = context.get("anomaly_code_correlation")
            if anomaly_correlation:
                import json as _json2
                corr_text = _json2.dumps(anomaly_correlation, indent=2, default=str)
                augmented_query = (
                    f"[Anomaly-Code Correlation]\n{corr_text}"
                    f"\n\n---\n\n{augmented_query}"
                )

            llm = self._build_llm(llm_config)

            checkpointer = await self._make_checkpointer()

            agent = self._build_agent(
                llm, tools, agent_config,
                has_cloudwatch=bool(cloudwatch_config),
                has_code_analyzer=bool(code_analyzer_config),
                checkpointer=checkpointer,
            )

            # ------------------------------------------------------------------
            # Supervisor retry loop
            # Each pass: run agent → supervisor evaluates → route accordingly.
            # supervisor_enabled defaults True; set to False on the agent node
            # to bypass entirely (e.g. for quick ad-hoc queries).
            # ------------------------------------------------------------------
            from app.core.supervisor import (
                InvestigationSupervisor,
                SupervisorConfig,
                SupervisorAction,
            )
            supervisor_enabled = agent_config.get("supervisor_enabled", True)
            supervisor = InvestigationSupervisor(SupervisorConfig()) if supervisor_enabled else None
            supervisor_retry_count = 0
            current_query = augmented_query
            result: Dict[str, Any] = {}

            while True:
                result = await self._execute_agent(
                    agent, current_query, logger_instance, execution_id,
                    stream_callback, thread_id=execution_id,
                )

                # Derive confidence from auto-learn heuristic for supervisor input.
                _final_answer = result.get("final_answer") or ""
                _confidence = 0.90 if _RESOLUTION_RE.search(_final_answer) else 0.55

                if supervisor is None:
                    # Supervisor disabled — always pass.
                    break

                verdict = supervisor.evaluate(
                    final_answer = _final_answer,
                    tool_calls   = result.get("tool_calls", []),
                    confidence   = _confidence,
                    retry_count  = supervisor_retry_count,
                )

                logger_instance.info(
                    "ReactStrategy: supervisor verdict=%s score=%.2f reason=%s",
                    verdict.action.value, verdict.score, verdict.reason,
                    extra={"execution_id": execution_id},
                )

                if verdict.action == SupervisorAction.PASS:
                    break

                if verdict.action == SupervisorAction.RETRY:
                    supervisor_retry_count += 1
                    # Prepend corrective guidance so the agent knows what to fix.
                    current_query = (
                        f"{verdict.retry_guidance}\n\n"
                        f"---\n\nOriginal query:\n{augmented_query}"
                    )
                    logger_instance.info(
                        "ReactStrategy: supervisor RETRY %d — rebuilding agent",
                        supervisor_retry_count,
                        extra={"execution_id": execution_id},
                    )
                    # Rebuild agent with same config for a clean retry.
                    agent = self._build_agent(
                        llm, tools, agent_config,
                        has_cloudwatch=bool(cloudwatch_config),
                        has_code_analyzer=bool(code_analyzer_config),
                        checkpointer=checkpointer,
                    )
                    continue

                if verdict.action == SupervisorAction.HITL:
                    logger_instance.info(
                        "ReactStrategy: supervisor → HITL (score=%.2f)",
                        verdict.score,
                        extra={"execution_id": execution_id},
                    )
                    await self._emit_hitl_pause(
                        execution_id,
                        {
                            "request_id":   str(uuid.uuid4()),
                            "draft_answer": _final_answer,
                            "message":      (
                                f"Supervisor quality score {verdict.score:.2f} — "
                                f"engineer review requested. {verdict.reason}"
                            ),
                        },
                    )
                    break

                if verdict.action == SupervisorAction.ESCALATE:
                    logger_instance.warning(
                        "ReactStrategy: supervisor → ESCALATE (score=%.2f)",
                        verdict.score,
                        extra={"execution_id": execution_id},
                    )
                    result["supervisor_escalated"] = True
                    result["supervisor_reason"]    = verdict.reason
                    break

                # Unexpected action — pass through.
                break

            # ------------------------------------------------------------------
            # Post-execution learning: persist what the agent found.
            # Failures here must never break result delivery.
            # ------------------------------------------------------------------
            await self._auto_learn(
                user_query, result, execution_id, execution_start, recall_hits, logger_instance
            )

            if mcp_manager:
                await mcp_manager.disconnect_all()

            logger_instance.info(
                "ReactStrategy: Execution completed",
                extra={
                    "execution_id": execution_id,
                    "message_count": len(result.get("messages", [])),
                },
            )

            return {
                "type":         "react",
                "user_query":   user_query,
                "final_answer": result.get("final_answer"),
                "messages":     result.get("messages", []),
                "message_count": len(result.get("messages", [])),
                "tool_calls":   result.get("tool_calls", []),
                "model":        llm_config.get("model", "unknown"),
                "provider":     llm_config.get("provider", "unknown"),
                "supervisor_escalated": result.get("supervisor_escalated", False),
                "supervisor_reason":    result.get("supervisor_reason"),
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

            # ── exec_fallback — return partial result instead of crashing ──
            fallback = self._exec_fallback(error, user_query, execution_id, logger_instance)
            if fallback is not None:
                return fallback
            raise

    # ------------------------------------------------------------------
    # Post-execution learning
    # ------------------------------------------------------------------

    async def _auto_learn(
        self,
        user_query: str,
        result: Dict[str, Any],
        execution_id: Optional[str],
        execution_start: "datetime",
        recall_hits: int,
        logger_instance: Any,
    ) -> None:
        """Persist what this execution found to the knowledge base.

        Phase 1 — record_analysis (lightweight, always runs).
        Phase 2 — AutoLearnService.learn() (closed learning loop):
            • auto-approve gate (confidence threshold)
            • KB upsert + pattern bump
            • trajectory JSONL
            • skill distillation via LLM when enough tool calls exist

        Any failure in either phase is caught and logged as a warning —
        it must never propagate to the caller.
        """
        final_answer = result.get("final_answer") or ""
        execution_end = datetime.now(timezone.utc)

        # ── Phase 1: lightweight record_analysis ─────────────────────────
        try:
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

        # ── Phase 2: closed learning loop (AutoLearnService) ─────────────
        try:
            from app.core.auto_learn import AutoLearnService, AutoLearnConfig

            _config = AutoLearnConfig()
            _svc = AutoLearnService(_config)

            # Derive a rough confidence score: high when a resolution keyword
            # was found, moderate otherwise.  The AutoLearnService gate uses
            # this to decide whether to auto-approve.
            _has_resolution = bool(final_answer and _RESOLUTION_RE.search(final_answer))
            _confidence = 0.90 if _has_resolution else 0.55

            # Build the state dict AutoLearnService expects.
            _learn_state = {
                "execution_id": execution_id,
                "user_query": user_query,
                "final_answer": final_answer,
                "tool_calls": result.get("tool_calls", []),
                "confidence_score": _confidence,
                "recall_hits": recall_hits,
                "execution_start": execution_start.isoformat(),
                "execution_end": execution_end.isoformat(),
            }

            _learn_result = await _svc.learn(
                execution_id=str(execution_id or "unknown"),
                state=_learn_state,
            )

            logger_instance.info(
                "ReactStrategy: AutoLearnService completed — "
                "kb=%s pattern=%s trajectory=%s skill=%s skipped=%s",
                _learn_result.kb_upserted,
                _learn_result.pattern_bumped,
                _learn_result.trajectory_saved,
                _learn_result.skill_distilled,
                _learn_result.skipped_reason or "none",
                extra={"execution_id": execution_id},
            )

        except Exception as _learn_err:
            logger_instance.warning(
                "ReactStrategy: AutoLearnService.learn failed (non-fatal): %s",
                redact(str(_learn_err)),
                extra={"execution_id": execution_id},
            )

        # ── Phase 3: log failed tools (diagnostic only) ──────────────────
        try:
            failed_tools = _collect_failed_tools(result)
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

    @staticmethod
    def _exec_fallback(
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

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workflow(self, workflow: Dict[str, Any]) -> bool:
        """
        Validate ReAct workflow structure.

        Args:
            workflow: The workflow definition

        Returns:
            True if valid

        Raises:
            ValueError: If workflow is invalid
        """
        super().validate_workflow(workflow)

        nodes = workflow.get("nodes", [])
        agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
        llm_node = next((n for n in nodes if n.get("type") == "llm"), None)

        if not agent_node:
            raise ValueError("ReAct workflow must have an agent node")

        if not llm_node:
            raise ValueError("ReAct workflow must have an LLM node")

        # LLM node must reference a valid config or have inline config
        llm_data = llm_node.get("data", {})
        if not llm_data.get("model") and not llm_data.get("configName") and not llm_data.get("llmConfigId"):
            raise ValueError("LLM node must specify a model or reference an LLM config")

        return True

    # ------------------------------------------------------------------
    # Config extraction helpers
    # ------------------------------------------------------------------

    def _extract_agent_config(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Extract agent node configuration from workflow."""
        nodes = workflow.get("nodes", [])
        agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
        return agent_node.get("data", {}) if agent_node else {}

    async def _resolve_llm_config(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve LLM configuration from DB-stored configs or inline node data.

        Priority order:
        1. Inline config in LLM node data (model + provider set directly)
        2. Named config reference (configName / llmConfigId) → lookup in DB
        3. First available config in DB

        If the resolved config has no api_key, falls back to Model Keys for
        the matching provider.
        """
        nodes = workflow.get("nodes", [])
        llm_node = next((n for n in nodes if n.get("type") == "llm"), None)
        llm_data = llm_node.get("data", {}) if llm_node else {}

        resolved = None

        if llm_data.get("model") and llm_data.get("provider"):
            resolved = {
                "provider": llm_data["provider"],
                "model": llm_data["model"],
                "temperature": llm_data.get("temperature", 0.1),
                "max_tokens": llm_data.get("maxTokens") or llm_data.get("max_tokens") or 4096,
                "region": llm_data.get("region", "us-east-1"),
                "base_url": llm_data.get("baseUrl") or llm_data.get("base_url"),
            }

        if not resolved:
            config_name = llm_data.get("configName") or llm_data.get("llmConfigId")
            if config_name:
                try:
                    cfg = await db_repository.get_llm_config(config_name)
                    if cfg:
                        resolved = {
                            "provider": cfg["provider"],
                            "model": cfg["model"],
                            "temperature": cfg.get("temperature", 0.1),
                            "max_tokens": cfg.get("max_tokens", 4096),
                            "region": cfg.get("region", "us-east-1"),
                            "base_url": cfg.get("base_url"),
                        }
                except Exception as e:
                    logger.warning("Could not load LLM config '%s' from DB: %s", config_name, e)

        if not resolved:
            try:
                db_configs = await db_repository.list_llm_configs()
                if db_configs:
                    first_name, cfg = next(iter(db_configs.items()))
                    logger.info("ReactStrategy: using first available LLM config '%s'", first_name)
                    resolved = {
                        "provider": cfg["provider"],
                        "model": cfg["model"],
                        "temperature": cfg.get("temperature", 0.1),
                        "max_tokens": cfg.get("max_tokens", 4096),
                        "region": cfg.get("region", "us-east-1"),
                        "base_url": cfg.get("base_url"),
                    }
            except Exception as e:
                logger.warning("Could not load LLM configs from DB: %s", e)

        if not resolved:
            raise ValueError(
                "No LLM configuration available. Configure an LLM in Settings or "
                "add an LLM node to the workflow."
            )

        if not resolved.get("api_key") and resolved.get("provider", "").lower() not in ("bedrock", "aws", "aws_bedrock", "aws bedrock", "ollama"):
            try:
                mk = await db_repository.get_model_key(resolved["provider"], include_secrets=True)
                if mk:
                    if mk.get("api_key"):
                        resolved["api_key"] = mk["api_key"]
                    if mk.get("endpoint") and not resolved.get("base_url"):
                        resolved["base_url"] = mk["endpoint"]
                    if mk.get("region") and (not resolved.get("region") or resolved["region"] == "us-east-1"):
                        resolved["region"] = mk["region"]
            except Exception as e:
                logger.warning("Could not look up Model Key for provider '%s': %s", resolved.get("provider"), e)

        # For Bedrock, look up AWS credentials from model_keys table
        if resolved.get("provider", "").lower() in ("bedrock", "aws", "aws_bedrock", "aws bedrock"):
            try:
                for bedrock_key in ("AWS Bedrock", "bedrock", "aws bedrock", "aws"):
                    mk = await db_repository.get_model_key(bedrock_key, include_secrets=True)
                    if mk:
                        if mk.get("access_key_id"):
                            resolved["access_key_id"] = mk["access_key_id"]
                        if mk.get("secret_access_key"):
                            resolved["secret_access_key"] = mk["secret_access_key"]
                        if mk.get("session_token"):
                            resolved["session_token"] = mk["session_token"]
                        if mk.get("region") and (not resolved.get("region") or resolved["region"] == "us-east-1"):
                            resolved["region"] = mk["region"]
                        break
            except Exception as e:
                logger.warning("Could not look up Model Key for Bedrock: %s", e)

        return resolved

    @staticmethod
    def _get_connected_node_ids(
        workflow: Dict[str, Any],
        target_type: str,
    ) -> List[str]:
        """Return IDs of nodes of *target_type* connected to any ``agent`` node.

        Uses undirected BFS across the workflow edges so that connection is
        detected regardless of edge direction.  This is the single source of
        truth for "is this node wired to the agent?".

        Args:
            workflow: Full workflow definition (nodes + edges).
            target_type: The ``type`` value to look for (e.g. ``"tool"``,
                ``"cloudwatchAnalyzer"``).

        Returns:
            List of node IDs of type *target_type* reachable from at least one
            agent node.
        """
        nodes = workflow.get("nodes", [])
        edges = workflow.get("edges", [])

        agent_ids = {n["id"] for n in nodes if n.get("type") == "agent"}
        target_ids = {n["id"] for n in nodes if n.get("type") == target_type}

        if not agent_ids or not target_ids:
            return []

        # Build undirected adjacency.
        neighbours: Dict[str, set] = {}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src and tgt:
                neighbours.setdefault(src, set()).add(tgt)
                neighbours.setdefault(tgt, set()).add(src)

        # BFS from every agent node.
        visited: set = set()
        queue = list(agent_ids)
        connected: List[str] = []

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current in target_ids:
                connected.append(current)
            for neighbour in neighbours.get(current, set()):
                if neighbour not in visited:
                    queue.append(neighbour)

        return connected

    def _extract_tools_config(self, workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract tool node configurations from workflow.

        Only tool nodes connected to the agent via edges are included.
        This prevents stray/unconnected tool nodes from being registered
        on the agent.
        """
        nodes = workflow.get("nodes", [])
        connected_ids = set(self._get_connected_node_ids(workflow, "tool"))

        tool_nodes = [
            n for n in nodes
            if n.get("type") == "tool" and n.get("id") in connected_ids
        ]

        if not tool_nodes:
            # Backwards-compat: if the graph has no edges at all (e.g. a
            # minimal/legacy workflow), fall back to including all tool nodes
            # so existing workflows don't break silently.
            edges = workflow.get("edges", [])
            if not edges:
                tool_nodes = [n for n in nodes if n.get("type") == "tool"]
                if tool_nodes:
                    logger.info(
                        "ReactStrategy: no edges in workflow — falling back to all %d tool node(s)",
                        len(tool_nodes),
                    )

        tools = []
        for tool_node in tool_nodes:
            tool_data = tool_node.get("data", {})
            server_name = tool_data.get("serverName", "")
            node_id = tool_node.get("id", server_name)

            if tool_data.get("command"):
                tools.append({
                    "node_id": node_id,
                    "name": server_name,
                    "command": tool_data.get("command", ""),
                    "args": tool_data.get("args", []),
                    "env": tool_data.get("env", {}),
                })
            else:
                # Will be resolved from DB below in _setup_tools
                tools.append({
                    "node_id": node_id,
                    "name": server_name,
                    "command": None,
                    "args": [],
                    "env": {},
                })

        return tools

    def _extract_cloudwatch_config(
        self, workflow: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Extract CloudWatch config from CW nodes connected to the agent node.

        Only returns a config when a ``cloudwatchAnalyzer`` node is reachable
        from (connected to) an ``agent`` node via the workflow's edges.  This
        ensures CW tools are **only** registered when explicitly wired up.

        Returns:
            Merged CloudWatch config dict, or ``None`` if no CW node is
            connected to the agent.
        """
        nodes = workflow.get("nodes", [])
        connected_cw_ids = self._get_connected_node_ids(workflow, "cloudwatchAnalyzer")

        if not connected_cw_ids:
            return None

        cw_nodes = {n["id"]: n for n in nodes if n.get("type") == "cloudwatchAnalyzer"}

        # Merge configs from all connected CW nodes.
        merged_log_groups: List[str] = []
        merged_region = "us-east-1"
        merged_profile: Optional[str] = None

        for cw_id in connected_cw_ids:
            cw_data = cw_nodes[cw_id].get("data", {})
            for lg in cw_data.get("logGroups", []):
                if lg and lg not in merged_log_groups:
                    merged_log_groups.append(lg)
            if cw_data.get("awsRegion"):
                merged_region = cw_data["awsRegion"]
            if cw_data.get("awsProfile"):
                merged_profile = cw_data["awsProfile"]

        logger.info(
            "ReactStrategy: %d cloudwatchAnalyzer node(s) connected to agent, "
            "log_groups=%s",
            len(connected_cw_ids),
            merged_log_groups,
        )

        return {
            "log_groups": merged_log_groups,
            "aws_region": merged_region,
            "aws_profile": merged_profile,
        }

    def _extract_code_analyzer_config(
        self, workflow: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Extract Code Analyzer config from codeAnalyzer nodes connected to the agent.

        Merges repo lists from all connected ``codeAnalyzer`` nodes, deduplicating
        by repo name.  First occurrence (BFS order) wins on name conflicts.

        Returns:
            Dict with ``repos`` list, or ``None`` if no codeAnalyzer node is connected.
        """
        nodes = workflow.get("nodes", [])
        connected_ids = self._get_connected_node_ids(workflow, "codeAnalyzer")

        if not connected_ids:
            return None

        ca_nodes = {n["id"]: n for n in nodes if n.get("type") == "codeAnalyzer"}

        merged_repos: List[Dict[str, Any]] = []
        seen_names: Dict[str, str] = {}  # name → first path (for conflict detection)

        for ca_id in connected_ids:
            if ca_id not in ca_nodes:
                continue
            ca_data = ca_nodes[ca_id].get("data", {})
            for repo in ca_data.get("repos", []):
                rname = repo.get("name", "")
                rpath = repo.get("path", "")
                if not rname:
                    continue
                if rname in seen_names:
                    if seen_names[rname] != rpath:
                        logger.warning(
                            "ReactStrategy: duplicate repo name %r in codeAnalyzer nodes "
                            "— first occurrence (path=%r) wins; ignoring path=%r",
                            rname, seen_names[rname], rpath,
                        )
                    continue
                seen_names[rname] = rpath
                merged_repos.append(repo)

        if not merged_repos:
            return None

        logger.info(
            "ReactStrategy: %d codeAnalyzer node(s) connected to agent, repos=%s",
            len(connected_ids),
            [r.get("name") for r in merged_repos],
        )

        return {"repos": merged_repos}

    # ------------------------------------------------------------------
    # Tool setup (MCP → LangChain)
    # ------------------------------------------------------------------

    async def _setup_tools(
        self,
        tools_config: List[Dict[str, Any]],
        mcp_manager: Any,
        execution_id: Optional[str] = None,
    ) -> List[Any]:
        """
        Connect to MCP servers and convert their tools to LangChain BaseTool instances.

        Args:
            tools_config: List of tool node configurations from the workflow.
            mcp_manager: Live MCPClientManager instance.
            execution_id: Execution ID for logging.

        Returns:
            List of LangChain-compatible tool objects.
        """
        from app.workflow.mcp.mcp_langchain_adapter import build_langchain_tools

        for tool_config in tools_config:
            server_name = tool_config["name"]
            node_id = tool_config.get("node_id", server_name)

            # Resolve command from DB if not inline
            if not tool_config.get("command"):
                db_config = await db_repository.get_mcp_server_by_name(server_name)
                if db_config:
                    tool_config["command"] = db_config.get("command", "")
                    tool_config["args"] = db_config.get("args", [])
                    tool_config["env"] = db_config.get("env", {})
                    logger.info(
                        "ReactStrategy: loaded MCP server '%s' from DB",
                        server_name,
                        extra={"execution_id": execution_id},
                    )
                else:
                    logger.warning(
                        "ReactStrategy: MCP server '%s' not found in DB, skipping",
                        server_name,
                    )
                    continue

            # Connect (skip if already connected from a previous tool node)
            if not mcp_manager.is_connected(node_id):
                mcp_config = {
                    "command": tool_config["command"],
                    "args": tool_config["args"],
                    "env": tool_config["env"],
                }
                connected = await mcp_manager.connect_server(node_id, mcp_config)
                if not connected:
                    logger.warning(
                        "ReactStrategy: failed to connect MCP server '%s', skipping",
                        server_name,
                    )
                    continue

        # Convert all live MCP connections to LangChain tools
        langchain_tools = build_langchain_tools(mcp_manager)
        logger.info(
            "ReactStrategy: built %d LangChain tools",
            len(langchain_tools),
            extra={"execution_id": execution_id},
        )
        return langchain_tools

    # ------------------------------------------------------------------
    # LLM factory
    # ------------------------------------------------------------------

    def _build_llm(self, llm_config: Dict[str, Any]) -> Any:
        """
        Instantiate a LangChain LLM from the resolved configuration.

        Supports:
        - provider="openai"      → ChatOpenAI
        - provider="anthropic"   → ChatAnthropic
        - provider="google"      → ChatGoogleGenerativeAI
        - provider="groq"        → ChatGroq
        - provider="bedrock"     → ChatBedrockConverse
        - provider="azure"       → AzureChatOpenAI
        - provider="ollama"      → ChatOllama

        Args:
            llm_config: Resolved LLM configuration dict.

        Returns:
            LangChain chat model instance (BaseChatModel).

        Raises:
            ValueError: If the provider is not supported.
        """
        provider = (llm_config.get("provider") or "bedrock").lower()
        # Normalize provider aliases
        if provider == "aws bedrock":
            provider = "bedrock"
        model = llm_config.get("model", "")
        temperature = float(llm_config.get("temperature") or 0.1)
        max_tokens = int(llm_config.get("max_tokens") or 4096)
        region = llm_config.get("region") or "us-east-1"
        api_key = llm_config.get("api_key")
        base_url = llm_config.get("base_url")

        if provider == "openai":
            from langchain_openai import ChatOpenAI
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            logger.info("ReactStrategy: using ChatOpenAI model=%s", model)
            return ChatOpenAI(**kwargs)

        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            logger.info("ReactStrategy: using ChatAnthropic model=%s", model)
            return ChatAnthropic(**kwargs)

        if provider in ("google", "gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if api_key:
                kwargs["google_api_key"] = api_key
            logger.info("ReactStrategy: using ChatGoogleGenerativeAI model=%s", model)
            return ChatGoogleGenerativeAI(**kwargs)

        if provider == "groq":
            from langchain_groq import ChatGroq
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            logger.info("ReactStrategy: using ChatGroq model=%s", model)
            return ChatGroq(**kwargs)

        if provider in ("bedrock", "aws", "aws_bedrock"):
            from langchain_aws import ChatBedrockConverse
            import boto3
            from botocore.config import Config as BotocoreConfig
            access_key_id = llm_config.get("access_key_id")
            secret_access_key = llm_config.get("secret_access_key")
            session_token = llm_config.get("session_token")
            aws_profile = llm_config.get("aws_profile") or llm_config.get("profile")
            # Newer Bedrock models (e.g. Claude 3.5/4.x) require a cross-region
            # inference profile ID instead of the bare model ID for on-demand calls.
            # Automatically prepend the region prefix when the model ID looks like a
            # plain foundation model ID (e.g. "anthropic.claude-*") with no prefix.
            _INFERENCE_PROFILE_PREFIXES = ("us.", "eu.", "ap.")
            _NEEDS_PROFILE_PROVIDERS = ("anthropic.", "amazon.", "meta.", "mistral.")
            if not any(model.startswith(p) for p in _INFERENCE_PROFILE_PREFIXES) and \
                    any(model.startswith(p) for p in _NEEDS_PROFILE_PROVIDERS):
                if region.startswith("eu-"):
                    model = f"eu.{model}"
                elif region.startswith("ap-"):
                    model = f"ap.{model}"
                else:
                    model = f"us.{model}"
                logger.info("ReactStrategy: remapped model to inference profile ID: %s", model)
            logger.info(
                "ReactStrategy: using ChatBedrockConverse model=%s region=%s has_explicit_creds=%s profile=%s",
                model, region, bool(access_key_id), aws_profile,
            )
            if access_key_id and secret_access_key:
                boto_session = boto3.Session(
                    region_name=region,
                    aws_access_key_id=access_key_id,
                    aws_secret_access_key=secret_access_key,
                    aws_session_token=session_token,
                )
            else:
                boto_session = boto3.Session(region_name=region, profile_name=aws_profile)
            boto_client = boto_session.client(
                "bedrock-runtime",
                region_name=region,
                verify=False,
                config=BotocoreConfig(retries={"max_attempts": 3}),
            )
            return ChatBedrockConverse(
                model=model,
                region_name=region,
                temperature=temperature,
                max_tokens=max_tokens,
                client=boto_client,
            )

        if provider in ("azure", "azure_openai"):
            from langchain_openai import AzureChatOpenAI
            kwargs: Dict[str, Any] = {
                "azure_deployment": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["azure_endpoint"] = base_url
            logger.info("ReactStrategy: using AzureChatOpenAI model=%s", model)
            return AzureChatOpenAI(**kwargs)

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "num_predict": max_tokens,
            }
            if base_url:
                kwargs["base_url"] = base_url
            logger.info("ReactStrategy: using ChatOllama model=%s", model)
            return ChatOllama(**kwargs)

        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            f"Supported providers: openai, anthropic, google, groq, bedrock, azure, ollama."
        )

    # ------------------------------------------------------------------
    # Playbook tools (agent-writable KB)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_playbook_tools() -> List[Any]:
        """Build LangChain StructuredTool instances that let the agent write
        and update investigation playbooks in the knowledge base.

        These are appended to the MCP tools list before the ReAct agent is
        constructed so the agent can call them like any other tool.
        """
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field as PydanticField

        class SavePlaybookInput(BaseModel):
            title: str = PydanticField(description="Short title identifying the issue type (max 120 chars).")
            symptoms: List[str] = PydanticField(description="List of symptoms or error patterns observed.")
            solution: str = PydanticField(description="Step-by-step resolution or investigation procedure.")
            category: str = PydanticField(description="Category, e.g. 'database', 'auth', 'network', 'agent_discovered'.")

        class PatchPlaybookInput(BaseModel):
            issue_id: int = PydanticField(description="The integer ID of the known issue to update.")
            new_solution: str = PydanticField(description="Replacement solution text.")

        class ExecuteSkillInput(BaseModel):
            skill_name: str = PydanticField(
                description="Slug name of the skill to execute (as shown in the memory-context block)."
            )
            context: dict = PydanticField(
                default_factory=dict,
                description=(
                    "Key-value pairs injected into the skill's args_template placeholders. "
                    "For example: {\"log_group\": \"/aws/app\", \"threshold\": \"100\"}."
                ),
            )

        async def _save_playbook(title: str, symptoms: List[str], solution: str, category: str) -> str:
            try:
                from app.services.knowledge_base import knowledge_base as _kb
                result = await _kb.upsert_playbook(
                    title=title[:120],
                    symptoms=symptoms,
                    solution=solution,
                    category=category,
                    source="agent",
                )
                action = result.get("action", "saved")
                return f"Playbook {action}: id={result.get('id')} title='{result.get('title')}'"
            except Exception as exc:
                return f"save_playbook failed: {exc}"

        async def _patch_playbook(issue_id: int, new_solution: str) -> str:
            try:
                from app.services.knowledge_base import knowledge_base as _kb
                result = await _kb.patch_playbook_solution(
                    issue_id=issue_id,
                    new_solution=new_solution,
                    source="agent",
                )
                if "error" in result:
                    return f"patch_playbook error: {result['error']}"
                return f"Playbook patched: id={result.get('id')} title='{result.get('title')}'"
            except Exception as exc:
                return f"patch_playbook failed: {exc}"

        async def _execute_skill(skill_name: str, context: dict) -> str:
            """Run a named skill's steps against the live MCP tool set."""
            try:
                from app.core.skills import skill_service
                exec_result = await skill_service.execute(skill_name, context=context)
                return exec_result.to_agent_text()
            except Exception as exc:
                return f"execute_skill failed: {exc}"

        return [
            StructuredTool.from_function(
                coroutine=_save_playbook,
                name="save_playbook",
                description=(
                    "Save or update an investigation playbook for a known issue type. "
                    "Call this when you have identified the root cause and resolution of an issue."
                ),
                args_schema=SavePlaybookInput,
            ),
            StructuredTool.from_function(
                coroutine=_patch_playbook,
                name="patch_playbook",
                description=(
                    "Update the solution of an existing playbook by its integer ID. "
                    "Use this when you have found a better resolution than what is already recorded."
                ),
                args_schema=PatchPlaybookInput,
            ),
            StructuredTool.from_function(
                coroutine=_execute_skill,
                name="execute_skill",
                description=(
                    "Execute a named, pre-built remediation skill by running its ordered steps "
                    "against the live MCP tool set. Use this when the memory-context block "
                    "shows a matching 'Executable Skill' and you want to apply it directly. "
                    "Pass any required placeholder values in the 'context' dict."
                ),
                args_schema=ExecuteSkillInput,
            ),
        ]

    # ------------------------------------------------------------------
    # Checkpointer factory (AsyncPostgresSaver)
    # ------------------------------------------------------------------

    @staticmethod
    async def _make_checkpointer() -> Any:
        """Create an AsyncPostgresSaver connected to the app database.

        Returns None (gracefully) if the dependency is not installed or the
        connection fails — the agent still runs without crash-safe state in
        that case.
        """
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from app.config import settings

            # psycopg connection string (not +asyncpg variant)
            db_url = settings.database_url
            if "+asyncpg" in db_url:
                db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

            checkpointer = AsyncPostgresSaver.from_conn_string(db_url)
            await checkpointer.setup()
            return checkpointer
        except Exception as exc:
            logger.warning(
                "ReactStrategy: AsyncPostgresSaver unavailable — running without checkpointer: %s",
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # HITL pause helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _emit_hitl_pause(execution_id: Optional[str], interrupt_data: Dict[str, Any]) -> None:
        """Publish a hitl_pause SSE event into the execution's event queue."""
        if not execution_id:
            return
        try:
            from app.workflow.visual_executor import active_executions
            from app.workflow.event_schema import WorkflowEvent, EventType

            queue = active_executions.get(execution_id, {}).get("event_queue")
            if queue is not None:
                event = WorkflowEvent(
                    event_type=EventType.HITL_PAUSE,
                    data={
                        "execution_id": execution_id,
                        "request_id": interrupt_data.get("request_id", ""),
                        "draft_answer": interrupt_data.get("draft_answer", ""),
                        "message": interrupt_data.get("message", "Engineer approval required."),
                    },
                )
                await queue.put(event.to_sse())
        except Exception as exc:
            logger.warning("ReactStrategy: could not emit hitl_pause event: %s", exc)

    # ------------------------------------------------------------------
    # Agent construction (LangGraph)
    # ------------------------------------------------------------------

    def _build_agent(
        self,
        llm: Any,
        tools: List[Any],
        agent_config: Dict[str, Any],
        has_cloudwatch: bool = False,
        has_code_analyzer: bool = False,
        checkpointer: Any = None,
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

        system_parts = [
            "You are an expert on-call engineer assistant for KYC Protect.",
            "You have access to database tools and can run SQL queries to investigate issues.",
            "Always reason step by step and use the available tools to find accurate answers.",
            "When querying databases, prefer targeted queries over full table scans.",
            "Present your findings clearly with specific data from the query results.",
            "When you resolve an issue or identify its root cause, use the save_playbook tool "
            "to record the resolution so future investigations can benefit from it.",
            "If the memory-context block at the start of the query lists 'Executable Skill' entries "
            "that match the current issue, prefer calling execute_skill with the skill's name before "
            "running manual tool calls — this reuses proven remediation steps and is faster.",
        ]

        # CloudWatch-specific instructions when CW nodes are connected.
        if has_cloudwatch:
            system_parts.append(
                "\nYou also have access to CloudWatch log analysis tools. "
                "Use them to investigate log patterns, detect anomalies, and correlate "
                "events across services. When analysing logs:\n"
                "- Start with cloudwatch_analyze_patterns to identify error trends.\n"
                "- Use cloudwatch_detect_anomalies to compare current activity against baselines.\n"
                "- Use cloudwatch_correlate_logs with a correlation or trace ID to trace requests across services.\n"
                "- Use cloudwatch_search_logs for custom Insights queries when you need specific data.\n"
                "- Use cloudwatch_watch_logs to retrieve raw log events for detailed inspection.\n"
                "- Look for error spikes, unusual patterns, and cross-service correlations.\n"
                "- If pre-computed CloudWatch analysis is provided, review it before making additional queries."
            )

        # Code Analyzer instructions when codeAnalyzer nodes are connected.
        if has_code_analyzer:
            system_parts.append(
                "\nYou also have access to code analysis tools for investigating source repositories. "
                "Use the following 5-tool investigation workflow:\n"
                "  1. code_investigate     — primary entry for all investigations; "
                "call this first to identify relevant functions and root-cause suspects.\n"
                "  2. code_explain_flow    — when you need to understand how a specific "
                "function or code path works in depth.\n"
                "  3. code_analyze_change  — when the issue likely started after a recent "
                "deploy or code change; traces diff + deployment timeline.\n"
                "  4. code_get_runtime_evidence — when a stack trace or CloudWatch anomaly "
                "is available; maps runtime frames directly to source functions with highest "
                "confidence.\n"
                "  5. code_finalize_incident — ALWAYS call this at investigation end to "
                "record root cause, remediation steps, and persist findings for future use.\n\n"
                "Investigation guidelines:\n"
                "- Start with code_investigate for any code-related question.\n"
                "- Use code_analyze_change when the error correlates with a deployment.\n"
                "- Use code_get_runtime_evidence when a stack trace or CloudWatch anomaly is "
                "available — it has the highest confidence.\n"
                "- Always end every investigation with code_finalize_incident regardless of "
                "outcome.\n"
                "- Examine the 'evidence_grade' field in results: "
                "speculative < inferred < correlated < runtime-confirmed < historically-confirmed. "
                "Never conclude on 'speculative' alone — upgrade via code_get_runtime_evidence.\n"
                "- If [Anomaly-Code Correlation] context is present, use the suggested "
                "code_trace_flow call as your first code investigation step.\n"
                "- The 'sub_tool_trace' in code_investigate responses shows individual "
                "confidence scores — review them to identify weak evidence before concluding.\n"
                "- remediation.grade is always 'suggestive' — present remediation steps as "
                "options to consider, not guaranteed fixes.\n"
                "- If [Pre-computed Code Analysis] context is present, review the pre-summary "
                "before making additional tool calls."
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
        playbook_tools = self._build_playbook_tools()
        all_tools = list(tools) + playbook_tools

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

            inner_agent = create_react_agent(model=llm, tools=all_tools, prompt=system_prompt)

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
                model=llm,
                tools=all_tools,
                prompt=system_prompt,
                checkpointer=checkpointer,
            )

        return agent

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    async def _execute_agent(
        self,
        agent: Any,
        user_query: str,
        logger_instance: Any,
        execution_id: Optional[str] = None,
        stream_callback: Optional[StreamCallback] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute the LangGraph ReAct agent with the user's query.

        When a stream_callback is provided, uses ``agent.astream_events()`` to
        deliver LLM tokens and tool events in real time.  Falls back to
        ``agent.ainvoke()`` otherwise.

        The outer call is wrapped with ``with_retry`` so transient LLM errors
        (rate-limit, 502/503) are automatically retried up to 3 times.

        Args:
            agent: Compiled LangGraph agent graph.
            user_query: The user's question / investigation request.
            logger_instance: Logger for execution-scoped logging.
            execution_id: Execution ID for logging.
            stream_callback: Optional streaming callback protocol.

        Returns:
            Dict with:
              - final_answer: str — the last AI response
              - messages: list — all messages in the conversation
              - tool_calls: list — summary of tools invoked
        """
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
        from app.core.telemetry import agent_span, get_current_trace_id

        logger_instance.info("ReactStrategy: invoking agent with query: %.100s", user_query)

        input_state = {"messages": [HumanMessage(content=user_query)]}

        # Pass thread_id so the checkpointer can persist state across interrupts.
        run_config: Dict[str, Any] = {}
        if thread_id:
            run_config = {"configurable": {"thread_id": thread_id}}

        try:
            async with agent_span("react_agent", execution_id=execution_id):
                trace_id = get_current_trace_id()
                if trace_id:
                    # Store trace_id in the active execution for later persistence
                    try:
                        from app.services.visual_workflow_executor import visual_executor
                        if execution_id and execution_id in visual_executor.active_executions:
                            visual_executor.active_executions[execution_id]["trace_id"] = trace_id
                    except Exception:
                        pass

                if stream_callback is not None:
                    result_state = await with_retry(
                        self._execute_agent_stream,
                        agent, input_state, stream_callback, logger_instance, execution_id,
                        run_config,
                        max_retries=3,
                    )
                else:
                    result_state = await with_retry(
                        self._invoke_agent, agent, input_state, run_config,
                        max_retries=3,
                    )
        except Exception as exc:
            # Handle LangGraph HITL interrupt — surface to caller as a structured pause.
            try:
                from langgraph.types import GraphInterrupt
                if isinstance(exc, GraphInterrupt):
                    interrupt_value = exc.args[0] if exc.args else {}
                    logger_instance.info(
                        "ReactStrategy: HITL interrupt raised — execution_id=%s request_id=%s",
                        execution_id,
                        (interrupt_value[0].value if interrupt_value else {}).get("request_id", "?"),
                    )
                    # Publish hitl_pause SSE event via the active_executions queue.
                    _interrupt_data = interrupt_value[0].value if interrupt_value else {}
                    await self._emit_hitl_pause(execution_id, _interrupt_data)
                    # Return a sentinel result so the caller knows we paused.
                    return {
                        "final_answer": None,
                        "messages": [],
                        "tool_calls": [],
                        "hitl_paused": True,
                        "hitl_request": _interrupt_data,
                    }
            except ImportError:
                pass

            # On context overflow, use LLM-assisted compression then retry once.
            classified = classify_error(exc)
            if classified.should_compress:
                logger_instance.warning(
                    "ReactStrategy: context overflow detected — compressing and retrying",
                    extra={"execution_id": execution_id},
                )
                from langchain_core.messages import BaseMessage as _BM
                from app.core.context_compression import compress as _compress

                existing_msgs = input_state.get("messages", [])
                if isinstance(existing_msgs, list) and all(isinstance(m, _BM) for m in existing_msgs):
                    # Attempt LLM-assisted compression; fall back to hard truncation if llm=None
                    try:
                        compressed = await _compress(existing_msgs, llm=None)
                        input_state = {"messages": compressed}
                        logger_instance.info(
                            "ReactStrategy: compressed %d → %d messages via context_compression",
                            len(existing_msgs), len(compressed),
                            extra={"execution_id": execution_id},
                        )
                    except Exception as _ce:
                        logger_instance.warning(
                            "ReactStrategy: context_compression failed (%s), falling back to _compact",
                            _ce,
                        )
                        input_state = _compact_input_state(input_state)
                else:
                    input_state = _compact_input_state(input_state)

                if stream_callback is not None:
                    result_state = await self._execute_agent_stream(
                        agent, input_state, stream_callback, logger_instance, execution_id,
                        run_config,
                    )
                else:
                    result_state = await self._invoke_agent(agent, input_state, run_config)
            else:
                raise

        messages = result_state.get("messages", [])
        serialized_messages = []
        tool_calls_summary = []
        final_answer = ""

        for msg in messages:
            if isinstance(msg, HumanMessage):
                serialized_messages.append({"role": "user", "content": str(msg.content)})

            elif isinstance(msg, AIMessage):
                content = str(msg.content) if msg.content else ""
                entry: Dict[str, Any] = {"role": "assistant", "content": content}

                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "name": tc.get("name"),
                            "args": tc.get("args", {}),
                        }
                        for tc in msg.tool_calls
                    ]
                    for tc in msg.tool_calls:
                        tool_calls_summary.append(
                            {"tool": tc.get("name"), "args_keys": list((tc.get("args") or {}).keys())}
                        )

                serialized_messages.append(entry)
                if content:
                    final_answer = content

            elif isinstance(msg, ToolMessage):
                serialized_messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(msg, "tool_call_id", ""),
                    "content": str(msg.content)[:2000],
                })

            elif isinstance(msg, SystemMessage):
                pass

        logger_instance.info(
            "ReactStrategy: agent completed with %d messages, %d tool calls",
            len(serialized_messages),
            len(tool_calls_summary),
        )

        return {
            "final_answer": final_answer,
            "messages": serialized_messages,
            "tool_calls": tool_calls_summary,
        }

    @staticmethod
    async def _invoke_agent(
        agent: Any,
        input_state: Dict[str, Any],
        run_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Non-streaming invocation with timeout."""
        try:
            result_state = await asyncio.wait_for(
                agent.ainvoke(input_state, config=run_config or {}),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("ReAct agent timed out after 300 seconds.")
        return result_state

    async def _execute_agent_stream(
        self,
        agent: Any,
        input_state: Dict[str, Any],
        stream_callback: StreamCallback,
        logger_instance: Any,
        execution_id: Optional[str] = None,
        run_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Streaming invocation using ``agent.astream_events()`` (v2)."""
        from app.core.tool_guardrails import (
            ToolCallGuardrailController,
            toolguard_synthetic_result,
            append_toolguard_guidance,
        )

        current_tool_name: str = ""
        current_tool_args: Dict[str, Any] = {}
        accumulated_state: Dict[str, Any] = {"messages": []}
        msg_map: Dict[str, Any] = {}

        # ── Guardrail controller — one per streaming invocation (= one turn) ─
        # Side-effect-free controller whose decisions (warn / block / halt)
        # are acted on by this runtime code.
        guardrail = ToolCallGuardrailController()

        try:
            async for event in agent.astream_events(input_state, config=run_config or {}, version="v2"):
                kind = event.get("event", "")
                data = event.get("data", {})
                name = event.get("name", "")

                if kind == "on_llm_new_token":
                    token = event.get("data", {}).get("chunk", "")
                    if token:
                        try:
                            await stream_callback.on_llm_token(token)
                        except Exception:
                            pass

                elif kind == "on_chat_model_start":
                    pass

                elif kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk and hasattr(chunk, "tool_calls") and chunk.tool_calls:
                        for tc in chunk.tool_calls:
                            current_tool_name = tc.get("name", "")
                            try:
                                await stream_callback.on_tool_call(current_tool_name, tc.get("args", {}))
                            except Exception:
                                pass
                    content = getattr(chunk, "content", None) if chunk else None
                    if content and isinstance(content, str):
                        try:
                            await stream_callback.on_llm_token(content)
                        except Exception:
                            pass

                elif kind == "on_chat_model_end":
                    output = data.get("output")
                    if output and hasattr(output, "id"):
                        msg_map[output.id] = output

                elif kind == "on_tool_start":
                    tool_input = data.get("input", {})
                    tool_name = name or current_tool_name
                    current_tool_name = tool_name
                    current_tool_args = tool_input if isinstance(tool_input, dict) else {}

                    # ── Guardrail pre-check ───────────────────────────────
                    # before_call() is side-effect-free: it only reads state and
                    # returns a decision.  We honour warn/block/halt by logging;
                    # full blocking requires tool-wrapper interception which is
                    # done at the MCPToolWrapper layer when guardrail is wired in.
                    _gc_pre = guardrail.before_call(tool_name, current_tool_args)
                    if _gc_pre.should_halt:
                        logger_instance.warning(
                            "ReactStrategy: guardrail HALT before tool '%s' — %s",
                            tool_name, _gc_pre.message,
                            extra={"execution_id": execution_id},
                        )
                        try:
                            await stream_callback.on_error(
                                f"[Guardrail HALT] {_gc_pre.message}"
                            )
                        except Exception:
                            pass
                    elif not _gc_pre.allows_execution:
                        logger_instance.warning(
                            "ReactStrategy: guardrail BLOCK for tool '%s' (count=%d) — %s",
                            tool_name, _gc_pre.count, _gc_pre.message,
                            extra={"execution_id": execution_id},
                        )
                    elif _gc_pre.action == "warn":
                        logger_instance.info(
                            "ReactStrategy: guardrail WARN for tool '%s' (count=%d) — %s",
                            tool_name, _gc_pre.count, _gc_pre.message,
                            extra={"execution_id": execution_id},
                        )

                    try:
                        await stream_callback.on_tool_call(tool_name, current_tool_args)
                    except Exception:
                        pass

                elif kind == "on_tool_end":
                    tool_output = data.get("output", "")
                    tool_name = name or current_tool_name
                    output_str = str(tool_output)

                    # ── Guardrail post-check ──────────────────────────────
                    from app.core.tool_guardrails import classify_tool_failure
                    _is_failed, _fail_reason = classify_tool_failure(tool_name, output_str)
                    _gc_post = guardrail.after_call(
                        tool_name, current_tool_args, output_str, failed=_is_failed,
                    )
                    if _gc_post.action in ("warn", "block", "halt"):
                        output_str = append_toolguard_guidance(output_str, _gc_post)
                        logger_instance.warning(
                            "ReactStrategy: guardrail %s after tool '%s' (count=%d) — %s",
                            _gc_post.action.upper(), tool_name, _gc_post.count, _gc_post.message,
                            extra={"execution_id": execution_id},
                        )

                    try:
                        await stream_callback.on_tool_result(tool_name, output_str[:2000])
                    except Exception:
                        pass

                    # ── /steer injection ──────────────────────────────────
                    # Drain any engineer notes queued via POST /steer.
                    # Injected as HumanMessages so the LLM sees them before
                    # its next reasoning step (at tool boundary, as specified).
                    if execution_id:
                        try:
                            from app.services.visual_workflow_executor import visual_executor
                            from langchain_core.messages import HumanMessage as _HM
                            steer_notes: list = (
                                visual_executor.active_executions
                                .get(execution_id, {})
                                .get("steer_notes", [])
                            )
                            while steer_notes:
                                note = steer_notes.pop(0)
                                steer_msg = _HM(content=f"[Engineer Note] {note}")
                                accumulated_state["messages"] = (
                                    accumulated_state.get("messages", []) + [steer_msg]
                                )
                                logger_instance.info(
                                    "ReactStrategy: injected steer note at tool boundary "
                                    "(execution_id=%s)", execution_id,
                                )
                        except Exception:
                            pass

                elif kind == "on_chain_error":
                    err_str = str(data.get("error", ""))
                    try:
                        await stream_callback.on_error(err_str)
                    except Exception:
                        pass

        except asyncio.TimeoutError:
            try:
                await stream_callback.on_error("ReAct agent timed out after 300 seconds.")
            except Exception:
                pass
            raise RuntimeError("ReAct agent timed out after 300 seconds.")

        if accumulated_state.get("messages"):
            return accumulated_state

        messages = list(msg_map.values())
        if messages:
            return {"messages": messages}

        return await self._invoke_agent(agent, input_state)
