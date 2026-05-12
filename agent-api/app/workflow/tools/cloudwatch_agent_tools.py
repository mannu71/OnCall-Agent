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

logger = logging.getLogger(__name__)


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
        default=500,
        ge=1,
        le=2000,
        description="Maximum log events to return per group (default 500, max 2000).",
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

    # -- watch logs --
    async def _watch_logs(
        log_group_names: List[str],
        time_range_minutes: int = 60,
        filter_pattern: Optional[str] = None,
        max_events_per_group: int = 500,
        regions: Optional[List[str]] = None,
    ) -> str:
        import json
        result = await watch_log_groups(
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            filter_pattern=filter_pattern,
            region=_region,
            regions=regions,
            credentials=_creds if _creds else None,
            max_events_per_group=max_events_per_group,
        )
        return json.dumps(result, indent=2, default=str)

    # -- analyse patterns --
    async def _analyze_patterns(
        log_group_names: List[str],
        time_range_minutes: int = 60,
        pattern_types: Optional[List[str]] = None,
    ) -> str:
        import json
        result = await analyze_log_patterns(
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            pattern_types=pattern_types,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return json.dumps(result, indent=2, default=str)

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
            time_range_minutes=time_range_minutes,
            baseline_minutes=baseline_minutes,
            sensitivity=sensitivity,
            per_group_sensitivity=per_group_sensitivity,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return json.dumps(result, indent=2, default=str)

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
            time_range_minutes=time_range_minutes,
            trace_id=trace_id,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return json.dumps(result, indent=2, default=str)

    # -- search logs (Insights query) --
    async def _search_logs(
        log_group_names: List[str],
        query: str,
        hours: int = 24,
    ) -> str:
        import json
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
            return json.dumps(result, indent=2, default=str)
        search = CloudWatchLogsSearchTools(
            profile_name=_creds.get("aws_profile"),
            region_name=_region,
        )
        result = await search.search_logs_multi(
            log_group_names=log_group_names,
            query=query,
            hours=hours,
        )
        return result if isinstance(result, str) else json.dumps(result, indent=2, default=str)

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
        return json.dumps(result, indent=2, default=str)

    # -- get metric data --
    async def _get_metric_data(
        metric_queries: List[Dict[str, Any]],
        time_range_minutes: int = 60,
    ) -> str:
        import json
        result = await get_metric_data(
            metric_queries=metric_queries,
            time_range_minutes=time_range_minutes,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return json.dumps(result, indent=2, default=str)

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
            time_range_minutes=time_range_minutes,
            region=_region,
            credentials=_creds if _creds else None,
        )
        return json.dumps(result, indent=2, default=str)

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
        return json.dumps(result, indent=2, default=str)

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
