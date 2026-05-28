"""CloudWatch Analyzer node handler — runs log/metric analysis + LLM summary.

Two node types are registered here:

* ``cloudwatchAnalyzer`` (legacy) — runs a single pre-computed analysis upfront
  and dumps results to the LLM. Kept for backward-compat with workflows saved
  before the LangflowEditor schema change.

* ``cloudwatch_tool`` (new) — tool-provider stub. Validates config, resolves
  AWS credentials, and returns immediately. The ReAct strategy detects the
  connected node and binds composable LangChain tools so the agent can call
  CloudWatch iteratively — far more token-efficient than the legacy path.
"""
import logging
from typing import Any, Dict, List

from app.mcp.tools.watch_tools import (
    watch_log_groups,
    analyze_log_patterns,
    detect_anomalies,
    correlate_logs,
)

from . import register

logger = logging.getLogger(__name__)

# Hard upper bound for any CloudWatch time-range. Enforced at the tool boundary
# and again here so node-config validation rejects nonsense values early.
MAX_TIME_RANGE_MINUTES = 1440  # 24 hours


def _read_cw_config(node: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise CloudWatch node config from either new (params) or legacy (data).

    New LangflowEditor schema stores values under ``node.params`` with snake_case
    keys: ``region``, ``profile``, ``groups`` (CSV string), ``analysis``,
    ``range``, ``threshold``, ``alerts``.

    Legacy ReactFlow schema stores values under ``node.data`` with camelCase
    keys: ``awsRegion``, ``awsProfile``, ``logGroups`` (list), ``analysisType``,
    ``timeRange``, ``errorThreshold``, ``enableAlerts``.

    Returns a single canonical dict with: ``log_groups`` (list[str]),
    ``aws_region``, ``aws_profile``, ``analysis_type``, ``time_range``,
    ``error_threshold``, ``enable_alerts``.
    """
    params = node.get("params") or {}
    data = node.get("data") or {}

    def _pick(p_key: str, d_key: str, default: Any = None) -> Any:
        if p_key in params and params[p_key] not in (None, ""):
            return params[p_key]
        if d_key in data and data[d_key] not in (None, ""):
            return data[d_key]
        return default

    raw_groups = _pick("groups", "logGroups", "")
    if isinstance(raw_groups, list):
        log_groups: List[str] = [g.strip() for g in raw_groups if isinstance(g, str) and g.strip()]
    elif isinstance(raw_groups, str):
        log_groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
    else:
        log_groups = []

    raw_alerts = _pick("alerts", "enableAlerts", False)
    if isinstance(raw_alerts, str):
        enable_alerts = raw_alerts.lower() in ("true", "1", "yes", "on")
    else:
        enable_alerts = bool(raw_alerts)

    try:
        error_threshold = int(_pick("threshold", "errorThreshold", 10))
    except (TypeError, ValueError):
        error_threshold = 10

    # "Active alarms only" checkbox — takes precedence over the dropdown filter.
    # Supported from both LangflowEditor (params.activeAlarmsOnly) and
    # WorkflowEditor (data.activeAlarmsOnly).
    raw_active_only = _pick("activeAlarmsOnly", "activeAlarmsOnly", False)
    if isinstance(raw_active_only, str):
        active_alarms_only = raw_active_only.lower() in ("true", "1", "yes", "on")
    else:
        active_alarms_only = bool(raw_active_only)

    # Resolve the effective alarm state filter:
    # activeAlarmsOnly=True → always ALARM, regardless of the dropdown value.
    raw_alarm_filter = _pick("alarmStateFilter", "alarmStateFilter", None)
    if active_alarms_only:
        alarm_state_filter = "ALARM"
    else:
        alarm_state_filter = raw_alarm_filter or None

    return {
        "log_groups":         log_groups,
        "aws_region":         _pick("region", "awsRegion", "us-east-1"),
        "aws_profile":        _pick("profile", "awsProfile", None),
        "analysis_type":      _pick("analysis", "analysisType", "error-patterns"),
        "time_range":         _pick("range", "timeRange", "1h"),
        "error_threshold":    error_threshold,
        "enable_alerts":      enable_alerts,
        "active_alarms_only": active_alarms_only,
        # Pass-through for legacy fields used only by cloudwatchAnalyzer:
        "metric_queries":         data.get("metricQueries") or [],
        "alarm_name_prefix":      data.get("alarmNamePrefix"),
        "alarm_state_filter":     alarm_state_filter,
        "custom_insights_query":  data.get("customInsightsQuery", ""),
    }


@register("cloudwatch_tool")
async def execute_tool_provider(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Tool-provider stub for the new ``cloudwatch_tool`` LangflowEditor node.

    Validates config and resolves AWS credentials, then returns immediately.
    The actual CloudWatch work happens when the agent calls the LangChain tools
    bound by :func:`app.workflow.tools.cloudwatch_agent_tools.build_cloudwatch_agent_tools`
    via :class:`ReactStrategy`. No pre-computed analysis, no LLM call here.
    """
    from app.core.aws_credentials import resolve_aws_credentials

    cfg = _read_cw_config(node)
    log_groups = cfg["log_groups"]

    if not log_groups:
        logger.warning(
            "cloudwatch_tool node has no log groups configured — "
            "agent tools will still be available but unscoped"
        )

    # Pre-resolve credentials so ReactStrategy doesn't pay this cost on every
    # agent step. Failures here are non-fatal: the agent tools re-resolve and
    # surface a clearer error to the agent itself.
    try:
        await resolve_aws_credentials(
            aws_profile=cfg["aws_profile"],
            aws_region=cfg["aws_region"],
        )
        creds_ok = True
    except Exception as e:
        logger.warning(
            "cloudwatch_tool: credential resolution failed (%s); agent tools will retry", e
        )
        creds_ok = False

    logger.info(
        "cloudwatch_tool node validated: region=%s, %d log group(s), analysis=%s, range=%s, creds=%s",
        cfg["aws_region"], len(log_groups), cfg["analysis_type"], cfg["time_range"],
        "ok" if creds_ok else "deferred",
    )

    return {
        "status":        "success",
        "tool_provider": "cloudwatch",
        "output":        f"CloudWatch tools available for {len(log_groups)} log group(s) in {cfg['aws_region']}",
        "log_groups":    log_groups,
        "region":        cfg["aws_region"],
        "analysis_type": cfg["analysis_type"],
        "time_range":    cfg["time_range"],
    }


@register("cloudwatchAnalyzer")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute CloudWatch Analyzer node by running log analysis."""
    from app.core.aws_credentials import resolve_aws_credentials
    from app.core.retry import with_retry

    node_data = node.get('data', {})
    log_groups = node_data.get('logGroups', [])
    analysis_type = node_data.get('analysisType', 'error-patterns')
    time_range = node_data.get('timeRange', '1h')
    error_threshold = node_data.get('errorThreshold', 10)
    aws_region = node_data.get('awsRegion', 'us-east-1')
    aws_profile = node_data.get('awsProfile')
    enable_alerts = node_data.get('enableAlerts', False)

    if not log_groups:
        return {
            "status": "failed",
            "error": "No log groups configured for CloudWatch Analyzer"
        }

    log_groups = [lg for lg in log_groups if lg]
    if not log_groups:
        return {
            "status": "failed",
            "error": "All configured log groups are empty"
        }

    time_range_minutes = executor._parse_time_range_minutes(time_range)

    # Use shared credential resolver instead of inline duplication.
    credentials, aws_region = await resolve_aws_credentials(
        aws_profile=aws_profile,
        aws_region=aws_region,
    )

    async def _run_analysis() -> Dict[str, Any]:
        """Inner coroutine wrapped by with_retry for transient AWS errors."""
        if analysis_type == 'error-patterns':
            return await analyze_log_patterns(
                log_group_names=log_groups,
                time_range_minutes=time_range_minutes,
                pattern_types=["error", "warning"],
                region=aws_region,
                credentials=credentials if credentials else None,
            )
        elif analysis_type == 'activity-summary':
            return await watch_log_groups(
                log_group_names=log_groups,
                time_range_minutes=time_range_minutes,
                region=aws_region,
                credentials=credentials if credentials else None,
            )
        elif analysis_type == 'anomaly-detection':
            return await detect_anomalies(
                log_group_names=log_groups,
                time_range_minutes=time_range_minutes,
                sensitivity="medium",
                region=aws_region,
                credentials=credentials if credentials else None,
            )
        elif analysis_type == 'correlation':
            return await correlate_logs(
                log_group_names=log_groups,
                time_range_minutes=time_range_minutes,
                region=aws_region,
                credentials=credentials if credentials else None,
            )
        elif analysis_type == 'metrics':
            from app.mcp.tools.metrics_tools import get_metric_data
            metric_queries = node_data.get('metricQueries', [])
            if not metric_queries:
                return {
                    "status": "failed",
                    "error": "No metricQueries configured for 'metrics' analysis type"
                }
            return await get_metric_data(
                metric_queries=metric_queries,
                time_range_minutes=time_range_minutes,
                region=aws_region,
                credentials=credentials if credentials else None,
            )
        elif analysis_type == 'alarms':
            from app.mcp.tools.metrics_tools import list_metric_alarms
            return await list_metric_alarms(
                alarm_name_prefix=node_data.get('alarmNamePrefix'),
                state_value=node_data.get('alarmStateFilter'),
                region=aws_region,
                credentials=credentials if credentials else None,
            )
        elif analysis_type == 'custom-query':
            from app.mcp.tools.watch_tools import get_watcher, _extract_credentials
            from datetime import datetime, timedelta, timezone as _tz
            custom_query = node_data.get('customInsightsQuery', '').strip()
            if not custom_query:
                return {
                    "status": "failed",
                    "error": "No customInsightsQuery set for 'custom-query' analysis type"
                }
            watcher = get_watcher(
                region=aws_region,
                **_extract_credentials(credentials if credentials else {}),
            )
            end_time = datetime.now(_tz.utc)
            start_time = end_time - timedelta(minutes=time_range_minutes)
            return await watcher.query_with_insights(
                log_group_names=log_groups,
                query_string=custom_query,
                start_time=start_time,
                end_time=end_time,
            )
        else:
            return {
                "status": "failed",
                "error": f"Unknown analysis type: {analysis_type}"
            }

    try:
        result = await with_retry(_run_analysis, max_retries=2)

        if isinstance(result, dict) and result.get('error'):
            return {
                "status": "failed",
                "error": result.get('message', str(result.get('error')))
            }

        output_summary = executor._build_cloudwatch_summary(result, analysis_type, log_groups)

        alerts = []
        if enable_alerts and isinstance(result, dict):
            alerts = executor._check_cloudwatch_alerts(result, analysis_type, error_threshold)

        # Use the workflow's LLM to produce an agent-style analysis.
        # _analyze_cloudwatch_with_llm returns a 3-tuple
        # (text, model_name, structured_dict); the structured_dict may be
        # None for providers that don't support structured output.
        llm_result = await executor._analyze_cloudwatch_with_llm(
            raw_result=result,
            analysis_type=analysis_type,
            log_groups=log_groups,
            time_range=time_range,
            alerts=alerts,
            execution_id=context.get('execution_id'),
        )
        if llm_result and len(llm_result) >= 5:
            llm_analysis, model_used, structured_analysis, llm_input_tokens, llm_output_tokens = llm_result[:5]
        elif llm_result and len(llm_result) == 3:
            llm_analysis, model_used, structured_analysis = llm_result
            llm_input_tokens = llm_output_tokens = 0
        else:
            llm_analysis = model_used = structured_analysis = None
            llm_input_tokens = llm_output_tokens = 0

        return {
            "status": "success",
            "output": llm_analysis or output_summary,
            "analysis_type": analysis_type,
            "log_groups_analyzed": log_groups,
            "time_range": time_range,
            "data": result,
            "alerts": alerts if alerts else None,
            "model": model_used,
            "structured_analysis": structured_analysis,
            "input_tokens":  llm_input_tokens,
            "output_tokens": llm_output_tokens,
            "total_tokens":  llm_input_tokens + llm_output_tokens,
        }

    except Exception as e:
        logger.error(f"CloudWatch Analyzer execution failed: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }
