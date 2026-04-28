"""CloudWatch tools exposed as LangChain StructuredTools for the ReAct agent.

When a ``cloudwatchAnalyzer`` node is connected to an ``agent`` node in the
workflow graph, the executor calls :func:`build_cloudwatch_agent_tools` to
create LangChain-compatible tool instances that the agent can invoke
autonomously during its reasoning loop.

Each tool wraps a function from :mod:`app.mcp.tools.watch_tools`, binding
pre-resolved AWS credentials so the LLM never sees raw secrets.
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
    log_group_names: List[str] = Field(description="List of CloudWatch log group names to watch.")
    time_range_minutes: int = Field(default=60, description="How many minutes of logs to retrieve (default 60).")
    filter_pattern: Optional[str] = Field(default=None, description="Optional CloudWatch Logs filter pattern.")


class AnalyzePatternsInput(BaseModel):
    """Input for the cloudwatch_analyze_patterns tool."""
    log_group_names: List[str] = Field(description="List of CloudWatch log group names to analyse.")
    time_range_minutes: int = Field(default=60, description="How many minutes to analyse (default 60).")
    pattern_types: Optional[List[str]] = Field(
        default=None,
        description="Types of patterns to look for, e.g. ['error', 'warning']. Defaults to all.",
    )


class DetectAnomaliesInput(BaseModel):
    """Input for the cloudwatch_detect_anomalies tool."""
    log_group_names: List[str] = Field(description="List of CloudWatch log group names to check.")
    time_range_minutes: int = Field(default=60, description="Current window to analyse (default 60).")
    baseline_minutes: int = Field(default=1440, description="Baseline window in minutes (default 1440 = 24h).")
    sensitivity: str = Field(default="medium", description="Sensitivity: 'low', 'medium', or 'high'.")


class CorrelateCrossGroupInput(BaseModel):
    """Input for the cloudwatch_correlate_logs tool."""
    log_group_names: List[str] = Field(description="List of CloudWatch log group names to correlate.")
    time_range_minutes: int = Field(default=60, description="Time window in minutes (default 60).")
    correlation_id: Optional[str] = Field(default=None, description="Correlation/request ID to trace.")
    trace_id: Optional[str] = Field(default=None, description="AWS X-Ray trace ID to correlate.")


class SearchLogsInput(BaseModel):
    """Input for the cloudwatch_search_logs tool."""
    log_group_names: List[str] = Field(description="List of CloudWatch log group names to search.")
    query: str = Field(description="CloudWatch Logs Insights query string.")
    hours: int = Field(default=24, description="Number of hours to look back (default 24).")


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
            provided, they are mentioned in the tool descriptions to guide the
            agent, but the agent is still free to specify different groups.

    Returns:
        List of LangChain ``StructuredTool`` instances.
    """
    from app.mcp.tools.watch_tools import (
        watch_log_groups,
        analyze_log_patterns,
        detect_anomalies,
        correlate_logs,
    )

    # Bind credentials+region so the agent never has to supply them.
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
    ) -> str:
        import json
        result = await watch_log_groups(
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            filter_pattern=filter_pattern,
            region=_region,
            credentials=_creds if _creds else None,
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
    ) -> str:
        import json
        result = await detect_anomalies(
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            baseline_minutes=baseline_minutes,
            sensitivity=sensitivity,
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
            # Fallback: use the watcher's Insights query directly if
            # search_tools has import issues.
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
        # search_logs_multi returns a JSON string already
        return result if isinstance(result, str) else json.dumps(result, indent=2, default=str)

    tools = [
        StructuredTool.from_function(
            coroutine=_watch_logs,
            name="cloudwatch_watch_logs",
            description=(
                "Fetch recent log events from one or more CloudWatch log groups. "
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
                "Returns time-bucketed counts and unique error pattern groupings. "
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
                "historical baseline using z-score analysis. Use this to spot "
                "unusual activity or error rate spikes."
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
    ]

    logger.info(
        "build_cloudwatch_agent_tools: created %d CloudWatch tools (region=%s, log_groups=%s)",
        len(tools),
        _region,
        log_groups,
    )
    return tools
