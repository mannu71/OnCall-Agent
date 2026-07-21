"""Log Watch service — CloudWatch operations behind the HTTP API."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import settings
from app.core.aws.aws_credentials import resolve_aws_credentials
from app.core.resilience.retry import with_retry
from app.core.streaming.sse import HEARTBEAT_INTERVAL_SECONDS, STREAM_TIMEOUT_SECONDS
from app.mcp.tools.alert_tools import (
    acknowledge_alert,
    create_alert,
    dismiss_alert,
    get_alert_summary,
    get_alerts,
    get_knowledge_entries,
    create_knowledge_entry,
    resolve_alert,
)
from app.mcp.tools.metrics_tools import (
    get_metric_data,
    get_metric_statistics,
    list_metric_alarms,
)
from app.mcp.tools.watch_tools import (
    analyze_log_patterns,
    correlate_logs,
    detect_anomalies,
    discover_log_groups,
    watch_log_groups,
)
from app.services.knowledge_base import knowledge_base

logger = logging.getLogger(__name__)

_SSE_DEDUP_MAX = settings.log_watch_sse_dedup_max


def _classify_aws_error(exc: BaseException) -> str:
    """Coarse category for a failed AWS call so coverage signalling is actionable."""
    if isinstance(exc, ClientError):
        code = (exc.response.get("Error", {}) or {}).get("Code", "") or ""
        low = code.lower()
        if "throttl" in low:
            return "throttling"
        if "accessdenied" in low or "unauthorized" in low or "notauthorized" in low:
            return "access-denied"
        if "validation" in low or "malformed" in low:
            return "validation"
        if "expired" in low or "token" in low:
            return "expired-credentials"
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "throttl" in msg:
        return "throttling"
    if "access" in msg and "denied" in msg:
        return "access-denied"
    if "validation" in msg:
        return "validation"
    return "error"


class LogWatchService:
    """Orchestrates CloudWatch log/metric tools and knowledge-base helpers."""

    async def resolve_credentials(
        self,
        request_credentials: Optional[Dict[str, Any]],
        region: str,
        *,
        aws_profile: Optional[str] = None,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        if request_credentials:
            return request_credentials, region
        return await resolve_aws_credentials(aws_profile=aws_profile, aws_region=region)

    async def test_connection(
        self, *, region: str, credentials: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        try:
            session_kwargs: Dict[str, Any] = {}
            if credentials:
                if credentials.get("aws_profile"):
                    session_kwargs["profile_name"] = credentials["aws_profile"]
                elif credentials.get("aws_access_key_id") and credentials.get(
                    "aws_secret_access_key"
                ):
                    session_kwargs["aws_access_key_id"] = credentials["aws_access_key_id"]
                    session_kwargs["aws_secret_access_key"] = credentials[
                        "aws_secret_access_key"
                    ]

            session = boto3.Session(**session_kwargs)
            config = Config()
            if not settings.aws_ssl_verify:
                config = Config(
                    connect_timeout=10,
                    read_timeout=10,
                    retries={"max_attempts": 2},
                )

            client = session.client(
                "logs",
                region_name=region,
                config=config,
                verify=(
                    settings.aws_ca_bundle
                    if settings.aws_ca_bundle
                    else False
                    if not settings.aws_ssl_verify
                    else None
                ),
            )

            async def _call_describe_log_groups():
                return client.describe_log_groups(limit=1)

            await with_retry(_call_describe_log_groups, max_retries=3)

            return {
                "success": True,
                "message": "Successfully connected to CloudWatch Logs",
                "region": region,
            }
        except NoCredentialsError:
            return {
                "success": False,
                "message": "No AWS credentials found. Please provide valid credentials.",
                "region": region,
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            if error_code == "InvalidClientTokenId":
                message = "Invalid AWS Access Key ID or Secret Access Key"
            elif error_code == "UnrecognizedClientException":
                message = "AWS credentials are not valid or have expired"
            elif error_code == "AccessDenied":
                message = "Access denied. Check IAM permissions for CloudWatch Logs"
            else:
                message = f"AWS Error: {error_msg}"
            return {"success": False, "message": message, "region": region}
        except BotoCoreError as e:
            return {
                "success": False,
                "message": f"Connection error: {str(e)}",
                "region": region,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}",
                "region": region,
            }

    async def watch_logs(
        self,
        *,
        log_group_names: List[str],
        time_range_minutes: int,
        filter_pattern: Optional[str],
        max_events_per_group: int,
        regions: Optional[List[str]],
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            watch_log_groups,
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            filter_pattern=filter_pattern,
            max_events_per_group=max_events_per_group,
            regions=regions,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def analyze_patterns(
        self,
        *,
        log_group_names: List[str],
        time_range_minutes: int,
        pattern_types: Optional[List[str]],
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            analyze_log_patterns,
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            pattern_types=pattern_types,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def detect_anomalies(
        self,
        *,
        log_group_names: List[str],
        time_range_minutes: int,
        baseline_minutes: int,
        sensitivity: str,
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            detect_anomalies,
            log_group_names=log_group_names,
            time_range_minutes=time_range_minutes,
            baseline_minutes=baseline_minutes,
            sensitivity=sensitivity,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def correlate_logs(
        self,
        *,
        log_group_names: List[str],
        correlation_id: Optional[str],
        trace_id: Optional[str],
        time_range_minutes: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            correlate_logs,
            log_group_names=log_group_names,
            correlation_id=correlation_id,
            time_range_minutes=time_range_minutes,
            trace_id=trace_id,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def discover_log_groups(
        self,
        *,
        prefix: Optional[str],
        tag_key: Optional[str],
        tag_value: Optional[str],
        limit: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            discover_log_groups,
            prefix=prefix,
            tag_key=tag_key,
            tag_value=tag_value,
            limit=limit,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def query_metrics(
        self,
        *,
        metric_queries: List[Dict[str, Any]],
        time_range_minutes: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            get_metric_data,
            metric_queries=metric_queries,
            time_range_minutes=time_range_minutes,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def query_metric_statistics(
        self,
        *,
        namespace: str,
        metric_name: str,
        dimensions: List[Dict[str, str]],
        statistics: Optional[List[str]],
        period_seconds: int,
        time_range_minutes: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return await with_retry(
            get_metric_statistics,
            namespace=namespace,
            metric_name=metric_name,
            dimensions=dimensions,
            statistics=statistics,
            period_seconds=period_seconds,
            time_range_minutes=time_range_minutes,
            region=region,
            credentials=credentials,
            max_retries=2,
        )

    async def list_alarms(
        self,
        *,
        alarm_name_prefix: Optional[str],
        state_value: Optional[str],
        max_records: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
        include_history: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if include_history is None:
            include_history = settings.cloudwatch_alarm_history
        return await with_retry(
            list_metric_alarms,
            alarm_name_prefix=alarm_name_prefix,
            state_value=state_value,
            region=region,
            credentials=credentials,
            max_records=max_records,
            include_history=include_history,
            max_retries=2,
        )

    async def stream_log_events(
        self,
        *,
        log_group_name: str,
        filter_pattern: Optional[str],
        poll_interval_seconds: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
    ) -> AsyncGenerator[str, None]:
        from app.mcp.tools.watch_tools import get_watcher, _extract_credentials

        watcher = get_watcher(
            region=region,
            **_extract_credentials(credentials if credentials else {}),
        )
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=1)
        seen_event_ids: OrderedDict[Any, None] = OrderedDict()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STREAM_TIMEOUT_SECONDS
        last_heartbeat = loop.time()

        yield (
            f"event: connected\n"
            f"data: {json.dumps({'log_group_name': log_group_name})}\n\n"
        )

        while loop.time() < deadline:
            now = loop.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            try:
                end_time = datetime.now(timezone.utc)
                logs = await watcher.fetch_logs(
                    log_group_name=log_group_name,
                    start_time=start_time,
                    end_time=end_time,
                    filter_pattern=filter_pattern,
                    limit=500,
                )
                for event in logs:
                    eid = event.get("eventId") or event.get("ingestionTime")
                    if eid and eid in seen_event_ids:
                        continue
                    if eid:
                        seen_event_ids[eid] = None
                        if len(seen_event_ids) > _SSE_DEDUP_MAX:
                            seen_event_ids.popitem(last=False)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                start_time = end_time
            except Exception as stream_err:
                yield f"event: error\ndata: {json.dumps({'error': str(stream_err)})}\n\n"
            await asyncio.sleep(poll_interval_seconds)

        yield (
            f"event: stream_end\n"
            f"data: {json.dumps({'log_group_name': log_group_name})}\n\n"
        )

    # Alert management -------------------------------------------------------

    async def create_alert(self, **kwargs: Any) -> Dict[str, Any]:
        return await create_alert(**kwargs)

    async def list_alerts(self, **kwargs: Any) -> Dict[str, Any]:
        return await get_alerts(**kwargs)

    async def acknowledge_alert(self, **kwargs: Any) -> Dict[str, Any]:
        return await acknowledge_alert(**kwargs)

    async def resolve_alert(self, **kwargs: Any) -> Dict[str, Any]:
        return await resolve_alert(**kwargs)

    async def dismiss_alert(self, **kwargs: Any) -> Dict[str, Any]:
        return await dismiss_alert(**kwargs)

    async def alert_summary(self, **kwargs: Any) -> Dict[str, Any]:
        return await get_alert_summary(**kwargs)

    # Knowledge base ---------------------------------------------------------

    async def list_knowledge_entries(self, **kwargs: Any) -> Dict[str, Any]:
        return await get_knowledge_entries(**kwargs)

    async def add_knowledge_entry(self, **kwargs: Any) -> Dict[str, Any]:
        return await create_knowledge_entry(**kwargs)

    async def list_patterns(self, **kwargs: Any) -> List[Dict[str, Any]]:
        from app.core.knowledge import list_patterns
        return list_patterns(**kwargs)

    async def add_pattern(self, **kwargs: Any) -> Dict[str, Any]:
        from app.core.knowledge import add_pattern
        return await add_pattern(**kwargs)

    async def search_patterns(self, **kwargs: Any) -> List[Dict[str, Any]]:
        from app.core.knowledge import search_patterns
        return await search_patterns(**kwargs)

    async def list_baselines(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await knowledge_base.get_baseline_metrics(**kwargs)

    async def set_baseline(self, **kwargs: Any) -> Dict[str, Any]:
        return await knowledge_base.set_baseline_metric(**kwargs)

    async def list_analysis_history(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await knowledge_base.get_analysis_history(**kwargs)

    async def run_workflow_analysis(
        self,
        *,
        analysis_type: str,
        log_group_names: List[str],
        time_range_minutes: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
        node_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a CloudWatch analysis by type (shared by workflow handler and API).

        Read-only triage types are cached within a short per-analyzer TTL
        (Phase 1) so repeated on-call checks and drill-down reuse don't re-bill
        the same AWS query. Credentials are bound via closure and never enter the
        cache key. Correlation / metrics / custom-query are never cached (unique
        or side-effecting inputs); their TTL maps to 0 → direct dispatch.
        """
        node_data = node_data or {}
        _ttl = {
            "alarms": settings.cloudwatch_cache_ttl_alarms,
            "error-patterns": settings.cloudwatch_cache_ttl_logs,
            "anomaly-detection": settings.cloudwatch_cache_ttl_logs,
            "activity-summary": settings.cloudwatch_cache_ttl_logs,
        }.get(analysis_type, 0)

        async def _run_dispatch(**_key: Any) -> Dict[str, Any]:
            # _key only differentiates cache entries; real args come via closure.
            return await self._dispatch_analysis(
                analysis_type=analysis_type,
                log_group_names=log_group_names,
                time_range_minutes=time_range_minutes,
                region=region,
                credentials=credentials,
                node_data=node_data,
            )

        if _ttl <= 0:
            return await _run_dispatch()

        from app.core.aws.cloudwatch_cache import cached_call

        return await cached_call(
            f"analysis:{analysis_type}",
            _run_dispatch,
            ttl_seconds=_ttl,
            log_group_names=sorted(log_group_names),
            time_range_minutes=time_range_minutes,
            region=region,
            alarm_prefix=node_data.get("alarmNamePrefix"),
            alarm_state=node_data.get("alarmStateFilter"),
        )

    async def _dispatch_analysis(
        self,
        *,
        analysis_type: str,
        log_group_names: List[str],
        time_range_minutes: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
        node_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dispatch a single CloudWatch analysis by type (no caching)."""
        node_data = node_data or {}

        if analysis_type == "error-patterns":
            return await self.analyze_patterns(
                log_group_names=log_group_names,
                time_range_minutes=time_range_minutes,
                pattern_types=["error", "warning"],
                region=region,
                credentials=credentials,
            )
        if analysis_type == "activity-summary":
            return await self.watch_logs(
                log_group_names=log_group_names,
                time_range_minutes=time_range_minutes,
                filter_pattern=None,
                max_events_per_group=100,
                regions=None,
                region=region,
                credentials=credentials,
            )
        if analysis_type == "anomaly-detection":
            return await self.detect_anomalies(
                log_group_names=log_group_names,
                time_range_minutes=time_range_minutes,
                baseline_minutes=1440,
                sensitivity="medium",
                region=region,
                credentials=credentials,
            )
        if analysis_type == "correlation":
            return await self.correlate_logs(
                log_group_names=log_group_names,
                correlation_id=None,
                trace_id=None,
                time_range_minutes=time_range_minutes,
                region=region,
                credentials=credentials,
            )
        if analysis_type == "metrics":
            metric_queries = node_data.get("metricQueries", [])
            # Phase 4: metrics is first-class — when no queries are hand-written,
            # discover them from the log groups (ListMetrics + namespace inference)
            # so the node works without bespoke MetricDataQuery dicts.
            if not metric_queries and settings.cloudwatch_metrics_discovery and log_group_names:
                from app.mcp.tools.metrics_tools import (
                    discover_metrics,
                    build_metric_queries_from_discovery,
                )
                disc = await discover_metrics(
                    log_group_names=log_group_names, region=region, credentials=credentials,
                )
                if isinstance(disc, dict) and not disc.get("error"):
                    metric_queries = build_metric_queries_from_discovery(disc)
            if not metric_queries:
                return {
                    "status": "failed",
                    "error": "No metricQueries configured and none discoverable for 'metrics' analysis type",
                }
            return await self.query_metrics(
                metric_queries=metric_queries,
                time_range_minutes=time_range_minutes,
                region=region,
                credentials=credentials,
            )
        if analysis_type == "alarms":
            return await self.list_alarms(
                alarm_name_prefix=node_data.get("alarmNamePrefix"),
                state_value=node_data.get("alarmStateFilter"),
                max_records=100,
                region=region,
                credentials=credentials,
            )
        if analysis_type == "custom-query":
            from app.mcp.tools.watch_tools import get_watcher, _extract_credentials

            custom_query = node_data.get("customInsightsQuery", "").strip()
            if not custom_query:
                return {
                    "status": "failed",
                    "error": "No customInsightsQuery set for 'custom-query' analysis type",
                }
            watcher = get_watcher(
                region=region,
                **_extract_credentials(credentials if credentials else {}),
            )
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=time_range_minutes)
            return await watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=custom_query,
                start_time=start_time,
                end_time=end_time,
            )

        return {
            "status": "failed",
            "error": f"Unknown analysis type: {analysis_type}",
        }

    async def execute_analyzer_node(
        self,
        *,
        analysis_type: str,
        log_groups: List[str],
        time_range: str,
        time_range_minutes: int,
        error_threshold: int,
        enable_alerts: bool,
        region: str,
        credentials: Optional[Dict[str, Any]],
        node_data: Dict[str, Any],
        active_executions: Dict[str, Dict[str, Any]],
        execution_id: str,
    ) -> Dict[str, Any]:
        """Full legacy cloudwatchAnalyzer pipeline: analyze, summarize, alert, LLM."""
        from app.workflow.executor.cloudwatch_analysis import (
            analyze_cloudwatch_with_llm,
            build_cloudwatch_summary,
            check_cloudwatch_alerts,
        )

        try:
            result = await self.run_workflow_analysis(
                analysis_type=analysis_type,
                log_group_names=log_groups,
                time_range_minutes=time_range_minutes,
                region=region,
                credentials=credentials,
                node_data=node_data,
            )

            if isinstance(result, dict) and result.get("error"):
                return {
                    "status": "failed",
                    "error": result.get("message", str(result.get("error"))),
                }

            output_summary = build_cloudwatch_summary(result, analysis_type, log_groups)

            alerts = []
            if enable_alerts and isinstance(result, dict):
                alerts = check_cloudwatch_alerts(result, analysis_type, error_threshold)

            llm_result = await analyze_cloudwatch_with_llm(
                active_executions=active_executions,
                execution_id=execution_id,
                raw_result=result,
                analysis_type=analysis_type,
                log_groups=log_groups,
                time_range=time_range,
                alerts=alerts,
            )
            if llm_result and len(llm_result) >= 5:
                llm_analysis, model_used, structured_analysis, llm_input_tokens, llm_output_tokens = (
                    llm_result[:5]
                )
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
                "input_tokens": llm_input_tokens,
                "output_tokens": llm_output_tokens,
                "total_tokens": llm_input_tokens + llm_output_tokens,
            }
        except Exception as e:
            logger.error("CloudWatch Analyzer execution failed: %s", e)
            return {"status": "failed", "error": str(e)}

    async def _run_targeted_correlation(
        self,
        *,
        log_groups: List[str],
        time_range: str,
        time_range_minutes: int,
        region: str,
        credentials: Optional[Dict[str, Any]],
        active_executions: Dict[str, Dict[str, Any]],
        execution_id: str,
        correlation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        focus: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fast path: trace a specific correlation/trace id (chat lookup).

        Skips the broad triage — runs one bounded ``correlate_logs`` then the same
        guaranteed synthesis / deterministic report as the full pipeline. Keeps the
        result shape compatible with the agent-seed collector.
        """
        from app.workflow.executor.cloudwatch_analysis import (
            analyze_cloudwatch_with_llm,
            build_rich_cloudwatch_report,
            is_usable_synthesis,
        )
        from app.workflow.tools.cloudwatch_summarizers import summarise_correlation

        ident = correlation_id or trace_id
        logger.info(
            "CloudWatch pipeline: targeted correlation lookup id=%s "
            "(correlation_id=%s trace_id=%s) — skipping broad triage (execution_id=%s)",
            ident, correlation_id, trace_id, execution_id,
        )
        data_quality: Dict[str, Any] = {
            "coverage": "targeted",
            "mode": "correlation-lookup",
            "correlation_id": correlation_id,
            "trace_id": trace_id,
            "failures": [],
            "partial": False,
            "sampled": False,
        }
        try:
            corr_raw = await asyncio.wait_for(
                self.correlate_logs(
                    log_group_names=log_groups,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    time_range_minutes=time_range_minutes,
                    region=region,
                    credentials=credentials,
                ),
                timeout=120.0,
            )
            evidence: Dict[str, Any] = {"correlation": summarise_correlation(corr_raw)}
        except asyncio.TimeoutError:
            data_quality["failures"].append(
                {"analysis": "correlation", "type": "timeout", "error": "120s timeout"}
            )
            evidence = {"correlation": {"error": "correlation lookup timed out after 120s"}}
        except Exception as exc:  # noqa: BLE001
            data_quality["failures"].append(
                {"analysis": "correlation", "type": _classify_aws_error(exc), "error": str(exc)}
            )
            evidence = {"correlation": {"error": str(exc)}}
        evidence["data_quality"] = data_quality

        llm_result = await analyze_cloudwatch_with_llm(
            active_executions=active_executions,
            execution_id=execution_id,
            raw_result=evidence,
            analysis_type="investigation",
            log_groups=log_groups,
            time_range=time_range,
            alerts=[],
            focus=focus,
        )
        llm_analysis = model_used = structured_analysis = None
        in_tok = out_tok = 0
        if llm_result and len(llm_result) >= 5:
            llm_analysis, model_used, structured_analysis, in_tok, out_tok = llm_result[:5]

        rich_report = build_rich_cloudwatch_report(evidence, log_groups, time_range, focus=focus)
        if is_usable_synthesis(llm_analysis):
            output = llm_analysis.strip() + "\n\n---\n\n### Evidence\n\n" + rich_report
        else:
            output = rich_report

        return {
            "status": "success",
            "output": output,
            "analysis_mode": "correlation-lookup",
            "analysis_type": "investigation",
            "log_groups_analyzed": log_groups,
            "time_range": time_range,
            "data": evidence,
            "alerts": None,
            "model": model_used,
            "structured_analysis": structured_analysis,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        }

    async def run_investigation_pipeline(
        self,
        *,
        log_groups: List[str],
        time_range: str,
        time_range_minutes: int,
        error_threshold: int,
        enable_alerts: bool,
        region: str,
        credentials: Optional[Dict[str, Any]],
        node_data: Dict[str, Any],
        active_executions: Dict[str, Dict[str, Any]],
        execution_id: str,
        depth: str = "auto",
        focus: Optional[str] = None,
        correlation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deterministic staged CloudWatch investigation with guaranteed synthesis.

        Stage 1 (parallel, deterministic): alarms + anomaly-detection + error-patterns
        (+ metrics when configured) → merged evidence bundle.
        Stage 2 (bounded, conditional): drill into the top findings with template-safe
        Insights queries (no free-text → no AWS ValidationException). Gated by depth.
        Stage 3 (always, terminal): one LLM synthesis → structured report. Falls back to
        a deterministic summary if no LLM node is wired.

        No ReAct loop — the synthesis is always the last step, so the result can never be
        a mid-investigation "Step N: let me…" fragment.
        """
        from app.workflow.executor.cloudwatch_analysis import (
            _fmt_drilldown_events,
            analyze_cloudwatch_with_llm,
            build_cloudwatch_summary,
            build_rich_cloudwatch_report,
            check_cloudwatch_alerts,
            format_cost_footer,
            is_usable_synthesis,
        )
        from app.workflow.tools.cloudwatch_drilldown import (
            build_insights_query_for_anomaly,
            build_insights_query_for_pattern,
            compute_drill_window,
            select_drill_targets,
            should_auto_drill_down,
        )
        from app.workflow.tools.cloudwatch_summarizers import (
            summarise_anomalies,
            summarise_correlation,
            summarise_patterns,
        )

        from app.core.aws import cloudwatch_cache

        depth = (depth or "auto").strip().lower()
        if depth not in ("shallow", "auto", "deep"):
            depth = "auto"

        # ── Run telemetry (Phase 0) — observational only, never gates behaviour ──
        # Aggregated into the result as ``run_stats`` so cost/coverage/cache
        # effectiveness are measurable. Excluded from the LLM synthesis payload
        # (build_synthesis_payload whitelists evidence keys) so it costs 0 tokens.
        cloudwatch_cache.reset_stats()
        run_stats: Dict[str, Any] = {
            "stages": {},
            "analyzers": {"succeeded": [], "failed": []},
            "drilldown": {"targets": 0, "completed": 0, "failed": 0, "sampled": 0},
            "insights": {
                "queries": 0,
                "bytes_scanned": 0.0,
                "records_scanned": 0.0,
                "records_matched": 0.0,
            },
        }
        _pipeline_t0 = time.monotonic()

        try:
            # ── Fast path — targeted correlation/trace lookup (chat) ────────────
            # When the user pasted a correlation/trace id, skip the broad triage and
            # go straight to a bounded correlate_logs, then the same synthesis.
            if correlation_id or trace_id:
                return await self._run_targeted_correlation(
                    log_groups=log_groups,
                    time_range=time_range,
                    time_range_minutes=time_range_minutes,
                    region=region,
                    credentials=credentials,
                    active_executions=active_executions,
                    execution_id=execution_id,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    focus=focus,
                )

            # ── Stage 1 — triage (parallel, deterministic) ──────────────────────
            stage_types = ["alarms", "anomaly-detection", "error-patterns"]
            # Metrics is first-class (Phase 4): run it when queries are configured
            # OR the node's analysis lens is "metrics" (then queries are discovered
            # from the log groups via ListMetrics).
            if node_data.get("metricQueries") or (
                focus == "metrics" and settings.cloudwatch_metrics_discovery
            ):
                stage_types.append("metrics")

            async def _run(atype: str):
                try:
                    res = await self.run_workflow_analysis(
                        analysis_type=atype,
                        log_group_names=log_groups,
                        time_range_minutes=time_range_minutes,
                        region=region,
                        credentials=credentials,
                        node_data=node_data,
                    )
                    return atype, res
                except Exception as exc:  # noqa: BLE001 — one analyzer must not sink the run
                    # Reduced-scope retry (Phase 1): for transient errors only
                    # (throttling/timeout — never access-denied/validation), give
                    # the analyzer one more attempt over a halved time window
                    # before recording the failure. The narrower window is more
                    # likely to complete; the result is flagged reduced-scope so
                    # coverage/severity stay honest about the degradation.
                    cat = _classify_aws_error(exc)
                    if settings.cloudwatch_reduced_scope_retry and cat in ("throttling", "timeout"):
                        narrowed = max(1, time_range_minutes // 2)
                        logger.info(
                            "Pipeline triage '%s' failed (%s); reduced-scope retry over %dm",
                            atype, cat, narrowed,
                        )
                        try:
                            res = await self.run_workflow_analysis(
                                analysis_type=atype,
                                log_group_names=log_groups,
                                time_range_minutes=narrowed,
                                region=region,
                                credentials=credentials,
                                node_data=node_data,
                            )
                            if isinstance(res, dict):
                                _dq = res.setdefault("data_quality", {})
                                _dq["partial"] = True
                                _dq["reduced_scope"] = True
                            run_stats["analyzers"].setdefault("reduced_scope", []).append(atype)
                            return atype, res
                        except Exception as exc2:  # noqa: BLE001
                            logger.warning(
                                "Pipeline triage '%s' reduced-scope retry failed: %s", atype, exc2
                            )
                            return atype, {"status": "failed", "error": str(exc2),
                                           "error_type": _classify_aws_error(exc2)}
                    logger.warning("Pipeline triage '%s' failed: %s", atype, exc)
                    return atype, {"status": "failed", "error": str(exc), "error_type": cat}

            _stage_t0 = time.monotonic()
            triage = dict(await asyncio.gather(*[_run(t) for t in stage_types]))
            run_stats["stages"]["triage_s"] = round(time.monotonic() - _stage_t0, 3)

            alarms_raw = triage.get("alarms") or {}
            anomalies_raw = triage.get("anomaly-detection") or {}
            patterns_raw = triage.get("error-patterns") or {}
            metrics_raw = triage.get("metrics")

            patterns_sum = summarise_patterns(patterns_raw)
            anomalies_sum = summarise_anomalies(anomalies_raw)

            evidence: Dict[str, Any] = {
                "alarms": alarms_raw,
                "anomalies": anomalies_sum,
                "patterns": patterns_sum,
            }
            if metrics_raw is not None:
                evidence["metrics"] = metrics_raw

            # ── Data coverage signalling ────────────────────────────────────────
            # Distinguish "genuinely nothing found" from "fetch failed / partial /
            # sampled" so an empty result is never silent. Inspect triage outcomes
            # plus each analyzer's own data_quality; drill-down sampling flags are
            # folded in below during Stage 2.
            data_quality: Dict[str, Any] = {
                "log_groups_requested": len(log_groups),
                "analyses_requested": list(stage_types),
                "failures": [],
                "partial": False,
                "sampled": False,
            }
            for _atype, _res in triage.items():
                if not isinstance(_res, dict):
                    continue
                _is_failed = (
                    str(_res.get("status", "")).lower() in ("failed", "error")
                    or _res.get("success") is False
                )
                if _is_failed:
                    data_quality["failures"].append(
                        {"analysis": _atype, "error": str(_res.get("error", "unknown"))}
                    )
                    run_stats["analyzers"]["failed"].append(_atype)
                else:
                    run_stats["analyzers"]["succeeded"].append(_atype)
                _dq = _res.get("data_quality")
                if isinstance(_dq, dict) and _dq.get("partial"):
                    data_quality["partial"] = True
            _failed = len(data_quality["failures"])
            _total = len(stage_types)
            data_quality["coverage"] = (
                "none" if (_total and _failed >= _total)
                else "partial" if _failed
                else "full"
            )
            evidence["data_quality"] = data_quality

            # ── Stage 2 — bounded, conditional drill-down ───────────────────────
            alarms_firing = bool(
                (alarms_raw.get("summary") or {}).get("in_alarm")
            ) if isinstance(alarms_raw, dict) else False
            high_anomaly = any(
                (a.get("severity") in ("critical", "high"))
                for a in (anomalies_sum.get("anomalies") or [])
            )

            if depth == "deep":
                do_drill = True
            elif depth == "auto":
                do_drill = (
                    should_auto_drill_down(patterns_sum)
                    or should_auto_drill_down(anomalies_sum)
                    or alarms_firing
                    or high_anomaly
                )
            else:  # shallow
                do_drill = False

            if do_drill and log_groups:
                top_n = settings.cloudwatch_pipeline_drilldown_top_n
                if depth == "deep":
                    top_n = max(top_n, 5)
                targets = select_drill_targets(patterns_sum, anomalies_sum, top_n=top_n)
                run_stats["drilldown"]["targets"] = len(targets)
                _drill_t0 = time.monotonic()

                from app.mcp.tools.watch_tools import get_watcher, _extract_credentials

                watcher = get_watcher(
                    region=region, **_extract_credentials(credentials or {}),
                )
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(minutes=time_range_minutes)

                # Build (target, query) pairs, then run all Insights queries
                # CONCURRENTLY. The sequential await loop was the dominant latency
                # (N × ~20-40s); asyncio.gather collapses it to ~one query's time.
                _pairs = []
                for tgt in targets:
                    if tgt.get("kind") == "pattern":
                        q = build_insights_query_for_pattern(
                            tgt.get("normalized_pattern", ""),
                            tgt.get("example_message", ""),
                        )
                    else:
                        q = build_insights_query_for_anomaly(tgt.get("log_group"))
                    _pairs.append((tgt, q))

                # Bound concurrent Insights queries (Phase 1). A wide drill-down
                # fan-out (top_n × log groups) can otherwise trip CloudWatch's
                # StartQuery concurrency limit and self-throttle the whole run.
                _conc = max(1, settings.cloudwatch_insights_max_concurrency)
                _insights_sem = asyncio.Semaphore(_conc)

                async def _bounded_query(q: str, qstart: datetime, qend: datetime):
                    async with _insights_sem:
                        return await watcher.query_with_insights(
                            log_group_names=log_groups,
                            query_string=q,
                            start_time=qstart,
                            end_time=qend,
                        )

                # Scan budget (Phase 3): process drill-downs in waves so the
                # cumulative bytes scanned can be checked between waves. Each
                # target queries only its cost-aware window (pattern first/last
                # seen, clamped to the triage window). Once the budget is
                # exceeded, remaining targets degrade to a sampled (last-25%)
                # window instead of failing — recorded as budget_limited.
                _budget_bytes = settings.cloudwatch_max_gb_scanned_per_run * 1e9
                _budget_limited = False
                drilldowns: List[Dict[str, Any]] = []

                for _w in range(0, len(_pairs), _conc):
                    wave = _pairs[_w:_w + _conc]
                    _coros = []
                    for tgt, q in wave:
                        if _budget_limited:
                            _span = end_time - start_time
                            qstart = (end_time - (_span / 4)
                                      if _span.total_seconds() > 0 else start_time)
                            qend = end_time
                        else:
                            qstart, qend = compute_drill_window(tgt, start_time, end_time)
                        _coros.append(_bounded_query(q, qstart, qend))

                    wave_results = await asyncio.gather(*_coros, return_exceptions=True)

                    for (tgt, _q), res in zip(wave, wave_results):
                        label = tgt.get("label")
                        if isinstance(res, Exception):
                            _cat = _classify_aws_error(res)
                            logger.warning("Pipeline drill-down failed for %s (%s): %s",
                                           label, _cat, res)
                            data_quality["failures"].append(
                                {"analysis": f"drilldown:{label}", "type": _cat, "error": str(res)}
                            )
                            run_stats["drilldown"]["failed"] += 1
                            drilldowns.append({"target": label, "error": str(res), "error_type": _cat})
                            continue
                        run_stats["drilldown"]["completed"] += 1
                        # Aggregate Insights scan statistics (Phase 0). CloudWatch returns
                        # bytesScanned/recordsScanned/recordsMatched per query — these drive
                        # the Phase 3 cost accounting and are summed across the run.
                        if isinstance(res, dict):
                            _st = res.get("statistics") or {}
                            if _st:
                                run_stats["insights"]["queries"] += 1
                                run_stats["insights"]["bytes_scanned"] += float(_st.get("bytesScanned", 0) or 0)
                                run_stats["insights"]["records_scanned"] += float(_st.get("recordsScanned", 0) or 0)
                                run_stats["insights"]["records_matched"] += float(_st.get("recordsMatched", 0) or 0)
                        # Fold drill-down degradation into coverage signalling.
                        if isinstance(res, dict) and res.get("partial"):
                            data_quality["partial"] = True
                            if res.get("sampling_ratio"):
                                data_quality["sampled"] = True
                                run_stats["drilldown"]["sampled"] += 1
                        # Store CLEAN, readable event lines (@timestamp/@message/@logStream),
                        # already capped by _fmt_drilldown_events — no extra cap needed.
                        drilldowns.append({
                            "target": label,
                            "events": _fmt_drilldown_events(
                                res, max_chars=settings.cloudwatch_drill_sample_chars,
                            ),
                        })

                    if (not _budget_limited
                            and run_stats["insights"]["bytes_scanned"] > _budget_bytes):
                        _budget_limited = True
                        data_quality["budget_limited"] = True
                        run_stats["budget_limited"] = True
                        logger.info(
                            "CW drill-down scan budget (%.2f GB) exceeded — sampling "
                            "remaining targets", settings.cloudwatch_max_gb_scanned_per_run,
                        )

                if drilldowns:
                    evidence["drilldown"] = drilldowns
                run_stats["stages"]["drilldown_s"] = round(time.monotonic() - _drill_t0, 3)

                # Correlation is the heaviest step — only on deep (or when enabled).
                # Bounded so a slow Insights query can't run the whole pipeline away.
                if settings.cloudwatch_pipeline_enable_correlation and depth == "deep":
                    try:
                        corr = await asyncio.wait_for(
                            self.run_workflow_analysis(
                                analysis_type="correlation",
                                log_group_names=log_groups,
                                time_range_minutes=time_range_minutes,
                                region=region,
                                credentials=credentials,
                                node_data=node_data,
                            ),
                            timeout=120.0,
                        )
                        evidence["correlation"] = summarise_correlation(corr)
                    except asyncio.TimeoutError:
                        logger.warning("Pipeline correlation timed out after 120s; skipping")
                        data_quality["failures"].append(
                            {"analysis": "correlation", "type": "timeout", "error": "120s timeout"}
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Pipeline correlation failed: %s", exc)

            # ── Stage 3 — alerts + guaranteed synthesis (terminal) ──────────────
            alerts: List[Any] = []
            if enable_alerts:
                for raw, atype in ((patterns_raw, "error-patterns"),
                                   (anomalies_raw, "anomaly-detection")):
                    if isinstance(raw, dict):
                        try:
                            alerts.extend(
                                check_cloudwatch_alerts(raw, atype, error_threshold) or []
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("alert check (%s) skipped: %s", atype, exc)

            _synth_t0 = time.monotonic()
            llm_result = await analyze_cloudwatch_with_llm(
                active_executions=active_executions,
                execution_id=execution_id,
                raw_result=evidence,
                analysis_type="investigation",
                log_groups=log_groups,
                time_range=time_range,
                alerts=alerts,
                focus=focus,
            )
            run_stats["stages"]["synthesis_s"] = round(time.monotonic() - _synth_t0, 3)
            if llm_result and len(llm_result) >= 5:
                llm_analysis, model_used, structured_analysis, in_tok, out_tok = llm_result[:5]
            elif llm_result and len(llm_result) == 3:
                llm_analysis, model_used, structured_analysis = llm_result
                in_tok = out_tok = 0
            else:
                llm_analysis = model_used = structured_analysis = None
                in_tok = out_tok = 0

            # Guaranteed, detail-rich output. The deterministic evidence report
            # (actual error messages, correlation/request/trace IDs from drill-down
            # raw events, timestamps, anomalies, alarms) is ALWAYS built and is the
            # floor. A usable LLM synthesis is shown on top of it; a refusal/empty
            # synthesis (e.g. provider guardrail returning "cannot answer this
            # question") is discarded so it never becomes the result.
            rich_report = build_rich_cloudwatch_report(evidence, log_groups, time_range, focus=focus)
            if is_usable_synthesis(llm_analysis):
                output = (
                    llm_analysis.strip()
                    + "\n\n---\n\n### Evidence\n\n"
                    + rich_report
                )
            else:
                if llm_analysis:
                    logger.info(
                        "Pipeline synthesis discarded (refusal/empty, %d chars) — "
                        "using deterministic evidence report",
                        len(str(llm_analysis)),
                    )
                output = rich_report

            run_stats["stages"]["total_s"] = round(time.monotonic() - _pipeline_t0, 3)
            run_stats["cache"] = cloudwatch_cache.stats()
            run_stats["synthesis_tokens"] = {"input": in_tok, "output": out_tok}

            # Cost footer (Phase 3) — appended to the engineer-facing output only,
            # never to the LLM synthesis input.
            _footer = format_cost_footer(run_stats)
            if _footer:
                output = output + _footer

            return {
                "status": "success",
                "output": output,
                "analysis_mode": "pipeline",
                "depth": depth,
                "run_stats": run_stats,
                "analysis_type": "investigation",
                "log_groups_analyzed": log_groups,
                "time_range": time_range,
                "data": evidence,
                "alerts": alerts if alerts else None,
                "model": model_used,
                "structured_analysis": structured_analysis,
                "stages": {
                    "triage": stage_types,
                    "drilldown": bool(evidence.get("drilldown")),
                    "correlation": bool(evidence.get("correlation")),
                    "synthesized": llm_analysis is not None,
                },
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
            }
        except Exception as e:
            logger.error("CloudWatch investigation pipeline failed: %s", e)
            return {"status": "failed", "error": str(e)}

    async def run_multiregion_investigation(
        self,
        *,
        regions: List[str],
        log_groups: List[str],
        time_range: str,
        time_range_minutes: int,
        error_threshold: int,
        enable_alerts: bool,
        credentials: Optional[Dict[str, Any]],
        node_data: Dict[str, Any],
        active_executions: Dict[str, Dict[str, Any]],
        execution_id: str,
        focus: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fan an investigation across regions, then synthesize once (Phase 5).

        Runs the deterministic triage (alarms + anomaly-detection + error-patterns)
        per region under a concurrency bound, namespaces every finding as
        ``region:log_group``, merges into a single evidence bundle, and produces
        one LLM synthesis. A failing region degrades to partial coverage rather
        than sinking the whole run. Same account/credentials across regions —
        cross-account is out of scope.
        """
        from app.workflow.executor.cloudwatch_analysis import (
            analyze_cloudwatch_with_llm,
            build_rich_cloudwatch_report,
            is_usable_synthesis,
            merge_region_evidence,
        )
        from app.workflow.tools.cloudwatch_summarizers import (
            summarise_anomalies,
            summarise_patterns,
        )

        regions = (regions or [])[: max(1, settings.cloudwatch_max_regions_per_run)]
        sem = asyncio.Semaphore(max(1, settings.cloudwatch_insights_max_concurrency))
        stage_types = ["alarms", "anomaly-detection", "error-patterns"]

        async def _region_evidence(region: str):
            async def _triage(atype: str):
                try:
                    return atype, await self.run_workflow_analysis(
                        analysis_type=atype, log_group_names=log_groups,
                        time_range_minutes=time_range_minutes, region=region,
                        credentials=credentials, node_data=node_data,
                    )
                except Exception as exc:  # noqa: BLE001 — one analyzer/region must not sink the run
                    return atype, {"status": "failed", "error": str(exc),
                                   "error_type": _classify_aws_error(exc)}

            async with sem:
                triage = dict(await asyncio.gather(*[_triage(t) for t in stage_types]))

            failures = []
            for atype, res in triage.items():
                if isinstance(res, dict) and (
                    str(res.get("status", "")).lower() in ("failed", "error")
                    or res.get("success") is False
                ):
                    failures.append({"analysis": atype, "error": str(res.get("error", "unknown"))})
            coverage = ("none" if len(failures) >= len(stage_types)
                        else "partial" if failures else "full")
            return region, {
                "alarms": triage.get("alarms") or {},
                "patterns": summarise_patterns(triage.get("error-patterns") or {}),
                "anomalies": summarise_anomalies(triage.get("anomaly-detection") or {}),
                "data_quality": {"coverage": coverage, "partial": bool(failures),
                                 "failures": failures},
            }

        try:
            pairs = await asyncio.gather(*[_region_evidence(r) for r in regions])
            per_region = dict(pairs)
            merged = merge_region_evidence(per_region)

            llm_result = await analyze_cloudwatch_with_llm(
                active_executions=active_executions, execution_id=execution_id,
                raw_result=merged, analysis_type="investigation",
                log_groups=[f"{r}:{g}" for r in regions for g in log_groups],
                time_range=time_range, alerts=[], focus=focus,
            )
            if llm_result and len(llm_result) >= 5:
                llm_analysis, model_used, structured_analysis, in_tok, out_tok = llm_result[:5]
            elif llm_result and len(llm_result) == 3:
                llm_analysis, model_used, structured_analysis = llm_result
                in_tok = out_tok = 0
            else:
                llm_analysis = model_used = structured_analysis = None
                in_tok = out_tok = 0

            rich_report = build_rich_cloudwatch_report(merged, log_groups, time_range, focus=focus)
            output = (llm_analysis.strip() + "\n\n---\n\n### Evidence\n\n" + rich_report
                      if is_usable_synthesis(llm_analysis) else rich_report)

            return {
                "status": "success",
                "output": output,
                "analysis_mode": "multi-region",
                "analysis_type": "investigation",
                "log_groups_analyzed": log_groups,
                "regions": regions,
                "time_range": time_range,
                "data": merged,
                "model": model_used,
                "structured_analysis": structured_analysis,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
            }
        except Exception as e:
            logger.error("CloudWatch multi-region investigation failed: %s", e)
            return {"status": "failed", "error": str(e)}


log_watch_service = LogWatchService()
