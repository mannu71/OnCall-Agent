"""Log Watch service — CloudWatch operations behind the HTTP API."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import settings
from app.core.aws_credentials import resolve_aws_credentials
from app.core.retry import with_retry
from app.core.sse import HEARTBEAT_INTERVAL_SECONDS, STREAM_TIMEOUT_SECONDS
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
    ) -> Dict[str, Any]:
        return await with_retry(
            list_metric_alarms,
            alarm_name_prefix=alarm_name_prefix,
            state_value=state_value,
            region=region,
            credentials=credentials,
            max_records=max_records,
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
        return await knowledge_base.list_log_patterns(**kwargs)

    async def add_pattern(self, **kwargs: Any) -> Dict[str, Any]:
        return await knowledge_base.add_log_pattern(**kwargs)

    async def search_patterns(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return await knowledge_base.search_similar_patterns(**kwargs)

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
        """Run a CloudWatch analysis by type (shared by workflow handler and API)."""
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
            if not metric_queries:
                return {
                    "status": "failed",
                    "error": "No metricQueries configured for 'metrics' analysis type",
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


log_watch_service = LogWatchService()
