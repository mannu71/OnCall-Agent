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


def _budget_json(payload: Any, tool_name: str) -> str:
    """Serialize *payload* to JSON and enforce a per-call token budget.

    Uses a single encode → slice token IDs → decode pass (O(N)) when tiktoken
    is available, avoiding the O(N log N) binary-search approach.
    """
    import json
    from app.workflow.tools.cloudwatch_sanitizer import _get_encoder
    text = json.dumps(payload, indent=2, default=str)
    enc = _get_encoder()
    if enc is not None:
        token_ids = enc.encode(text)
        if len(token_ids) <= _NON_EVENT_TOKEN_BUDGET:
            return text
        logger.warning(
            "CW tool [%s]: response %d tokens exceeded budget %d — truncated.",
            tool_name, len(token_ids), _NON_EVENT_TOKEN_BUDGET,
        )
        truncated = enc.decode(token_ids[:_NON_EVENT_TOKEN_BUDGET])
        return (
            truncated
            + f"\n...[truncated by token budget: was ~{len(token_ids)} tokens, "
            f"capped at {_NON_EVENT_TOKEN_BUDGET}]"
        )
    # tiktoken unavailable — fall back to char-based approximation
    char_budget = _NON_EVENT_TOKEN_BUDGET * 4
    if len(text) <= char_budget:
        return text
    logger.warning(
        "CW tool [%s]: response ~%d tokens exceeded budget %d — truncated (char heuristic).",
        tool_name, len(text) // 4, _NON_EVENT_TOKEN_BUDGET,
    )
    return (
        text[:char_budget]
        + f"\n...[truncated by token budget: capped at ~{_NON_EVENT_TOKEN_BUDGET} tokens]"
    )


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
) -> List[StructuredTool]:
    """Create LangChain tools that let the ReAct agent call CloudWatch functions.

    Args:
        region: AWS region for all CloudWatch calls.
        credentials: Pre-resolved credentials dict (from
            :func:`app.core.aws_credentials.resolve_aws_credentials`).
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
    )

    _creds = credentials or {}
    _region = region

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
    MAX_MINUTES = 1440  # 24-hour hard cap on every time-range parameter

    def _clamp(m: Any) -> int:
        """Clamp a time-range value to [1, 1440] minutes, logging if clipped."""
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

    def _summarise_patterns(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Strip per-pattern example arrays to a single example string each."""
        if not isinstance(raw, dict):
            return {"error": "unexpected_response", "raw_type": type(raw).__name__}
        out = {k: v for k, v in raw.items() if k != "unique_patterns"}
        compact: List[Dict[str, Any]] = []
        for p in (raw.get("unique_patterns") or [])[:20]:
            if not isinstance(p, dict):
                continue
            examples = p.get("examples") or []
            example = examples[0] if examples else p.get("example", "")
            compact.append({
                "normalized": _truncate(p.get("normalized", ""), 200),
                "count":      p.get("count"),
                "severity":   p.get("severity"),
                "example":    _truncate(example, 200),
            })
        out["unique_patterns"] = compact
        return out

    def _summarise_anomalies(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Keep critical/high/medium anomalies only, cap at 25, drop baseline details."""
        if not isinstance(raw, dict):
            return {"error": "unexpected_response", "raw_type": type(raw).__name__}
        keep = {"critical", "high", "medium"}
        kept: List[Dict[str, Any]] = []
        for a in (raw.get("anomalies") or []):
            if not isinstance(a, dict):
                continue
            if a.get("severity", "low") not in keep:
                continue
            kept.append({
                "log_group":      a.get("log_group"),
                "severity":       a.get("severity"),
                "z_score":        a.get("z_score"),
                "current_count":  a.get("current_count"),
                "baseline_count": a.get("baseline_count"),
                "message":        _truncate(a.get("message") or a.get("normalized", ""), 200),
            })
        kept.sort(key=lambda x: (x.get("z_score") or 0), reverse=True)
        return {
            "anomalies": kept[:25],
            "summary":   raw.get("summary") or {},
            "filtered_out_low_severity": True,
        }

    def _summarise_correlation(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Cap timeline to 100 events for token budget."""
        if not isinstance(raw, dict):
            return {"error": "unexpected_response", "raw_type": type(raw).__name__}
        timeline = raw.get("timeline") or []
        truncated = len(timeline) > 100
        out = {k: v for k, v in raw.items() if k != "timeline"}
        out["timeline"] = timeline[:100]
        out["timeline_truncated"] = truncated
        out["timeline_total"] = len(timeline)
        return out

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
            )
        return _budget_json(_summarise_watch_logs(result), "cloudwatch_watch_logs")

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
        )
        return _budget_json(_summarise_patterns(result), "cloudwatch_analyze_patterns")

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
        return _budget_json(_summarise_anomalies(result), "cloudwatch_detect_anomalies")

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
        return _budget_json(_summarise_correlation(result), "cloudwatch_correlate_logs")

    # -- search logs (Insights query) --
    async def _search_logs(
        log_group_names: List[str],
        query: str,
        hours: int = 24,
    ) -> str:
        import json
        # Hard-cap to 24h to match the global window policy.
        hours = max(1, min(int(hours or 24), 24))
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
                )
            return _budget_json(result, "cloudwatch_search_logs")
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
            tokens = _count_tokens(result)
            if tokens <= _NON_EVENT_TOKEN_BUDGET:
                return result
            logger.warning(
                "CW tool [cloudwatch_search_logs]: string response %d tokens > %d budget — truncating.",
                tokens, _NON_EVENT_TOKEN_BUDGET,
            )
            return result[: _NON_EVENT_TOKEN_BUDGET * 4] + "\n...[truncated to token budget]"
        if _has_events(result):
            return _sanitizer.sanitize(
                result,
                "cloudwatch_search_logs",
                {"log_groups": log_group_names, "query": query, "hours": hours},
            )
        return _budget_json(result, "cloudwatch_search_logs")

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
        return _budget_json(result, "cloudwatch_discover_log_groups")

    # -- get metric data --
    async def _get_metric_data(
        metric_queries: List[Dict[str, Any]],
        time_range_minutes: int = 60,
    ) -> str:
        import json
        result = await get_metric_data(
            metric_queries=metric_queries,
            time_range_minutes=_clamp(time_range_minutes),
            region=_region,
            credentials=_creds if _creds else None,
        )
        return _budget_json(result, "cloudwatch_get_metric_data")

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
        return _budget_json(result, "cloudwatch_get_metric_statistics")

    # -- list alarms --
    async def _list_alarms(
        alarm_name_prefix: Optional[str] = None,
        state_value: Optional[str] = None,
        max_records: int = 100,
    ) -> str:
        import json
        result = await list_metric_alarms(
            alarm_name_prefix=alarm_name_prefix,
            state_value=state_value,
            region=_region,
            credentials=_creds if _creds else None,
            max_records=max_records,
        )
        return _budget_json(_summarise_alarms(result), "cloudwatch_list_alarms")

    tools = [
        StructuredTool.from_function(
            coroutine=_watch_logs,
            name="cloudwatch_watch_logs",
            description=(
                "Fetch recent log events from one or more CloudWatch log groups. "
                "Supports multi-region queries and configurable event limits. "
                "Use this to retrieve raw log entries for inspection."
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
                "Use this for custom log queries with full Insights syntax "
                "(fields, filter, stats, sort, etc.)."
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
                "incident investigation. Returns alarm state, threshold, metric, and "
                "last state change time."
            ),
            args_schema=ListAlarmsInput,
        ),
    ]

    logger.info(
        "build_cloudwatch_agent_tools: created %d CloudWatch tools (region=%s, log_groups=%s)",
        len(tools),
        _region,
        log_groups,
    )
    return tools
