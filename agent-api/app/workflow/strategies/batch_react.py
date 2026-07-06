"""Batch investigation strategy using async Map-Reduce.

Architecture
------------
Given a high-level investigation query that touches multiple services or log
groups, ``BatchReactStrategy`` decomposes it into N independent sub-queries,
runs each through a full ``ReactStrategy`` agent in parallel (map phase), then
synthesises all sub-results into a single unified root-cause report (reduce
phase).

Decomposition sources (tried in order):
  1. ``context["batch_targets"]`` — caller provides an explicit list of
     :class:`InvestigationTarget` dicts (highest priority).
  2. LLM decomposition — the same LLM configured on the workflow is asked to
     split the query into parallel sub-investigations.
  3. CloudWatch log groups — if the workflow has a cloudwatchAnalyzer node,
     one sub-investigation is created per connected log group.
  4. Single fallback — if nothing yields multiple targets, the strategy
     degrades gracefully to a single ReactStrategy run (no overhead).

Activation
----------
The strategy handles any workflow that contains a ``batchAgent`` node **or**
has ``context["batch_targets"]`` with two or more entries.

SSE streaming
-------------
Each sub-agent streams with a unique ``node_id`` (``"batch_{target_name}"``)
so the UI cost/token panels show per-service breakdowns without any changes.

No external dependencies beyond what ``ReactStrategy`` already uses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.workflow.strategies.base import BaseStrategy
from app.workflow.strategies.react import ReactStrategy
from app.workflow.strategies.react.llm_factory import build_llm
from app.workflow.strategies.react.workflow_config import (
    extract_cloudwatch_config,
    resolve_llm_config_for_workflow,
)
from app.core.map_reduce import MapReduceEngine, MapReduceConfig, MapResult
from app.core.parallel_flow import ParallelFlowRunner, ParallelFlowConfig
from app.core.redact import redact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class InvestigationTarget:
    """One unit of work in the map phase."""

    name:         str                      # short label, e.g. "auth-service"
    query:        str                      # specific sub-query for this target
    log_groups:   List[str] = field(default_factory=list)
    service:      Optional[str] = None
    extra_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubInvestigationResult:
    """Outcome of one map-phase sub-investigation."""

    target:       InvestigationTarget
    final_answer: str
    tool_calls:   List[Dict[str, Any]]
    duration_ms:  float
    error:        Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Reduce prompt
# ---------------------------------------------------------------------------

_REDUCE_SYSTEM_PROMPT = """\
You are a senior engineer synthesising findings from parallel \
sub-investigations. You will receive the original high-level query and the \
analysis reports from each sub-investigation. Your job is to produce a \
single, concise consolidated report that:
  1. Directly answers the original query.
  2. States the primary finding (or root cause, when investigating an issue) clearly.
  3. Notes any secondary or contributing factors.
  4. Highlights cross-service correlations and causal chains.
  5. Provides concrete recommended next steps.
  6. Flags any sub-investigations that failed or produced uncertain results.

Be precise. Cite specific sub-investigation findings where relevant. \
Do not repeat the raw data — synthesise it into insight."""

_REDUCE_USER_TEMPLATE = """\
## Original Investigation Query
{original_query}

## Sub-Investigation Results
{sub_results}

## Task
Synthesise the above into a unified consolidated report following the \
format described in your instructions."""

_DECOMPOSE_SYSTEM_PROMPT = """\
You are a planning agent for a parallel investigation system. \
Your job is to decompose a high-level investigation query into a set of \
focused, independent sub-queries that can be investigated in parallel. \
Each sub-query should target a distinct service, component, or log group. \
Output ONLY a JSON array (no markdown, no preamble) of objects with the \
keys: \"name\" (short slug), \"query\" (the sub-query string), \
\"service\" (service name or null), \"log_groups\" (array of log group paths \
or empty array)."""


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class BatchReactStrategy(BaseStrategy):
    """Map-Reduce investigation over multiple services or log groups.

    Workflow node type: ``batchAgent``

    Context keys (all optional):
      ``batch_targets``     — list of InvestigationTarget-compatible dicts
      ``batch_concurrency`` — int, overrides MAP_REDUCE_CONCURRENCY env var
      ``batch_timeout``     — float seconds, overrides MAP_REDUCE_ITEM_TIMEOUT
      ``user_query``        — high-level investigation query
    """

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return False
        return any(n.get("type") == "batchAgent" for n in nodes)

    async def execute(
        self,
        workflow: Dict[str, Any],
        context:  Dict[str, Any],
    ) -> Dict[str, Any]:
        execution_id     = context.get("execution_id")
        logger_instance  = context.get("logger", logger)
        user_query       = (
            context.get("user_query")
            or context.get("inputs", {}).get("user_query", "")
        )
        stream_callback  = context.get("stream_callback")
        execution_start  = datetime.now(timezone.utc)

        if not user_query:
            raise ValueError(
                "BatchReactStrategy requires a user_query in the execution context."
            )

        logger_instance.info(
            "BatchReactStrategy: starting execution",
            extra={
                "execution_id": execution_id,
                "query_preview": user_query[:100],
            },
        )

        # ── 1. Resolve LLM (needed for decompose + reduce) ────────────────
        react = ReactStrategy()
        llm_config = await resolve_llm_config_for_workflow(workflow)
        llm = build_llm(llm_config)

        # ── 2. Build investigation targets ────────────────────────────────
        targets = await self._build_targets(
            user_query, workflow, context, llm, logger_instance, execution_id
        )

        logger_instance.info(
            "BatchReactStrategy: %d target(s) identified — %s",
            len(targets),
            [t.name for t in targets],
            extra={"execution_id": execution_id},
        )

        if len(targets) == 1:
            # Single target — delegate straight to ReactStrategy, no overhead.
            logger_instance.info(
                "BatchReactStrategy: single target, delegating to ReactStrategy",
                extra={"execution_id": execution_id},
            )
            sub_ctx = {**context, "user_query": targets[0].query}
            return await react.execute(workflow, sub_ctx)

        # ── 3. Map phase — parallel dispatch ─────────────────────────────
        # ParallelFlowRunner fans out one ReactStrategy per target concurrently,
        # bounded by the concurrency limit.
        concurrency = int(context.get("batch_concurrency") or 5)
        timeout_s   = float(context.get("batch_timeout") or 180.0)

        flow_runner = ParallelFlowRunner(
            ParallelFlowConfig(
                concurrency          = concurrency,
                item_timeout_seconds = timeout_s,
                fail_fast            = False,
            )
        )

        flow_results = await flow_runner.run_all(
            param_sets = targets,
            flow_fn    = lambda t: self._run_sub_investigation(
                t, workflow, context, logger_instance, execution_id
            ),
        )

        # ── 4. Reduce phase — synthesise via MapReduceEngine ──────────────
        # Wrap flow_results into MapResult shapes for the reducer signature.
        map_results: List[MapResult] = [
            MapResult(
                item       = fr.params,
                result     = fr.result,
                error      = fr.error,
                duration_ms= fr.duration_ms,
            )
            for fr in flow_results
        ]

        success_count = sum(1 for r in map_results if r.result and r.result.success)
        reduce_result: Optional[str] = None
        reduce_error:  Optional[str] = None

        if success_count >= max(1, len(targets) * 0.3):
            try:
                reduce_result = await self._reduce(
                    user_query, map_results, llm, logger_instance, execution_id
                )
            except Exception as _re:
                reduce_error = redact(str(_re))
                logger_instance.warning(
                    "BatchReactStrategy: reduce phase failed — %s", reduce_error,
                    extra={"execution_id": execution_id},
                )
        else:
            reduce_error = (
                f"Reduce skipped: only {success_count}/{len(targets)} "
                f"sub-investigations succeeded"
            )

        # Assemble an mr_result-compatible object for downstream code.
        class _MRResult:
            def __init__(self):
                self.map_results    = map_results
                self.reduce_result  = reduce_result
                self.reduce_error   = reduce_error
                self.total_duration_ms = sum(fr.duration_ms for fr in flow_results)
            def summary(self):
                return {
                    "total_items":       len(map_results),
                    "succeeded":         success_count,
                    "failed":            len(map_results) - success_count,
                    "success_rate":      round(success_count / max(len(map_results), 1), 3),
                    "total_duration_ms": round(self.total_duration_ms, 1),
                    "reduce_ok":         reduce_result is not None,
                    "reduce_error":      reduce_error,
                }

        mr_result = _MRResult()

        logger_instance.info(
            "BatchReactStrategy: parallel batch complete — %s",
            mr_result.summary(),
            extra={"execution_id": execution_id},
        )

        # ── 4. Notify via stream callback ─────────────────────────────────
        if stream_callback:
            final = mr_result.reduce_result or _fallback_answer(mr_result)
            try:
                await stream_callback.on_complete(final[:500])
            except Exception:
                pass

        # ── 5. Build return value ──────────────────────────────────────────
        all_tool_calls: List[Dict[str, Any]] = []
        sub_summaries: List[Dict[str, Any]] = []

        for mr in mr_result.map_results:
            sub: Optional[SubInvestigationResult] = mr.result
            if sub:
                all_tool_calls.extend(sub.tool_calls)
                sub_summaries.append({
                    "target":       sub.target.name,
                    "service":      sub.target.service,
                    "success":      sub.success,
                    "answer":       sub.final_answer[:500] if sub.final_answer else None,
                    "error":        sub.error,
                    "duration_ms":  round(mr.duration_ms, 1),
                })
            else:
                sub_summaries.append({
                    "target":  getattr(mr.item, "name", "?"),
                    "success": False,
                    "error":   mr.error,
                })

        final_answer = mr_result.reduce_result or _fallback_answer(mr_result)

        return {
            "type":           "batch_react",
            "user_query":     user_query,
            "final_answer":   final_answer,
            "sub_results":    sub_summaries,
            "tool_calls":     all_tool_calls,
            "map_stats":      mr_result.summary(),
            "model":          llm_config.get("model", "unknown"),
            "provider":       llm_config.get("provider", "unknown"),
            "total_duration_ms": round(mr_result.total_duration_ms, 1),
        }

    # ------------------------------------------------------------------
    # Target decomposition
    # ------------------------------------------------------------------

    async def _build_targets(
        self,
        query:          str,
        workflow:       Dict[str, Any],
        context:        Dict[str, Any],
        llm:            Any,
        logger_instance: Any,
        execution_id:   Optional[str],
    ) -> List[InvestigationTarget]:
        """Resolve investigation targets using the best available source."""

        # ── Priority 1: explicit caller-provided targets ───────────────────
        raw_targets = context.get("batch_targets") or []
        if raw_targets and len(raw_targets) >= 2:
            targets = [
                InvestigationTarget(
                    name          = t.get("name", f"target_{i}"),
                    query         = t.get("query", query),
                    log_groups    = t.get("log_groups", []),
                    service       = t.get("service"),
                    extra_context = t.get("extra_context", {}),
                )
                for i, t in enumerate(raw_targets)
            ]
            logger_instance.debug(
                "BatchReactStrategy: using %d explicit batch_targets",
                len(targets),
                extra={"execution_id": execution_id},
            )
            return targets

        # ── Priority 2: CloudWatch log groups from connected CW nodes ──────
        cw_cfg = extract_cloudwatch_config(workflow)
        if cw_cfg:
            log_groups = cw_cfg.get("log_groups") or []
            if len(log_groups) >= 2:
                targets = [
                    InvestigationTarget(
                        name       = _slug(lg),
                        query      = (
                            f"Investigate the following for the issue described below.\n"
                            f"Log group: {lg}\n\nIssue: {query}"
                        ),
                        log_groups = [lg],
                    )
                    for lg in log_groups
                ]
                logger_instance.debug(
                    "BatchReactStrategy: derived %d targets from CloudWatch log groups",
                    len(targets),
                    extra={"execution_id": execution_id},
                )
                return targets

        # ── Priority 3: LLM decomposition ─────────────────────────────────
        try:
            targets = await self._llm_decompose(
                query, llm, logger_instance, execution_id
            )
            if len(targets) >= 2:
                return targets
        except Exception as _dec_err:
            logger_instance.warning(
                "BatchReactStrategy: LLM decompose failed (%s) — falling back to single target",
                redact(str(_dec_err)),
                extra={"execution_id": execution_id},
            )

        # ── Priority 4: single-target fallback ────────────────────────────
        return [InvestigationTarget(name="investigation", query=query)]

    async def _llm_decompose(
        self,
        query:          str,
        llm:            Any,
        logger_instance: Any,
        execution_id:   Optional[str],
    ) -> List[InvestigationTarget]:
        """Ask the LLM to split *query* into parallel sub-investigations.

        Returns at least 2 targets or raises so the caller can fall through.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        logger_instance.debug(
            "BatchReactStrategy: using LLM to decompose query into sub-investigations",
            extra={"execution_id": execution_id},
        )

        messages = [
            SystemMessage(content=_DECOMPOSE_SYSTEM_PROMPT),
            HumanMessage(content=f"Query to decompose:\n{query}"),
        ]

        response = await llm.ainvoke(messages)
        raw = (response.content or "").strip()

        # Strip optional markdown code fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)
        if not isinstance(parsed, list) or len(parsed) < 2:
            raise ValueError(f"LLM returned fewer than 2 sub-queries: {raw[:200]}")

        targets = [
            InvestigationTarget(
                name       = item.get("name", f"sub_{i}"),
                query      = item.get("query", query),
                log_groups = item.get("log_groups", []),
                service    = item.get("service"),
            )
            for i, item in enumerate(parsed)
        ]

        logger_instance.info(
            "BatchReactStrategy: LLM decomposed query into %d sub-investigations: %s",
            len(targets),
            [t.name for t in targets],
            extra={"execution_id": execution_id},
        )
        return targets

    # ------------------------------------------------------------------
    # Map function — one sub-investigation
    # ------------------------------------------------------------------

    async def _run_sub_investigation(
        self,
        target:          InvestigationTarget,
        workflow:        Dict[str, Any],
        parent_context:  Dict[str, Any],
        logger_instance: Any,
        parent_exec_id:  Optional[str],
    ) -> SubInvestigationResult:
        """Execute a single sub-investigation via ReactStrategy.

        Each sub-investigation gets:
          - Its own ``MCPClientManager`` so MCP stdio processes are isolated.
          - A ``_SubAgentCallback`` that tags every SSE event with the
            target's ``node_id`` so the UI distinguishes per-service streams.
          - A scoped ``execution_id`` for tracing.
        """
        from app.services.mcp_client_manager import MCPClientManager

        sub_exec_id  = f"{parent_exec_id or 'batch'}_sub_{target.name}"
        sub_node_id  = f"batch_{target.name}"
        start        = datetime.now(timezone.utc)

        logger_instance.info(
            "BatchReactStrategy [%s]: starting sub-investigation",
            target.name,
            extra={"execution_id": sub_exec_id},
        )

        # Dedicated MCP manager — each sub-agent owns its own stdio processes.
        sub_mcp = MCPClientManager()

        # Stream callback that tags events with this target's node_id.
        parent_cb = parent_context.get("stream_callback")
        sub_cb    = _SubAgentCallback(parent_cb, sub_node_id) if parent_cb else None

        # Inject log groups as CloudWatch context if provided.
        sub_context: Dict[str, Any] = {
            **parent_context,
            "execution_id":   sub_exec_id,
            "user_query":     target.query,
            "mcp_manager":    sub_mcp,
            "stream_callback": sub_cb,
        }
        if target.log_groups:
            sub_context["cloudwatch_context"] = {
                "log_groups": target.log_groups,
            }
        if target.extra_context:
            sub_context.update(target.extra_context)

        try:
            react  = ReactStrategy()
            result = await react.execute(workflow, sub_context)

            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            logger_instance.info(
                "BatchReactStrategy [%s]: completed in %.0f ms",
                target.name, duration_ms,
                extra={"execution_id": sub_exec_id},
            )
            return SubInvestigationResult(
                target       = target,
                final_answer = result.get("final_answer") or "",
                tool_calls   = result.get("tool_calls", []),
                duration_ms  = duration_ms,
            )

        except Exception as exc:
            duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            err_str = redact(str(exc))
            logger_instance.warning(
                "BatchReactStrategy [%s]: sub-investigation failed — %s",
                target.name, err_str,
                extra={"execution_id": sub_exec_id},
            )
            return SubInvestigationResult(
                target       = target,
                final_answer = "",
                tool_calls   = [],
                duration_ms  = duration_ms,
                error        = err_str,
            )
        finally:
            try:
                await sub_mcp.disconnect_all()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Reduce function — synthesise all sub-results
    # ------------------------------------------------------------------

    async def _reduce(
        self,
        original_query:  str,
        map_results:     List[MapResult[InvestigationTarget, SubInvestigationResult]],
        llm:             Any,
        logger_instance: Any,
        execution_id:    Optional[str],
    ) -> str:
        """Synthesise parallel sub-investigation results into a unified report.

        Uses the same LLM as the main workflow — no extra model config needed.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        logger_instance.info(
            "BatchReactStrategy: running reduce phase over %d sub-results",
            len(map_results),
            extra={"execution_id": execution_id},
        )

        # Build readable sub-result blocks.
        blocks: List[str] = []
        for mr in map_results:
            sub: Optional[SubInvestigationResult] = mr.result
            if sub and sub.success:
                blocks.append(
                    f"### {sub.target.name}"
                    + (f" ({sub.target.service})" if sub.target.service else "")
                    + f"\n{sub.final_answer or '(no answer)'}"
                )
            else:
                err = (sub.error if sub else mr.error) or "unknown error"
                name = (sub.target.name if sub else getattr(mr.item, "name", "?"))
                blocks.append(f"### {name}\n**FAILED**: {err}")

        sub_results_text = "\n\n".join(blocks)

        messages = [
            SystemMessage(content=_REDUCE_SYSTEM_PROMPT),
            HumanMessage(
                content=_REDUCE_USER_TEMPLATE.format(
                    original_query=original_query,
                    sub_results=sub_results_text,
                )
            ),
        ]

        response = await llm.ainvoke(messages)
        synthesis = (response.content or "").strip()

        logger_instance.info(
            "BatchReactStrategy: reduce synthesis complete (%d chars)",
            len(synthesis),
            extra={"execution_id": execution_id},
        )
        return synthesis


# ---------------------------------------------------------------------------
# Sub-agent stream callback shim
# ---------------------------------------------------------------------------

class _SubAgentCallback:
    """Wraps a parent StreamCallback, tagging every event with a sub-agent node_id.

    This lets the front-end SSE consumer display per-service token/tool panels
    without any changes — the ``node_id`` field already drives that UI.
    """

    def __init__(self, parent: Any, node_id: str) -> None:
        self._parent  = parent
        self._node_id = node_id

    async def on_llm_token(self, token: str) -> None:
        if self._parent:
            try:
                await self._parent.on_llm_token(token)
            except Exception:
                pass

    async def on_tool_call(self, tool_name: str, args: dict) -> None:
        if self._parent:
            try:
                await self._parent.on_tool_call(f"[{self._node_id}] {tool_name}", args)
            except Exception:
                pass

    async def on_tool_result(self, tool_name: str, result: str, failed: bool = False) -> None:
        if self._parent:
            try:
                await self._parent.on_tool_result(f"[{self._node_id}] {tool_name}", result, failed=failed)
            except Exception:
                pass

    async def on_error(self, error: str) -> None:
        if self._parent:
            try:
                await self._parent.on_error(f"[{self._node_id}] {error}")
            except Exception:
                pass

    async def on_complete(self, output: str) -> None:
        if self._parent:
            try:
                await self._parent.on_complete(output)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Convert a log group path or service name into a short, safe slug."""
    name = text.rsplit("/", 1)[-1] or text  # take last path segment
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    return name[:40].strip("-")


def _fallback_answer(mr_result: Any) -> str:
    """Build a best-effort answer when the reduce phase failed or was skipped."""
    lines = ["## Investigation Summary (reduce phase unavailable)\n"]
    for mr in mr_result.map_results:
        sub = mr.result
        if sub and sub.success and sub.final_answer:
            lines.append(f"**{sub.target.name}**: {sub.final_answer[:300]}")
        elif mr.error:
            name = getattr(mr.item, "name", "?")
            lines.append(f"**{name}**: *(failed — {mr.error})*")
    if mr_result.reduce_error:
        lines.append(f"\n*Reduce phase error: {mr_result.reduce_error}*")
    return "\n".join(lines)
