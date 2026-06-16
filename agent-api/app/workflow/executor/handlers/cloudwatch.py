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

from app.services.log_watch_service import log_watch_service
from app.workflow.executor.sql_loader import parse_time_range_minutes

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

    # Multi-region (Phase 5): an optional comma-separated/list "regions" field
    # fans the investigation across regions. Falls back to the single region.
    _primary_region = _pick("region", "awsRegion", "us-east-1")
    raw_regions = _pick("regions", "awsRegions", None)
    if isinstance(raw_regions, list):
        regions = [r.strip() for r in raw_regions if isinstance(r, str) and r.strip()]
    elif isinstance(raw_regions, str):
        regions = [r.strip() for r in raw_regions.split(",") if r.strip()]
    else:
        regions = []
    if not regions:
        regions = [_primary_region]

    return {
        "log_groups":         log_groups,
        "aws_region":         _primary_region,
        "regions":            regions,
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
        "severity_excludes":      _parse_severity_excludes(
            _pick("severity_excludes", "severityExcludes", None) or data.get("severityExcludes")
        ),
    }


def _parse_severity_excludes(raw: Any) -> List[str]:
    """Normalise severity exclude list from CSV string or JSON list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def _resolve_depth(node: Dict[str, Any]) -> str:
    """Resolve investigation depth from the node's Analysis Depth field.

    shallow → triage + synthesis only
    auto    → triage + conditional drill-down + synthesis (default)
    deep    → triage + always drill-down + correlation + synthesis
    """
    depth = str(
        (node.get("params", {}) or {}).get("analysis_depth")
        or (node.get("data", {}) or {}).get("analysisDepth")
        or "auto"
    ).strip().lower()
    return depth if depth in ("auto", "shallow", "deep") else "auto"


@register("cloudwatch_tool")
async def execute_tool_provider(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Pipeline-first handler for the new ``cloudwatch_tool`` LangflowEditor node.

    Runs the deterministic :meth:`LogWatchService.run_investigation_pipeline`
    upfront — exactly like the legacy ``cloudwatchAnalyzer`` — so the run always
    fetches data and ends in a guaranteed synthesis. The agent then *refines*
    using this pre-computed analysis as seed plus the live LangChain tools bound
    by :func:`app.workflow.tools.cloudwatch_agent_tools.build_cloudwatch_agent_tools`
    via :class:`ReactStrategy`.

    The returned dict carries ``analysis_type`` + ``output`` + ``data`` so the
    agent handler injects it as a "Pre-computed CloudWatch Analysis" context block.
    """
    cfg = _read_cw_config(node)
    log_groups = cfg["log_groups"]

    if not log_groups:
        logger.warning(
            "cloudwatch_tool node has no log groups configured — "
            "agent tools will still be available but unscoped"
        )
        # Clean, non-silent result rather than a stub the agent can't reason about.
        return {
            "status":        "success",
            "tool_provider": "cloudwatch",
            "analysis_type": cfg["analysis_type"],
            "output": (
                "No CloudWatch log groups were configured on the cloudwatch_tool "
                "node, so no pre-computed analysis could be produced. The agent's "
                "CloudWatch tools remain available for ad-hoc discovery/queries."
            ),
            "log_groups":           log_groups,
            "log_groups_analyzed":  log_groups,
            "region":               cfg["aws_region"],
            "time_range":           cfg["time_range"],
        }

    credentials, aws_region = await log_watch_service.resolve_credentials(
        None,
        cfg["aws_region"],
        aws_profile=cfg["aws_profile"],
    )

    depth = _resolve_depth(node)

    logger.info(
        "cloudwatch_tool node running pipeline: region=%s, %d log group(s), depth=%s, range=%s",
        aws_region, len(log_groups), depth, cfg["time_range"],
    )

    # Forward the NORMALISED config to the pipeline as node_data. The pipeline's
    # analyzers read camelCase keys (alarmStateFilter, metricQueries, …); for the
    # new node these live under node.params, so passing node.data alone would drop
    # "Active alarms only", metric queries, custom query and severity excludes.
    pipeline_node_data = {
        **(node.get("data") or {}),
        "alarmStateFilter":    cfg["alarm_state_filter"],
        "alarmNamePrefix":     cfg["alarm_name_prefix"],
        "metricQueries":       cfg["metric_queries"],
        "customInsightsQuery": cfg["custom_insights_query"],
        "severityExcludes":    cfg["severity_excludes"],
    }

    # "Analysis type" is a FOCUS lens — the pipeline still runs the full
    # comprehensive triage, but leads the report with the selected dimension.
    # Map the UI label "anomalies" to the internal analysis type.
    _focus_map = {"anomalies": "anomaly-detection"}
    focus = _focus_map.get(cfg["analysis_type"], cfg["analysis_type"])

    # Chat fast path: if the user's message contains a correlation / trace id,
    # the pipeline skips the broad triage and traces just that request.
    from app.core.trace_ids import extract_trace_ids
    _user_query = (
        (context.get("inputs") or {}).get("user_query")
        or context.get("user_query")
        or ""
    )
    _ids = extract_trace_ids(_user_query)
    if _ids["found"]:
        logger.info(
            "cloudwatch_tool: detected id in query (correlation_id=%s trace_id=%s) — "
            "fast-path correlation lookup",
            _ids["correlation_id"], _ids["trace_id"],
        )

    # Multi-region fan-out (Phase 5): when the node lists >1 region, run per-region
    # triage and synthesize once over the merged, region-namespaced evidence.
    regions = cfg.get("regions") or [aws_region]
    if len([r for r in regions if r]) > 1:
        logger.info("cloudwatch_tool: multi-region investigation across %s", regions)
        result = await log_watch_service.run_multiregion_investigation(
            regions=regions,
            log_groups=log_groups,
            time_range=cfg["time_range"],
            time_range_minutes=parse_time_range_minutes(cfg["time_range"]),
            error_threshold=cfg["error_threshold"],
            enable_alerts=cfg["enable_alerts"],
            credentials=credentials,
            node_data=pipeline_node_data,
            active_executions=executor.active_executions,
            execution_id=context.get("execution_id"),
            focus=focus,
        )
        result["tool_provider"] = "cloudwatch"
        result.setdefault("region", aws_region)
        return result

    # Staged deterministic pipeline — always ends in a guaranteed LLM synthesis,
    # so the result can never be a mid-investigation fragment. The open-ended
    # agent refines this seed via the bound CloudWatch tools. When an id is
    # detected, run_investigation_pipeline short-circuits to a targeted trace.
    result = await log_watch_service.run_investigation_pipeline(
        log_groups=log_groups,
        time_range=cfg["time_range"],
        time_range_minutes=parse_time_range_minutes(cfg["time_range"]),
        error_threshold=cfg["error_threshold"],
        enable_alerts=cfg["enable_alerts"],
        region=aws_region,
        credentials=credentials,
        node_data=pipeline_node_data,
        active_executions=executor.active_executions,
        execution_id=context.get("execution_id"),
        depth=depth,
        focus=focus,
        correlation_id=_ids["correlation_id"],
        trace_id=_ids["trace_id"],
    )

    # Tag as a CloudWatch tool result while preserving analysis_type/output/data
    # so the agent-handler seed logic injects it (the exclusion guard was removed).
    result["tool_provider"] = "cloudwatch"
    result.setdefault("region", aws_region)
    return result


@register("cloudwatchAnalyzer")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute CloudWatch Analyzer node via ``LogWatchService``."""
    node_data = node.get('data', {})
    log_groups = node_data.get('logGroups', [])
    time_range = node_data.get('timeRange', '1h')
    error_threshold = node_data.get('errorThreshold', 10)
    aws_region = node_data.get('awsRegion', 'us-east-1')
    aws_profile = node_data.get('awsProfile')
    enable_alerts = node_data.get('enableAlerts', False)

    if not log_groups:
        return {
            "status": "failed",
            "error": "No log groups configured for CloudWatch Analyzer",
        }

    log_groups = [lg for lg in log_groups if lg]
    if not log_groups:
        return {
            "status": "failed",
            "error": "All configured log groups are empty",
        }

    credentials, aws_region = await log_watch_service.resolve_credentials(
        None,
        aws_region,
        aws_profile=aws_profile,
    )

    # Resolve investigation depth from the node's Analysis Depth field:
    #   shallow → triage + synthesis only
    #   auto    → triage + conditional drill-down + synthesis (default)
    #   deep    → triage + always drill-down + correlation + synthesis
    depth = str(
        node.get("params", {}).get("analysis_depth")
        or node_data.get("analysisDepth")
        or "auto"
    ).strip().lower()
    if depth not in ("auto", "shallow", "deep"):
        depth = "auto"

    # Staged deterministic pipeline — always ends in a guaranteed LLM synthesis,
    # so the result can never be a mid-investigation fragment. No ReAct loop here;
    # the open-ended agent is reserved for ad-hoc agent-chat investigations.
    return await log_watch_service.run_investigation_pipeline(
        log_groups=log_groups,
        time_range=time_range,
        time_range_minutes=parse_time_range_minutes(time_range),
        error_threshold=error_threshold,
        enable_alerts=enable_alerts,
        region=aws_region,
        credentials=credentials,
        node_data=node_data,
        active_executions=executor.active_executions,
        execution_id=context.get('execution_id'),
        depth=depth,
    )
