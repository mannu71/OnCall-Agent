"""CloudWatch tools exposed as LangChain StructuredTools for the ReAct agent.

When a ``cloudwatchAnalyzer`` node is connected to an ``agent`` node in the
workflow graph, the executor calls :func:`build_cloudwatch_agent_tools` to
create LangChain-compatible tool instances that the agent can invoke
autonomously during its reasoning loop.

Each tool wraps a function from :mod:`app.mcp.tools.watch_tools` or
:mod:`app.mcp.tools.metrics_tools`, binding pre-resolved AWS credentials so
the LLM never sees raw secrets.

Enhancement summary (2026-05-12):
- max_events_per_group: configurable pagination (was hard-capped at 100)
- regions: multi-region support for watch_logs
- per_group_sensitivity: per-group anomaly thresholds
- cloudwatch_discover_log_groups: new tool for log group auto-discovery
- cloudwatch_get_metric_data: new tool for CloudWatch Metrics queries
- cloudwatch_get_metric_statistics: new tool for single-metric statistics
- cloudwatch_list_alarms: new tool to surface currently-firing alarms
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.workflow.tools.cloudwatch_sanitizer import (
    CloudWatchToolSanitizer,
    _count_tokens,
)
from app.workflow.tools.cloudwatch_summarizers import (
    summarise_anomalies,
    summarise_correlation,
    summarise_patterns,
)
from app.workflow.tools.cloudwatch_drilldown import (
    _DEFAULT_FIXUP_LIMIT,
    build_insights_query_for_anomaly,
    build_insights_query_for_pattern,
    cap_drill_preview,
    lint_insights_query,
    pick_drill_target,
    recall_kb_for_log_groups,
    recall_kb_for_pattern,
    should_auto_drill_down,
)
from app.workflow.tools.cloudwatch_metrics_fusion import attach_metrics_context

logger = logging.getLogger(__name__)

# Module-level sanitizer — accumulates per-session token-usage telemetry.
_sanitizer = CloudWatchToolSanitizer()

# Per-tool-call token budget for outputs that aren't event-shaped
# (alarms, metrics, discovery). Mirrors CloudWatchToolSanitizer.TOKEN_BUDGET.
_NON_EVENT_TOKEN_BUDGET = 1500


def _has_events(raw: Any) -> bool:
    """Return True if *raw* matches a shape the sanitizer can extract events from."""
    if not isinstance(raw, dict):
        return False
    if "events" in raw or "results" in raw:
        return True
    return any(
        isinstance(v, dict) and "events" in v for v in raw.values()
    )


def _budget_text(text: str, tool_name: str, budget: int) -> str:
    """Enforce a per-call token budget on *text* by positional truncation.

    Uses a single encode → slice token IDs → decode pass (O(N)) when tiktoken
    is available, avoiding the O(N log N) binary-search approach.

    This is the LAST resort, not the first: truncation keeps the head of the
    response and discards the tail regardless of what is in it, so run
    ``_compress_then_budget`` ahead of it wherever the caller can await.
    """
    from app.core.llm.token_estimate import get_encoder
    enc = get_encoder()
    if enc is not None:
        token_ids = enc.encode(text)
        if len(token_ids) <= budget:
            return text
        logger.warning(
            "CW tool [%s]: response %d tokens exceeded budget %d — truncated.",
            tool_name, len(token_ids), budget,
        )
        truncated = enc.decode(token_ids[:budget])
        return (
            truncated
            + f"\n...[truncated by token budget: was ~{len(token_ids)} tokens, "
            f"capped at {budget}]"
        )
    # tiktoken unavailable — fall back to char-based approximation
    char_budget = budget * 4
    if len(text) <= char_budget:
        return text
    logger.warning(
        "CW tool [%s]: response ~%d tokens exceeded budget %d — truncated (char heuristic).",
        tool_name, len(text) // 4, budget,
    )
    return (
        text[:char_budget]
        + f"\n...[truncated by token budget: capped at ~{budget} tokens]"
    )


async def _compress_then_budget(text: str, tool_name: str, budget: int) -> str:
    """Compress an over-budget CloudWatch response, THEN enforce the budget.

    CloudWatch responses were the one large tool family the compression sidecar
    never saw. Each tool truncated to its own token budget first and stamped a
    ``[truncated…]`` suffix, which the shared ``compress_then_cap`` treats as an
    idempotency marker and skips — so the sidecar was handed nothing but
    already-shrunk text. Measured 2026-07-23 on a live analyze run, a single
    ``cloudwatch_search_logs`` call returned 138,972 tokens against a 2,500
    budget: 98.2% of it was dropped by POSITION, which is how error lines sitting
    past the cut vanish from an investigation.

    Log lines are the sidecar's single best shape (measured 5,128 → 223 tokens,
    95.6%, on a realistic sample) and its `search` transform is content-selective
    — it kept both seeded ERROR lines while collapsing repetitive INFO noise, so
    the same 2,500-token budget now carries far more signal than its head-slice
    ever did. Uniform JSON goes through `smart_crusher`, which is lossless: rows
    are re-encoded as a typed header plus CSV (measured 9,559 → 5,260 tokens with
    all 180 rows intact).

    Under-budget responses return untouched without a round-trip, and any sidecar
    failure falls back to ``_budget_text`` — byte-identical to the old behaviour.
    """
    if _count_tokens(text) <= budget:
        return text  # already fits — don't pay sidecar latency to shrink it further

    def _cap(t: str) -> str:
        return _budget_text(t, tool_name, budget)

    from app.config import settings as _settings
    if not _settings.compression_all_tools:
        return _cap(text)

    from app.core.context.tool_output import compress_then_cap
    # Distinct memo key: `wrap_tools_with_output_cap` also runs compress_then_cap
    # on this tool's (already compressed + budgeted) return value under the bare
    # tool name. Sharing one key would let those necessarily-unchanged outer
    # results advance the uncompressible streak and switch OFF the inner call
    # that does the real work.
    return await compress_then_cap(text, _cap, tool_name=f"{tool_name}:pre_budget")


async def _budget_json(payload: Any, tool_name: str, token_budget: Optional[int] = None) -> str:
    """Serialize *payload* to JSON, compress if over budget, then enforce it."""
    import json
    budget = token_budget if token_budget is not None else _NON_EVENT_TOKEN_BUDGET
    text = json.dumps(payload, default=str)  # compact (no indent) — replayed each ReAct turn
    return await _compress_then_budget(text, tool_name, budget)


# Severity label → budget class. Anything unlabelled is treated as info.
_SEV_CLASS = {"critical": "error", "high": "error", "medium": "warn", "low": "info", "none": "info"}

# Example-shedding tiers, cheapest concession first. A pattern seen thousands of
# times is fully described by its template — the example message adds a sample of
# something the model already knows recurs. A pattern seen twice is the opposite:
# the raw text *is* the evidence. So spend the example budget on the rare ones.
_SHED_TIERS = (
    (50, 200),   # count > 50  → clip example to 200 chars
    (50, 0),     # count > 50  → drop example entirely
    (5, 200),    # count > 5   → clip example to 200 chars
)


def _shed_examples(patterns: List[Any], summary: Dict[str, Any], budget: int) -> List[Any]:
    """Trade example verbosity for pattern coverage, cheapest concession first.

    ``example_message`` dominates per-entry cost (~180 of ~200 tokens on a live
    sample), so dropping whole patterns to fit the budget throws away distinct
    findings to preserve sample text the model mostly doesn't need. Shedding
    examples from high-volume patterns first keeps more *distinct* patterns in
    front of the model, which is what an investigation actually needs.

    Returns the original list untouched once the summary fits, so a payload
    that was already within budget is never degraded.
    """
    import json

    def fits(ps: List[Any]) -> bool:
        probe = dict(summary)
        probe["unique_patterns"] = ps
        return _count_tokens(json.dumps(probe, default=str)) <= budget

    current = patterns
    for min_count, keep_chars in _SHED_TIERS:
        if fits(current):
            return current
        shed = []
        for p in current:
            if not isinstance(p, dict):
                shed.append(p)
                continue
            try:
                occ = float(p.get("occurrence_count") or 0)
            except (TypeError, ValueError):
                occ = 0.0
            example = p.get("example_message")
            if occ <= min_count or not example or len(example) <= keep_chars:
                shed.append(p)
                continue
            entry = dict(p)
            if keep_chars <= 0:
                entry.pop("example_message", None)
                entry["example_omitted"] = "high-volume pattern; template above"
            else:
                entry["example_message"] = example[: keep_chars - 1] + "…"
            shed.append(entry)
        current = shed
    return current


def _note_query_fixup(original: str, fixed: str) -> str:
    """Describe a silently-applied query fix-up, or '' when nothing changed.

    ``fixup_insights_query`` injects a missing ``| limit`` so an almost-correct
    agent query still runs. That bound was invisible in the result, so a model
    that asked an unbounded question got a bounded answer and no way to know it —
    the same failure mode as a positional truncation, one layer up. Saying so
    costs a line and lets the agent re-query with an explicit limit.
    """
    if not fixed or fixed == original:
        return ""
    if "limit" in fixed.lower() and "limit" not in (original or "").lower():
        return (f"no `| limit` was given, so `| limit {_DEFAULT_FIXUP_LIMIT}` "
                f"was applied")
    return "query auto-corrected before running"


def _note_result_bounds(raw_result: Any, fixup_note: str) -> str:
    """Tell the model how much of the matching result set it is actually seeing.

    Insights reports ``statistics.recordsMatched`` alongside the rows it returns,
    and ``search_logs_multi`` caps every query at 100 rows at the API level —
    independently of any ``| limit`` in the query. So an agent asking "how many
    auth errors?" can receive 100 rows out of tens of thousands and, with no
    disclosure, reasonably conclude it saw them all. Surfacing matched-vs-shown
    turns a silent cap into a fact the model can act on (narrow the window, or
    use stats aggregation instead of raw rows).
    """
    import json

    parts: List[str] = []
    if fixup_note:
        parts.append(fixup_note)

    try:
        payload = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        if isinstance(payload, dict):
            shown = len(payload.get("results") or [])
            matched = int(float((payload.get("statistics") or {}).get("recordsMatched") or 0))
            if matched > shown > 0:
                parts.append(
                    f"showing {shown} of {matched:,} matching rows — this is a "
                    f"capped sample, not the full result set"
                )
    except (ValueError, TypeError):
        pass  # not JSON, or an unexpected shape — the fix-up note still stands

    return "\n[" + "; ".join(parts) + "]" if parts else ""


def _fit_patterns_to_budget(
    summary: Dict[str, Any], budget: int, tool_name: str,
) -> Dict[str, Any]:
    """Fit ``unique_patterns`` into *budget* by severity share, not by position.

    ``_budget_text`` is a positional cut — it keeps the head and discards the
    tail regardless of content, so a rare root-cause exception that ranking
    correctly promoted into the payload can still be deleted at serialisation
    time. Here the scaffold (buckets, data_quality, time_range, drill preview) is
    measured first, and whatever budget remains is split across severity classes
    (error / warn / info) so overflow drops the least diagnostic entries instead
    of the last ones. Unused share is redistributed, so a summary with no
    warnings still spends the full budget on errors.

    Returns *summary* unchanged when it already fits. Deterministic: the input
    order is preserved within each class, so ranking decides priority and this
    only decides how many survive.
    """
    import json

    patterns = summary.get("unique_patterns")
    if not isinstance(patterns, list) or not patterns:
        return summary
    if _count_tokens(json.dumps(summary, default=str)) <= budget:
        return summary  # fits already — nothing to allocate

    patterns = _shed_examples(patterns, summary, budget)

    scaffold = {k: v for k, v in summary.items() if k != "unique_patterns"}
    scaffold["unique_patterns"] = []
    overhead = _count_tokens(json.dumps(scaffold, default=str))
    # 5% headroom absorbs the separators and the truncation-note keys added below.
    available = int((budget - overhead) * 0.95)
    if available <= 0:
        logger.warning(
            "CW tool [%s]: scaffold alone (%d tokens) exhausts budget %d — "
            "falling back to positional truncation.", tool_name, overhead, budget,
        )
        return summary

    from app.config import settings as _s
    shares = {
        "error": max(0.0, _s.cloudwatch_budget_share_error),
        "warn": max(0.0, _s.cloudwatch_budget_share_warn),
    }
    shares["info"] = max(0.0, 1.0 - shares["error"] - shares["warn"])

    buckets: Dict[str, List[Any]] = {"error": [], "warn": [], "info": []}
    for p in patterns:
        cls = _SEV_CLASS.get(str((p or {}).get("severity") or "").lower(), "info")
        buckets[cls].append(p)

    # Cost each entry once; the +1 covers the comma joining it to the previous.
    costs = {
        cls: [_count_tokens(json.dumps(p, default=str)) + 1 for p in items]
        for cls, items in buckets.items()
    }

    # Pass 1: spend each class's own share. Pass 2: hand leftover to classes that
    # ran out, strongest severity first, so an all-error summary isn't capped at
    # 70% of the budget just because there were no warnings to spend the rest.
    kept: Dict[str, int] = {}
    spent = 0
    for cls in ("error", "warn", "info"):
        allowance = int(available * shares[cls])
        used = 0
        n = 0
        for cost in costs[cls]:
            if used + cost > allowance:
                break
            used += cost
            n += 1
        kept[cls] = n
        spent += used

    leftover = available - spent
    for cls in ("error", "warn", "info"):
        if leftover <= 0:
            break
        for cost in costs[cls][kept[cls]:]:
            if cost > leftover:
                break
            leftover -= cost
            kept[cls] += 1

    survivors = {id(p) for cls in buckets for p in buckets[cls][: kept[cls]]}
    out = dict(summary)
    # NB: `patterns` here is the *shed* list, not summary["unique_patterns"] —
    # returning the original would hand back the full-example payload that
    # didn't fit in the first place.
    out["unique_patterns"] = [p for p in patterns if id(p) in survivors]
    dropped = len(patterns) - len(out["unique_patterns"])
    if not dropped:
        return out  # shedding alone got it under budget; every pattern survives
    # Tell the model the list is partial — an agent that thinks it saw every
    # pattern will happily conclude a root cause from an incomplete set.
    out["unique_patterns_truncated"] = True
    out["unique_patterns_dropped_for_budget"] = dropped
    logger.info(
        "CW tool [%s]: severity-allocated %d/%d patterns into %d tokens "
        "(kept error=%d warn=%d info=%d).",
        tool_name, len(out["unique_patterns"]), len(patterns), available,
        kept["error"], kept["warn"], kept["info"],
    )
    return out


# ------------------------------------------------------------------
# Pydantic input schemas
# ------------------------------------------------------------------

class WatchLogsInput(BaseModel):
    """Input for the cloudwatch_watch_logs tool."""
    log_group_names: List[str] = Field(
        description="List of CloudWatch log group names to watch."
    )
    time_range_minutes: int = Field(
        default=60,
        description="How many minutes of logs to retrieve (default 60).",
    )
    filter_pattern: Optional[str] = Field(
        default=None,
        description="Optional CloudWatch Logs filter pattern.",
    )
    max_events_per_group: int = Field(
        default=100,
        ge=1,
        le=500,
        description=(
            "Maximum log events to return per group (default 100, max 500). "
            "Lower values keep observations compact; raise it only when you "
            "need a wider sample for pattern discovery."
        ),
    )
    regions: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of AWS regions to query in parallel, e.g. "
            "['us-east-1', 'eu-west-1']. Each group is queried in every "
            "region; results include a 'region' field on each event."
        ),
    )
    drill_down: bool = Field(
        default=False,
        description=(
            "When true, return up to 25 events with 480-char error tails "
            "and a 2500-token budget. Use after triage, not as first call."
        ),
    )


class AnalyzePatternsInput(BaseModel):
    """Input for the cloudwatch_analyze_patterns tool."""
    log_group_names: List[str] = Field(
        description="List of CloudWatch log group names to analyse."
    )
    time_range_minutes: int = Field(
        default=60,
        description="How many minutes to analyse (default 60).",
    )
    pattern_types: Optional[List[str]] = Field(
        default=None,
        description="Types of patterns to look for, e.g. ['error', 'warning']. Defaults to all.",
    )


class DetectAnomaliesInput(BaseModel):
    """Input for the cloudwatch_detect_anomalies tool."""
    log_group_names: List[str] = Field(
        description="List of CloudWatch log group names to check."
    )
    time_range_minutes: int = Field(
        default=60,
        description="Current window to analyse (default 60).",
    )
    baseline_minutes: int = Field(
        default=1440,
        description="Baseline window in minutes (default 1440 = 24h).",
    )
    sensitivity: str = Field(
        default="medium",
        description="Global sensitivity: 'low', 'medium', or 'high'.",
    )
    per_group_sensitivity: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Per-log-group sensitivity overrides. Keys are log group names, "
            "values are 'low', 'medium', or 'high'. Groups not listed use "
            "the global 'sensitivity'."
        ),
    )


class CorrelateCrossGroupInput(BaseModel):
    """Input for the cloudwatch_correlate_logs tool."""
    log_group_names: List[str] = Field(
        description="List of CloudWatch log group names to correlate."
    )
    time_range_minutes: int = Field(
        default=60,
        description="Time window in minutes (default 60).",
    )
    correlation_id: Optional[str] = Field(
        default=None,
        description="Correlation/request ID to trace.",
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="AWS X-Ray trace ID to correlate.",
    )


class SearchLogsInput(BaseModel):
    """Input for the cloudwatch_search_logs tool."""
    log_group_names: List[str] = Field(
        description="List of CloudWatch log group names to search."
    )
    query: str = Field(
        description="CloudWatch Logs Insights query string.",
    )
    hours: int = Field(
        default=24,
        description="Number of hours to look back (default 24).",
    )
    drill_down: bool = Field(
        default=False,
        description=(
            "When true, use a larger token budget (2500 vs 1000) and preserve "
            "error message tails (stack traces). Use only after triage tools "
            "identify a suspect pattern — not on the first call."
        ),
    )


class DiscoverLogGroupsInput(BaseModel):
    """Input for the cloudwatch_discover_log_groups tool."""
    prefix: Optional[str] = Field(
        default=None,
        description=(
            "Log group name prefix, e.g. '/aws/lambda/kyc-' to find all "
            "KYC Lambda log groups.  Omit to list all groups up to *limit*."
        ),
    )
    tag_key: Optional[str] = Field(
        default=None,
        description="Tag key to filter by, e.g. 'Environment'.",
    )
    tag_value: Optional[str] = Field(
        default=None,
        description="Tag value to filter by, e.g. 'production'. Requires tag_key.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of log groups to return (default 50).",
    )


class GetMetricDataInput(BaseModel):
    """Input for the cloudwatch_get_metric_data tool."""
    metric_queries: List[Dict[str, Any]] = Field(
        description=(
            "List of MetricDataQuery dicts (boto3 shape). Each must have: "
            "Id (str), MetricStat.Metric.Namespace, "
            "MetricStat.Metric.MetricName, MetricStat.Period (int seconds), "
            "MetricStat.Stat (e.g. 'Sum'). Optionally include Label. "
            "Example: [{\"Id\": \"errors\", \"MetricStat\": {\"Metric\": "
            "{\"Namespace\": \"AWS/Lambda\", \"MetricName\": \"Errors\", "
            "\"Dimensions\": [{\"Name\": \"FunctionName\", \"Value\": \"my-fn\"}]}, "
            "\"Period\": 300, \"Stat\": \"Sum\"}}]"
        )
    )
    time_range_minutes: int = Field(
        default=60,
        description="How far back to query (default 60 minutes).",
    )
    include_series: bool = Field(
        default=False,
        description=(
            "When false (default), return only the per-metric summary "
            "(count/total/average/max/min/latest) to stay token-compact. "
            "Set true only when you need the full timestamp/value arrays."
        ),
    )


class GetMetricStatisticsInput(BaseModel):
    """Input for the cloudwatch_get_metric_statistics tool."""
    namespace: str = Field(
        description="CloudWatch namespace, e.g. 'AWS/Lambda' or 'AWS/ApplicationELB'.",
    )
    metric_name: str = Field(
        description="Metric name, e.g. 'Errors', 'Duration', 'HTTPCode_ELB_5XX_Count'.",
    )
    dimensions: List[Dict[str, str]] = Field(
        description=(
            "List of {Name, Value} pairs, e.g. "
            "[{\"Name\": \"FunctionName\", \"Value\": \"kyc-auth\"}]."
        ),
    )
    statistics: Optional[List[str]] = Field(
        default=None,
        description=(
            "Statistics to return: Sum, Average, Maximum, Minimum, "
            "SampleCount. Defaults to all five."
        ),
    )
    period_seconds: int = Field(
        default=300,
        description="Aggregation period in seconds (default 300 = 5 min).",
    )
    time_range_minutes: int = Field(
        default=60,
        description="How far back to query (default 60 minutes).",
    )


class DiscoverMetricsInput(BaseModel):
    """Input for the cloudwatch_discover_metrics tool."""
    namespace: Optional[str] = Field(
        default=None,
        description=(
            "CloudWatch metric namespace to list, e.g. 'AWS/Lambda'. If omitted, "
            "namespaces are inferred from log_group_names."
        ),
    )
    log_group_names: Optional[List[str]] = Field(
        default=None,
        description=(
            "Log groups to infer namespaces/dimensions from, e.g. "
            "['/aws/lambda/kyc-auth'] → AWS/Lambda FunctionName=kyc-auth."
        ),
    )
    limit: int = Field(
        default=50, ge=1, le=200,
        description="Maximum metrics to return (default 50).",
    )


class ListAlarmsInput(BaseModel):
    """Input for the cloudwatch_list_alarms tool."""
    alarm_name_prefix: Optional[str] = Field(
        default=None,
        description="Filter alarms whose name starts with this prefix.",
    )
    state_value: Optional[str] = Field(
        default=None,
        description=(
            "Filter by state: 'ALARM', 'OK', or 'INSUFFICIENT_DATA'. "
            "Use 'ALARM' to get only currently firing alarms."
        ),
    )
    max_records: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum alarms to return (default 100).",
    )


# ------------------------------------------------------------------
# Builder
# ------------------------------------------------------------------

def build_cloudwatch_agent_tools(
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
    log_groups: Optional[List[str]] = None,
    severity_excludes: Optional[List[str]] = None,
) -> List[StructuredTool]:
    """Create LangChain tools that let the ReAct agent call CloudWatch functions.

    Args:
        region: AWS region for all CloudWatch calls.
        credentials: Pre-resolved credentials dict (from
            :func:`app.core.aws.aws_credentials.resolve_aws_credentials`).
        log_groups: Optional default log groups from the node config.  If
            provided they are mentioned in the tool descriptions to guide the
            agent, but the agent is still free to specify different groups.

    Returns:
        List of LangChain ``StructuredTool`` instances.
    """
    from app.mcp.tools.watch_tools import (
        watch_log_groups,
        analyze_log_patterns,
        detect_anomalies,
        correlate_logs,
        discover_log_groups,
    )
    from app.mcp.tools.metrics_tools import (
        get_metric_data,
        get_metric_statistics,
        list_metric_alarms,
        discover_metrics,
    )

    _creds = credentials or {}
    _region = region
    _severity_excludes = severity_excludes

    async def _execute_insights_search(
        log_group_names: List[str],
        query: str,
        time_range_minutes: int,
        *,
        drill_down: bool = True,
    ) -> str:
        """Run one Insights query and return sanitized event text."""
        from datetime import datetime, timedelta, timezone as _tz
        from app.mcp.tools.watch_tools import get_watcher, _extract_credentials

        hours = max(1, min(int(_clamp(time_range_minutes) / 60) or 1, 24))
        watcher = get_watcher(region=_region, **_extract_credentials(_creds))
        end = datetime.now(_tz.utc)
        start = end - timedelta(hours=hours)
        result = await watcher.query_with_insights(
            log_group_names=log_group_names,
            query_string=query,
            start_time=start,
            end_time=end,
            limit=500,
        )
        if _has_events(result):
            return _sanitizer.sanitize(
                result,
                "cloudwatch_auto_drilldown",
                {"log_groups": log_group_names, "query": query[:200]},
                drill_down=drill_down,
            )
        import json
        return json.dumps(result, default=str)[:2000]

    async def _enrich_with_kb_and_drill(
        summary: Dict[str, Any],
        log_group_names: List[str],
        time_range_minutes: int,
        *,
        tool_name: str,
    ) -> Dict[str, Any]:
        """Attach KB recall + optional auto drill-down preview to a triage summary."""
        patterns = summary.get("unique_patterns") or []
        if patterns:
            top = patterns[0]
            kb = await recall_kb_for_pattern(
                top.get("normalized_pattern", ""),
                top.get("example_message", ""),
            )
            if kb:
                summary["known_patterns"] = kb
        elif log_group_names:
            kb = await recall_kb_for_log_groups(log_group_names)
            if kb:
                summary["known_patterns"] = kb

        summary = await attach_metrics_context(
            summary,
            log_group_names,
            time_range_minutes,
            _region,
            _creds if _creds else None,
        )

        if not should_auto_drill_down(summary):
            return summary

        target = pick_drill_target(summary)
        if not target:
            return summary

        try:
            if target.get("kind") == "pattern":
                query = build_insights_query_for_pattern(
                    target.get("normalized_pattern", ""),
                    target.get("example_message", ""),
                )
            else:
                query = build_insights_query_for_anomaly(
                    target.get("log_group"),
                )
            preview = await _execute_insights_search(
                log_group_names,
                query,
                time_range_minutes,
                drill_down=True,
            )
            summary["auto_drill_down"] = {
                "trigger": summary.get("evidence_grade"),
                "target": target.get("normalized_pattern") or target.get("log_group"),
                "preview": cap_drill_preview(preview),
            }
            logger.info(
                "CW [%s]: auto drill-down attached (grade=%s)",
                tool_name, summary.get("evidence_grade"),
            )
        except Exception as exc:
            logger.warning("CW auto drill-down failed (non-fatal): %s", exc)
        return summary

    log_group_hint = ""
    if log_groups:
        log_group_hint = (
            f" The workflow has these log groups pre-configured: {log_groups}."
            " You can use them or specify different ones."
        )

    # ──────────────────────────────────────────────────────────────────
    # Token-efficiency helpers.  These wrap raw MCP responses so the
    # agent observes compact aggregates rather than raw event blobs.
    # ──────────────────────────────────────────────────────────────────
    # Configurable hard cap (Phase 4) — defaults to 24h. Raising
    # cloudwatch_max_time_range_minutes lets longer ranges through; watch_tools
    # then buckets each Insights query to keep it within bucket_minutes.
    from app.config import settings as _cw_settings
    MAX_MINUTES = max(1, int(_cw_settings.cloudwatch_max_time_range_minutes))

    def _clamp(m: Any) -> int:
        """Clamp a time-range value to [1, MAX_MINUTES] minutes, logging if clipped."""
        try:
            mi = int(m)
        except (TypeError, ValueError):
            mi = 60
        if mi > MAX_MINUTES:
            logger.warning("CW tool: clamping time_range_minutes %d → %d", mi, MAX_MINUTES)
            return MAX_MINUTES
        if mi < 1:
            return 1
        return mi

    def _truncate(s: Any, n: int = 200) -> str:
        s = "" if s is None else str(s)
        return s if len(s) <= n else (s[: n - 1] + "…")

    def _summarise_watch_logs(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Compress watch_log_groups output: totals + top messages + 10 samples.

        ``watch_log_groups`` returns a per-group dict with full event arrays.
        On a busy 24h window this is easily multi-MB. We aggregate to:

        * totals: events / errors / warnings counts
        * by_group: per-group event counts
        * top_messages: top-10 normalised messages with one example each
        * sample_events: first 10 raw events (for grep / drill-down)
        """
        if not isinstance(raw, dict):
            return {"error": "unexpected_response", "raw_type": type(raw).__name__}

        groups = raw.get("log_groups") or {}
        by_group: Dict[str, int] = {}
        totals = {"events": 0, "errors": 0, "warnings": 0}
        sample_events: List[Dict[str, Any]] = []
        msg_counts: Dict[str, Dict[str, Any]] = {}

        import re
        _norm_re = re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
            r"|\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*"
            r"|\b\d{1,3}(?:\.\d{1,3}){3}\b"
            r"|\b\d{6,}\b"
        )

        for group_key, group in (groups.items() if isinstance(groups, dict) else []):
            evs = group.get("events", []) if isinstance(group, dict) else []
            count = len(evs)
            by_group[group_key] = count
            totals["events"] += count
            for ev in evs:
                msg = (ev.get("message") or "") if isinstance(ev, dict) else ""
                low = msg.lower()
                if "error" in low or "exception" in low or "fatal" in low:
                    totals["errors"] += 1
                elif "warn" in low:
                    totals["warnings"] += 1
                norm = _norm_re.sub("«var»", msg)[:200] or "(empty)"
                bucket = msg_counts.setdefault(norm, {"count": 0, "example": msg})
                bucket["count"] += 1
                if len(sample_events) < 10:
                    sample_events.append({
                        "group":     group_key,
                        "timestamp": ev.get("timestamp") if isinstance(ev, dict) else None,
                        "message":   _truncate(msg, 300),
                    })

        top_messages = sorted(
            (
                {"normalized": k, "count": v["count"], "example": _truncate(v["example"], 200)}
                for k, v in msg_counts.items()
            ),
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        return {
            "totals":        totals,
            "by_group":      by_group,
            "top_messages":  top_messages,
            "sample_events": sample_events,
            "summary":       raw.get("summary") or {},
            "errors":        raw.get("errors") or [],
        }

    def _summarise_alarms(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Compress list_metric_alarms output to save tokens."""
        if not isinstance(raw, dict):
            return {"error": "unexpected_response", "raw_type": type(raw).__name__}
        alarms = raw.get("alarms") or []
        state_priority = {"ALARM": 0, "INSUFFICIENT_DATA": 1, "OK": 2}
        sorted_alarms = sorted(
            alarms,
            key=lambda a: state_priority.get(a.get("state"), 3)
        )
        compact_alarms = []
        for a in sorted_alarms[:20]:
            compact_alarms.append({
                "name": a.get("name"),
                "state": a.get("state"),
                "reason": _truncate(a.get("reason"), 150),
                "metric": a.get("metric_name"),
                "namespace": a.get("namespace"),
            })
        return {
            "success": raw.get("success", True),
            "summary": raw.get("summary") or {},
            "alarms": compact_alarms,
            "region": raw.get("region"),
        }

    # -- watch logs --
    async def _watch_logs(
        log_group_names: List[str],
        time_range_minutes: int = 60,
        filter_pattern: Optional[str] = None,
        max_events_per_group: int = 100,
        regions: Optional[List[str]] = None,
        drill_down: bool = False,
    ) -> str:
        import json
        result = await watch_log_groups(
            log_group_names=log_group_names,
            time_range_minutes=_clamp(time_range_minutes),
            filter_pattern=filter_pattern,
            region=_region,
            regions=regions,
            credentials=_creds if _creds else None,
            max_events_per_group=min(max_events_per_group, 500),
        )
        if _has_events(result):
            return _sanitizer.sanitize(
                result,
                "cloudwatch_watch_logs",
                {"log_groups": log_group_names, "time_range_minutes": time_range_minutes},
                drill_down=drill_down,
            )
        return await _budget_json(_summarise_watch_logs(result), "cloudwatch_watch_logs")

    # -- analyse patterns --
    async def _analyze_patterns(
        log_group_names: List[str],
        time_range_minutes: int = 60,
        pattern_types: Optional[List[str]] = None,
    ) -> str:
        import json
        result = await analyze_log_patterns(
            log_group_names=log_group_names,
            time_range_minutes=_clamp(time_range_minutes),
            pattern_types=pattern_types,
            region=_region,
            credentials=_creds if _creds else None,
            severity_excludes=_severity_excludes,
        )
        summary = summarise_patterns(result)
        summary = await _enrich_with_kb_and_drill(
            summary, log_group_names, time_range_minutes,
            tool_name="cloudwatch_analyze_patterns",
        )
        budget = 3200 if summary.get("auto_drill_down") else _NON_EVENT_TOKEN_BUDGET
        from app.config import settings as _pat_settings
        if _pat_settings.cloudwatch_pattern_budget_allocation:
            summary = _fit_patterns_to_budget(
                summary, budget, "cloudwatch_analyze_patterns")
        return await _budget_json(summary, "cloudwatch_analyze_patterns", token_budget=budget)

    # -- detect anomalies --
    async def _detect_anomalies(
        log_group_names: List[str],
        time_range_minutes: int = 60,
        baseline_minutes: int = 1440,
        sensitivity: str = "medium",
        per_group_sensitivity: Optional[Dict[str, str]] = None,
    ) -> str:
        import json
        result = await detect_anomalies(
            log_group_names=log_group_names,
            time_range_minutes=_clamp(time_range_minutes),
            baseline_minutes=_clamp(baseline_minutes),
            sensitivity=sensitivity,
            per_group_sensitivity=per_group_sensitivity,
            region=_region,
            credentials=_creds if _creds else None,
        )
        summary = summarise_anomalies(result)
        summary = await _enrich_with_kb_and_drill(
            summary, log_group_names, time_range_minutes,
            tool_name="cloudwatch_detect_anomalies",
        )
        budget = 3200 if summary.get("auto_drill_down") else _NON_EVENT_TOKEN_BUDGET
        return await _budget_json(summary, "cloudwatch_detect_anomalies", token_budget=budget)

    # -- correlate logs --
    async def _correlate_logs(
        log_group_names: List[str],
        time_range_minutes: int = 60,
        correlation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> str:
        import json
        result = await correlate_logs(
            log_group_names=log_group_names,
            correlation_id=correlation_id,
            time_range_minutes=_clamp(time_range_minutes),
            trace_id=trace_id,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return await _budget_json(summarise_correlation(result), "cloudwatch_correlate_logs")

    # -- search logs (Insights query) --
    async def _search_logs(
        log_group_names: List[str],
        query: str,
        hours: int = 24,
        drill_down: bool = False,
    ) -> str:
        import json
        budget = 2500 if drill_down else _NON_EVENT_TOKEN_BUDGET
        # Hard-cap to 24h to match the global window policy.
        hours = max(1, min(int(hours or 24), 24))
        # Validate the Insights query before hitting AWS — a clear corrective
        # hint costs nothing and saves an investigation iteration that a raw AWS
        # ValidationException would otherwise burn. In fix-up mode (default) a
        # missing `| limit` clause is injected rather than rejected; genuinely
        # broken queries still hard-fail with a hint.
        from app.config import settings as _settings
        _fixup_note = ""
        if _settings.cloudwatch_insights_query_fixup:
            from app.workflow.tools.cloudwatch_drilldown import fixup_insights_query
            _original_query = query
            query, _err = fixup_insights_query(query)
            if _err:
                return f"Query not run — fix the Insights query first. {_err}"
            _fixup_note = _note_query_fixup(_original_query, query)
        else:
            _lint = lint_insights_query(query)
            if _lint:
                return f"Query not run — fix the Insights query first. {_lint}"
        try:
            from app.mcp.tools.search_tools import CloudWatchLogsSearchTools
        except ImportError:
            from app.mcp.tools.watch_tools import get_watcher, _extract_credentials
            from datetime import datetime, timedelta, timezone as _tz
            watcher = get_watcher(region=_region, **_extract_credentials(_creds))
            end = datetime.now(_tz.utc)
            start = end - timedelta(hours=hours)
            result = await watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=query,
                start_time=start,
                end_time=end,
            )
            if _has_events(result):
                return _sanitizer.sanitize(
                    result,
                    "cloudwatch_search_logs",
                    {"log_groups": log_group_names, "query": query, "hours": hours},
                    drill_down=drill_down,
                ) + _note_result_bounds(result, _fixup_note)
            _b = _note_result_bounds(result, _fixup_note)
            if _b and isinstance(result, dict):
                result = {**result, "result_bounds": _b.strip("\n[]")}
            return await _budget_json(result, "cloudwatch_search_logs", token_budget=budget)
        search = CloudWatchLogsSearchTools(
            profile_name=_creds.get("aws_profile"),
            region_name=_region,
        )
        result = await search.search_logs_multi(
            log_group_names=log_group_names,
            query=query,
            hours=hours,
        )
        if isinstance(result, str):
            # Raw Insights output — the single largest tool result the agent
            # sees (138,972 tokens against a 2,500 budget, measured 2026-07-23)
            # and pure log lines, which the sidecar compresses ~96% while
            # keeping the ERROR rows a head-slice would have thrown away.
            # Read the bounds off the raw payload before compression rewrites it.
            _bounds = _note_result_bounds(result, _fixup_note)
            return await _compress_then_budget(
                result, "cloudwatch_search_logs", budget
            ) + _bounds
        if _has_events(result):
            return _sanitizer.sanitize(
                result,
                "cloudwatch_search_logs",
                {"log_groups": log_group_names, "query": query, "hours": hours},
                drill_down=drill_down,
            ) + _note_result_bounds(result, _fixup_note)
        _b2 = _note_result_bounds(result, _fixup_note)
        if _b2 and isinstance(result, dict):
            result = {**result, "result_bounds": _b2.strip("\n[]")}
        return await _budget_json(result, "cloudwatch_search_logs", token_budget=budget)

    # -- discover log groups --
    async def _discover_log_groups(
        prefix: Optional[str] = None,
        tag_key: Optional[str] = None,
        tag_value: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        import json
        result = await discover_log_groups(
            prefix=prefix,
            tag_key=tag_key,
            tag_value=tag_value,
            limit=limit,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return await _budget_json(result, "cloudwatch_discover_log_groups")

    # -- get metric data --
    async def _get_metric_data(
        metric_queries: List[Dict[str, Any]],
        time_range_minutes: int = 60,
        include_series: bool = False,
    ) -> str:
        import json
        result = await get_metric_data(
            metric_queries=metric_queries,
            time_range_minutes=_clamp(time_range_minutes),
            region=_region,
            credentials=_creds if _creds else None,
            include_series=include_series,
        )
        return await _budget_json(result, "cloudwatch_get_metric_data")

    # -- get metric statistics --
    async def _get_metric_statistics(
        namespace: str,
        metric_name: str,
        dimensions: List[Dict[str, str]],
        statistics: Optional[List[str]] = None,
        period_seconds: int = 300,
        time_range_minutes: int = 60,
    ) -> str:
        import json
        result = await get_metric_statistics(
            namespace=namespace,
            metric_name=metric_name,
            dimensions=dimensions,
            statistics=statistics,
            period_seconds=period_seconds,
            time_range_minutes=_clamp(time_range_minutes),
            region=_region,
            credentials=_creds if _creds else None,
        )
        return await _budget_json(result, "cloudwatch_get_metric_statistics")

    # -- discover metrics --
    async def _discover_metrics(
        namespace: Optional[str] = None,
        log_group_names: Optional[List[str]] = None,
        limit: int = 50,
    ) -> str:
        result = await discover_metrics(
            namespace=namespace,
            log_group_names=log_group_names or log_groups,
            region=_region,
            credentials=_creds if _creds else None,
            limit=limit,
        )
        return await _budget_json(result, "cloudwatch_discover_metrics")

    # -- list alarms --
    async def _list_alarms(
        alarm_name_prefix: Optional[str] = None,
        state_value: Optional[str] = None,
        max_records: int = 100,
    ) -> str:
        import json
        from app.config import settings as _settings
        result = await list_metric_alarms(
            alarm_name_prefix=alarm_name_prefix,
            state_value=state_value,
            region=_region,
            credentials=_creds if _creds else None,
            max_records=max_records,
            include_history=_settings.cloudwatch_alarm_history,
        )
        return await _budget_json(_summarise_alarms(result), "cloudwatch_list_alarms")

    tools = [
        StructuredTool.from_function(
            coroutine=_watch_logs,
            name="cloudwatch_watch_logs",
            description=(
                "Fetch recent log events from one or more CloudWatch log groups. "
                "Supports multi-region queries and configurable event limits. "
                "Use for raw log inspection; set drill_down=true only after triage "
                "to get longer error tails (2500-token budget)."
                + log_group_hint
            ),
            args_schema=WatchLogsInput,
        ),
        StructuredTool.from_function(
            coroutine=_analyze_patterns,
            name="cloudwatch_analyze_patterns",
            description=(
                "Analyse error, warning, and info patterns in CloudWatch logs. "
                "Returns time-bucketed counts and semantically-deduplicated unique "
                "error patterns (UUIDs/timestamps/IDs stripped for grouping). "
                "Use this when investigating error spikes or trends."
                + log_group_hint
            ),
            args_schema=AnalyzePatternsInput,
        ),
        StructuredTool.from_function(
            coroutine=_detect_anomalies,
            name="cloudwatch_detect_anomalies",
            description=(
                "Detect anomalies by comparing recent log error counts against a "
                "historical baseline using z-score analysis. Supports per-group "
                "sensitivity overrides. Use this to spot unusual activity or "
                "error rate spikes."
                + log_group_hint
            ),
            args_schema=DetectAnomaliesInput,
        ),
        StructuredTool.from_function(
            coroutine=_correlate_logs,
            name="cloudwatch_correlate_logs",
            description=(
                "Correlate log events across multiple services using a correlation "
                "ID or X-Ray trace ID. Returns a timeline of related events. "
                "Use this to trace a request across microservices."
                + log_group_hint
            ),
            args_schema=CorrelateCrossGroupInput,
        ),
        StructuredTool.from_function(
            coroutine=_search_logs,
            name="cloudwatch_search_logs",
            description=(
                "Run a CloudWatch Logs Insights query across one or more log groups. "
                "Use for custom queries after triage; set drill_down=true to preserve "
                "error stack-trace tails (2500-token budget vs 1000 default)."
                + log_group_hint
            ),
            args_schema=SearchLogsInput,
        ),
        StructuredTool.from_function(
            coroutine=_discover_log_groups,
            name="cloudwatch_discover_log_groups",
            description=(
                "Discover CloudWatch log groups by name prefix or resource tags. "
                "Use this when you know a service name or tag but not the exact "
                "log group path, e.g. prefix='/aws/lambda/kyc-' to find all KYC "
                "Lambda log groups."
            ),
            args_schema=DiscoverLogGroupsInput,
        ),
        StructuredTool.from_function(
            coroutine=_get_metric_data,
            name="cloudwatch_get_metric_data",
            description=(
                "Query CloudWatch Metrics using GetMetricData. Supports up to 500 "
                "metrics per call. Returns time-series values for infrastructure "
                "metrics like CPU, Lambda errors/duration, ALB 5xx rates, etc. "
                "Use this as the first check during an incident to see metric spikes."
            ),
            args_schema=GetMetricDataInput,
        ),
        StructuredTool.from_function(
            coroutine=_get_metric_statistics,
            name="cloudwatch_get_metric_statistics",
            description=(
                "Query a single CloudWatch metric via GetMetricStatistics. "
                "Simpler than cloudwatch_get_metric_data for one-metric lookups. "
                "Returns datapoints with Sum/Average/Max/Min/SampleCount."
            ),
            args_schema=GetMetricStatisticsInput,
        ),
        StructuredTool.from_function(
            coroutine=_list_alarms,
            name="cloudwatch_list_alarms",
            description=(
                "List CloudWatch Metric Alarms. Use state_value='ALARM' to get only "
                "currently firing alarms — this is typically the first call in any "
                "incident investigation. Returns alarm state, threshold, metric, "
                "last state change time, and flags flapping alarms (frequent state "
                "changes in the last 24h)."
            ),
            args_schema=ListAlarmsInput,
        ),
        StructuredTool.from_function(
            coroutine=_discover_metrics,
            name="cloudwatch_discover_metrics",
            description=(
                "Discover which CloudWatch metrics exist for a service via ListMetrics. "
                "Pass a namespace (e.g. 'AWS/Lambda') or log_group_names to infer it "
                "(e.g. '/aws/lambda/kyc-auth' → AWS/Lambda FunctionName=kyc-auth). Use "
                "this to find metric names/dimensions before cloudwatch_get_metric_data "
                "instead of guessing them."
            ),
            args_schema=DiscoverMetricsInput,
        ),
    ]

    logger.info(
        "build_cloudwatch_agent_tools: created %d CloudWatch tools (region=%s, log_groups=%s)",
        len(tools),
        _region,
        log_groups,
    )
    return tools
