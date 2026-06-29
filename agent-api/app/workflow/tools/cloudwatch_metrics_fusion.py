"""Compact CloudWatch Metrics fusion for log-group triage.

Infers standard AWS/Lambda (and similar) metrics from log group paths and
attaches a tiny summary so the agent can confirm log spikes against metrics
without a separate tool call (~200-400 tokens total).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

METRICS_FUSION_ENABLED = settings.cloudwatch_metrics_fusion

MAX_METRIC_SPECS = 4
MAX_LOG_GROUPS_SCAN = 5

# log group prefix -> (namespace, [(metric_name, stat_priority)])
_LOG_GROUP_METRIC_MAP = (
    (re.compile(r"^/aws/lambda/(?P<name>[^/]+)"), "AWS/Lambda", (
        ("Errors", "Sum"),
        ("Throttles", "Sum"),
    )),
    (re.compile(r"^/aws/apigateway/(?P<name>[^/]+)"), "AWS/ApiGateway", (
        ("5XXError", "Sum"),
        ("4XXError", "Sum"),
    )),
    (re.compile(r"^/aws/ecs/(?P<name>[^/]+)"), "AWS/ECS", (
        ("CPUUtilization", "Average"),
    )),
    (re.compile(r"^/aws/rds/instance/(?P<name>[^/]+)"), "AWS/RDS", (
        ("DatabaseConnections", "Average"),
    )),
)


def infer_metric_specs(log_groups: List[str]) -> List[Dict[str, Any]]:
    """Map log group names to GetMetricStatistics specs (deduped, capped)."""
    specs: List[Dict[str, Any]] = []
    seen: set = set()

    for lg in (log_groups or [])[:MAX_LOG_GROUPS_SCAN]:
        if not lg:
            continue
        for pattern, namespace, metrics in _LOG_GROUP_METRIC_MAP:
            m = pattern.match(lg)
            if not m:
                continue
            resource = m.group("name")
            dim_name = _dimension_name_for_namespace(namespace)
            for metric_name, _ in metrics:
                key = (namespace, metric_name, resource)
                if key in seen:
                    continue
                seen.add(key)
                specs.append({
                    "log_group": lg,
                    "namespace": namespace,
                    "metric_name": metric_name,
                    "dimensions": [{"Name": dim_name, "Value": resource}],
                    "statistics": ["Sum", "Average", "Maximum"],
                })
                if len(specs) >= MAX_METRIC_SPECS:
                    return specs
            break
    return specs


def _dimension_name_for_namespace(namespace: str) -> str:
    return {
        "AWS/Lambda": "FunctionName",
        "AWS/ApiGateway": "ApiName",
        "AWS/ECS": "ServiceName",
        "AWS/RDS": "DBInstanceIdentifier",
    }.get(namespace, "Resource")


def compact_metric_row(spec: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Shrink a GetMetricStatistics response to a token-efficient row."""
    summary = result.get("summary") or {}
    dims = spec.get("dimensions") or []
    dim_val = dims[0].get("Value") if dims else None
    row: Dict[str, Any] = {
        "log_group": spec.get("log_group"),
        "metric": f"{spec.get('namespace')}/{spec.get('metric_name')}",
        "resource": dim_val,
        "sum": summary.get("total"),
        "max": summary.get("maximum"),
        "avg": round(summary.get("average"), 2) if summary.get("average") is not None else None,
        "samples": summary.get("count"),
    }
    # Drop nulls to save tokens
    return {k: v for k, v in row.items() if v is not None}


async def fetch_metrics_context(
    log_groups: List[str],
    time_range_minutes: int,
    region: str,
    credentials: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fetch compact metric rows for inferred log-group resources."""
    if not METRICS_FUSION_ENABLED:
        return []

    specs = infer_metric_specs(log_groups)
    if not specs:
        return []

    from app.mcp.tools.metrics_tools import get_metric_statistics

    period = 300 if time_range_minutes >= 60 else max(60, time_range_minutes * 60)

    async def _one(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            res = await get_metric_statistics(
                namespace=spec["namespace"],
                metric_name=spec["metric_name"],
                dimensions=spec["dimensions"],
                statistics=spec.get("statistics"),
                period_seconds=period,
                time_range_minutes=time_range_minutes,
                region=region,
                credentials=credentials,
            )
            if not res.get("success"):
                return None
            return compact_metric_row(spec, res)
        except Exception as exc:
            logger.debug("metrics fusion fetch failed for %s: %s", spec.get("metric_name"), exc)
            return None

    rows = await asyncio.gather(*[_one(s) for s in specs])
    return [r for r in rows if r]


async def attach_metrics_context(
    summary: Dict[str, Any],
    log_groups: List[str],
    time_range_minutes: int,
    region: str,
    credentials: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Add ``metrics_context`` to a triage summary dict (non-fatal)."""
    try:
        ctx = await fetch_metrics_context(
            log_groups, time_range_minutes, region, credentials,
        )
        if ctx:
            summary["metrics_context"] = ctx
            summary["metrics_fusion"] = {
                "enabled": True,
                "rows": len(ctx),
                "hint": "Compare log spike counts with metric sum/max for same window.",
            }
    except Exception as exc:
        logger.warning("attach_metrics_context failed (non-fatal): %s", exc)
    return summary
