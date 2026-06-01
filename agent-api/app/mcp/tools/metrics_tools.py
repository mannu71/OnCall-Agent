"""CloudWatch Metrics and Alarms tools.

Provides async functions for querying CloudWatch Metrics (GetMetricData,
GetMetricStatistics) and listing CloudWatch Metric Alarms.  All AWS calls
run in an executor to avoid blocking the asyncio event loop, consistent with
the pattern established in watch_tools.py.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

import boto3
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import ClientError

from app.config import settings
from app.core.thread_pools import run_in_aws_pool

# Honour the same SSL-bypass env var used by watch_tools.
if not settings.aws_ssl_verify:
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception handler (mirrors watch_tools.handle_exceptions)
# ---------------------------------------------------------------------------

def handle_exceptions(func: Callable) -> Callable:
    """Decorator that converts AWS and generic exceptions to error dicts."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            logger.error("AWS ClientError [%s]: %s", error_code, error_msg)
            return {"error": True, "error_code": error_code, "message": error_msg}
        except Exception as e:
            logger.error("Error in %s: %s", func.__name__, e)
            return {"error": True, "message": str(e)}
    return wrapper


# ---------------------------------------------------------------------------
# Low-level client factory
# ---------------------------------------------------------------------------

def _get_metrics_client(
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
):
    """Create a boto3 ``cloudwatch`` client (not ``logs``)."""
    creds = credentials or {}
    session_kwargs: Dict[str, Any] = {}

    if creds.get("aws_profile"):
        session_kwargs["profile_name"] = creds["aws_profile"]
    elif creds.get("access_key_id") and creds.get("secret_access_key"):
        session_kwargs["aws_access_key_id"] = creds["access_key_id"]
        session_kwargs["aws_secret_access_key"] = creds["secret_access_key"]
        if creds.get("session_token"):
            session_kwargs["aws_session_token"] = creds["session_token"]
    elif creds.get("use_env_credentials"):
        pass  # boto3 picks up env vars automatically

    session = boto3.Session(region_name=region, **session_kwargs)
    ssl_verify = settings.aws_ssl_verify
    client_kwargs: Dict[str, Any] = {"region_name": region}
    if not ssl_verify:
        client_kwargs["verify"] = False
        client_kwargs["config"] = BotocoreConfig(retries={"max_attempts": 3})

    return session.client("cloudwatch", **client_kwargs)


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

@handle_exceptions
async def get_metric_data(
    metric_queries: List[Dict[str, Any]],
    time_range_minutes: int = 60,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
    cache_ttl_seconds: int = 60,
    include_series: bool = True,
) -> Dict[str, Any]:
    """Query CloudWatch Metrics using the GetMetricData API.

    Supports up to 500 metrics per call.  Handles pagination via
    ``NextToken`` automatically.

    Args:
        metric_queries: List of MetricDataQuery dicts (boto3 shape).  Each
            entry must have:
              - ``Id`` (str, e.g. ``"m1"``)
              - ``MetricStat.Metric.Namespace`` (e.g. ``"AWS/Lambda"``)
              - ``MetricStat.Metric.MetricName`` (e.g. ``"Errors"``)
              - ``MetricStat.Period`` (int seconds, e.g. ``300``)
              - ``MetricStat.Stat`` (e.g. ``"Sum"``)
            Optionally include ``Label`` for a human-readable name.
        time_range_minutes: How far back to query (default 60 minutes).
        region: AWS region (default: us-east-1).
        credentials: Pre-resolved credentials dict (optional).
        cache_ttl_seconds: Result cache TTL in seconds (default 60, 0 =
            bypass cache).

    Returns:
        Dict with ``metrics`` keyed by query Id, each containing
        ``label``, ``timestamps``, ``values``, and ``status_code``.

    Example::

        result = await get_metric_data(
            metric_queries=[{
                "Id": "errors",
                "Label": "Lambda Errors",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/Lambda",
                        "MetricName": "Errors",
                        "Dimensions": [{"Name": "FunctionName", "Value": "kyc-auth"}],
                    },
                    "Period": 300,
                    "Stat": "Sum",
                },
            }],
            time_range_minutes=60,
        )
    """
    client = _get_metrics_client(region=region, credentials=credentials)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)

    all_results: Dict[str, Dict[str, Any]] = {}
    next_token: Optional[str] = None

    while True:
        kwargs: Dict[str, Any] = {
            "MetricDataQueries": metric_queries,
            "StartTime": start_time,
            "EndTime": end_time,
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = await run_in_aws_pool(
            lambda: client.get_metric_data(**kwargs),
        )

        for result in response.get("MetricDataResults", []):
            qid = result["Id"]
            if qid not in all_results:
                all_results[qid] = {
                    "label": result.get("Label", qid),
                    "timestamps": [],
                    "values": [],
                    "status_code": result.get("StatusCode", "Unknown"),
                }
            all_results[qid]["timestamps"].extend(
                [ts.isoformat() for ts in result.get("Timestamps", [])]
            )
            all_results[qid]["values"].extend(result.get("Values", []))

        next_token = response.get("NextToken")
        if not next_token:
            break

    # Build a brief summary for each metric.
    for qid, data in all_results.items():
        values = data["values"]
        if values:
            data["summary"] = {
                "count": len(values),
                "total": round(sum(values), 4),
                "average": round(sum(values) / len(values), 4),
                "maximum": round(max(values), 4),
                "minimum": round(min(values), 4),
                "latest": round(values[-1], 4),
            }
        else:
            data["summary"] = {"count": 0, "total": 0}

        # Token efficiency: the summary already captures the signal. Unless the
        # caller explicitly wants the raw series, drop the per-datapoint arrays
        # (which dominate the payload and otherwise get truncated mid-array by
        # the agent token budget).
        if not include_series:
            data["points"] = len(values)
            data.pop("timestamps", None)
            data.pop("values", None)

    return {
        "success": True,
        "metrics": all_results,
        "series_included": include_series,
        "time_range": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "minutes": time_range_minutes,
        },
        "region": region,
    }


@handle_exceptions
async def get_metric_statistics(
    namespace: str,
    metric_name: str,
    dimensions: List[Dict[str, str]],
    statistics: Optional[List[str]] = None,
    period_seconds: int = 300,
    time_range_minutes: int = 60,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Query a single CloudWatch metric via GetMetricStatistics.

    A simpler alternative to :func:`get_metric_data` for when you only need
    one metric at a time.

    Args:
        namespace: CloudWatch namespace, e.g. ``"AWS/Lambda"``.
        metric_name: Metric name, e.g. ``"Duration"``.
        dimensions: List of ``{Name: ..., Value: ...}`` dicts.
        statistics: Statistics to request. Defaults to
            ``["Sum", "Average", "Maximum", "Minimum", "SampleCount"]``.
        period_seconds: Aggregation period in seconds (default 300 = 5 min).
        time_range_minutes: How far back to query (default 60).
        region: AWS region (default: us-east-1).
        credentials: Pre-resolved credentials dict (optional).

    Returns:
        Dict with ``datapoints`` sorted by ``Timestamp`` and ``summary``
        statistics.
    """
    if statistics is None:
        statistics = ["Sum", "Average", "Maximum", "Minimum", "SampleCount"]

    client = _get_metrics_client(region=region, credentials=credentials)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)

    response = await run_in_aws_pool(
        lambda: client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=[{"Name": d["Name"], "Value": d["Value"]} for d in dimensions],
            StartTime=start_time,
            EndTime=end_time,
            Period=period_seconds,
            Statistics=statistics,
        ),
    )

    datapoints = sorted(
        response.get("Datapoints", []),
        key=lambda dp: dp.get("Timestamp", datetime.min),
    )

    # Convert Timestamp objects to ISO strings for JSON serialisation.
    cleaned: List[Dict[str, Any]] = []
    for dp in datapoints:
        entry = {k: v for k, v in dp.items() if k != "Timestamp"}
        entry["timestamp"] = dp["Timestamp"].isoformat() if "Timestamp" in dp else None
        cleaned.append(entry)

    # Compute an overall summary.
    all_values = [dp.get("Average") or dp.get("Sum") or 0 for dp in datapoints]
    summary: Dict[str, Any] = {"count": len(cleaned)}
    if all_values:
        summary["total"] = round(sum(all_values), 4)
        summary["average"] = round(sum(all_values) / len(all_values), 4)
        summary["maximum"] = round(max(all_values), 4)
        summary["minimum"] = round(min(all_values), 4)

    return {
        "success": True,
        "namespace": namespace,
        "metric_name": metric_name,
        "dimensions": dimensions,
        "period_seconds": period_seconds,
        "datapoints": cleaned,
        "summary": summary,
        "time_range": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "minutes": time_range_minutes,
        },
        "region": region,
    }


@handle_exceptions
async def list_metric_alarms(
    alarm_names: Optional[List[str]] = None,
    alarm_name_prefix: Optional[str] = None,
    state_value: Optional[str] = None,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
    max_records: int = 100,
) -> Dict[str, Any]:
    """List CloudWatch Metric Alarms, optionally filtered by state.

    This is typically the first call an on-call engineer makes — seeing which
    alarms are currently firing narrows the investigation immediately.

    Args:
        alarm_names: Specific alarm names to retrieve (optional).
        alarm_name_prefix: Prefix filter for alarm names (optional).
        state_value: Filter by alarm state: ``"OK"``, ``"ALARM"``, or
            ``"INSUFFICIENT_DATA"``.  Use ``"ALARM"`` to get only firing alarms.
        region: AWS region (default: us-east-1).
        credentials: Pre-resolved credentials dict (optional).
        max_records: Maximum number of alarms to return (default 100).

    Returns:
        Dict with ``alarms`` list and ``summary`` counts by state.

    Example::

        # Get only currently firing alarms
        result = await list_metric_alarms(state_value="ALARM")
    """
    client = _get_metrics_client(region=region, credentials=credentials)

    alarms: List[Dict[str, Any]] = []
    next_token: Optional[str] = None

    while len(alarms) < max_records:
        kwargs: Dict[str, Any] = {"MaxRecords": min(100, max_records - len(alarms))}
        if alarm_names:
            kwargs["AlarmNames"] = alarm_names
        if alarm_name_prefix:
            kwargs["AlarmNamePrefix"] = alarm_name_prefix
        if state_value:
            kwargs["StateValue"] = state_value
        if next_token:
            kwargs["NextToken"] = next_token

        response = await run_in_aws_pool(
            lambda: client.describe_alarms(**kwargs),
        )

        for alarm in response.get("MetricAlarms", []):
            alarms.append({
                "name": alarm.get("AlarmName"),
                "description": alarm.get("AlarmDescription"),
                "state": alarm.get("StateValue"),
                "reason": alarm.get("StateReason"),
                "metric_name": alarm.get("MetricName"),
                "namespace": alarm.get("Namespace"),
                "dimensions": alarm.get("Dimensions", []),
                "threshold": alarm.get("Threshold"),
                "comparison": alarm.get("ComparisonOperator"),
                "period_seconds": alarm.get("Period"),
                "statistic": alarm.get("Statistic"),
                "evaluation_periods": alarm.get("EvaluationPeriods"),
                "state_updated_at": (
                    alarm["StateUpdatedTimestamp"].isoformat()
                    if alarm.get("StateUpdatedTimestamp")
                    else None
                ),
                "actions_enabled": alarm.get("ActionsEnabled", True),
                "alarm_arn": alarm.get("AlarmArn"),
            })

        next_token = response.get("NextToken")
        if not next_token:
            break

    # Build summary counts.
    state_counts: Dict[str, int] = {"ALARM": 0, "OK": 0, "INSUFFICIENT_DATA": 0}
    for alarm in alarms:
        state = alarm.get("state", "")
        if state in state_counts:
            state_counts[state] += 1

    return {
        "success": True,
        "alarms": alarms,
        "summary": {
            "total": len(alarms),
            "in_alarm": state_counts["ALARM"],
            "ok": state_counts["OK"],
            "insufficient_data": state_counts["INSUFFICIENT_DATA"],
        },
        "filter": {
            "state_value": state_value,
            "alarm_name_prefix": alarm_name_prefix,
        },
        "region": region,
    }
