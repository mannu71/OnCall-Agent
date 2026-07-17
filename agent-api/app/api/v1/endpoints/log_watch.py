"""API endpoints for CloudWatch Log Watch Analyzer — thin HTTP layer."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.streaming.sse import SSE_HEADERS
from app.services.log_watch_service import log_watch_service

router = APIRouter(prefix="/log-watch", tags=["Log Watch Analyzer"])


class AWSCredentials(BaseModel):
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    aws_profile: Optional[str] = Field(None, description="AWS profile name from ~/.aws/credentials")
    use_env_credentials: bool = Field(False, description="Use environment variables for credentials")


class TestConnectionRequest(BaseModel):
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class WatchLogGroupsRequest(BaseModel):
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    time_range_minutes: int = Field(60, description="Time range in minutes to look back")
    filter_pattern: Optional[str] = Field(None, description="CloudWatch Logs filter pattern")
    max_events_per_group: int = Field(500, ge=1, le=2000, description="Max events per group")
    regions: Optional[List[str]] = Field(None, description="Optional list of AWS regions")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class AnalyzePatternsRequest(BaseModel):
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    time_range_minutes: int = Field(60, description="Time range in minutes to analyze")
    pattern_types: Optional[List[str]] = Field(None, description="Pattern types to analyze")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class DetectAnomaliesRequest(BaseModel):
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    time_range_minutes: int = Field(60, description="Current time range in minutes")
    baseline_minutes: int = Field(1440, description="Baseline time range for comparison")
    sensitivity: str = Field("medium", description="Detection sensitivity (low, medium, high)")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class CorrelateLogsRequest(BaseModel):
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    correlation_id: Optional[str] = Field(None, description="Correlation ID to search for")
    trace_id: Optional[str] = Field(None, description="AWS X-Ray trace ID")
    time_range_minutes: int = Field(60, description="Time range in minutes to search")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class MetricDataRequest(BaseModel):
    metric_queries: List[Dict[str, Any]] = Field(..., description="List of MetricDataQuery dicts")
    time_range_minutes: int = Field(60, description="How far back to query (minutes)")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class MetricStatisticsRequest(BaseModel):
    namespace: str = Field(..., description="CloudWatch namespace")
    metric_name: str = Field(..., description="Metric name")
    dimensions: List[Dict[str, str]] = Field(..., description="List of {Name, Value} pairs")
    statistics: Optional[List[str]] = Field(None, description="Statistics to return")
    period_seconds: int = Field(300, description="Aggregation period in seconds")
    time_range_minutes: int = Field(60, description="How far back to query (minutes)")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class ListAlarmsRequest(BaseModel):
    alarm_name_prefix: Optional[str] = Field(None, description="Alarm name prefix filter")
    state_value: Optional[str] = Field(None, description="State filter: ALARM | OK | INSUFFICIENT_DATA")
    max_records: int = Field(100, ge=1, le=500, description="Maximum alarms to return")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class CreateAlertRequest(BaseModel):
    log_group: str = Field(..., description="Log group where issue was detected")
    alert_type: str = Field(..., description="Type of alert")
    severity: str = Field(..., description="Alert severity")
    message: str = Field(..., description="Alert message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")


class AcknowledgeAlertRequest(BaseModel):
    alert_id: int = Field(..., description="Alert ID")
    acknowledged_by: Optional[str] = Field(None, description="User acknowledging")
    notes: Optional[str] = Field(None, description="Acknowledgment notes")


class ResolveAlertRequest(BaseModel):
    alert_id: int = Field(..., description="Alert ID")
    resolved_by: str = Field(..., description="User resolving the alert")
    resolution: str = Field(..., description="Resolution description")


class CreateKnowledgeEntryRequest(BaseModel):
    title: str = Field(..., description="Entry title")
    description: str = Field(..., description="Detailed description")
    symptoms: List[str] = Field(..., description="List of symptoms / trigger phrases")
    solution: str = Field(..., description="Resolution steps")
    category: str = Field(..., description="Entry category")


CreateKnownIssueRequest = CreateKnowledgeEntryRequest


class AddPatternRequest(BaseModel):
    name: str = Field(..., description="Pattern name")
    pattern: str = Field(..., description="Pattern regex or text")
    pattern_type: str = Field(..., description="Pattern type")
    severity: int = Field(1, description="Severity level (1-5)")
    description: Optional[str] = Field(None, description="Pattern description")


class SetBaselineRequest(BaseModel):
    metric_name: str = Field(..., description="Metric name")
    log_group: str = Field(..., description="Associated log group")
    normal_range_min: float = Field(..., description="Minimum normal value")
    normal_range_max: float = Field(..., description="Maximum normal value")
    threshold_warning: float = Field(..., description="Warning threshold")
    threshold_critical: float = Field(..., description="Critical threshold")
    time_window: str = Field("5m", description="Time window")


async def _resolve_credentials(
    request_credentials: Optional[AWSCredentials],
    region: str,
    *,
    aws_profile: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], str]:
    creds = request_credentials.model_dump() if request_credentials else None
    return await log_watch_service.resolve_credentials(
        creds, region, aws_profile=aws_profile
    )


@router.post("/test-connection")
async def test_aws_connection(request: TestConnectionRequest) -> Dict[str, Any]:
    """Test AWS credentials by attempting to connect to CloudWatch Logs."""
    creds = request.credentials.model_dump() if request.credentials else None
    return await log_watch_service.test_connection(region=request.region, credentials=creds)


@router.post("/watch")
async def watch_logs(request: WatchLogGroupsRequest) -> Dict[str, Any]:
    """Watch multiple CloudWatch log groups and fetch recent logs."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.watch_logs(
        log_group_names=request.log_group_names,
        time_range_minutes=request.time_range_minutes,
        filter_pattern=request.filter_pattern,
        max_events_per_group=request.max_events_per_group,
        regions=request.regions,
        region=region,
        credentials=credentials,
    )


@router.post("/analyze-patterns")
async def analyze_patterns(request: AnalyzePatternsRequest) -> Dict[str, Any]:
    """Analyze log patterns across multiple log groups."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.analyze_patterns(
        log_group_names=request.log_group_names,
        time_range_minutes=request.time_range_minutes,
        pattern_types=request.pattern_types,
        region=region,
        credentials=credentials,
    )


@router.post("/detect-anomalies")
async def detect_log_anomalies(request: DetectAnomaliesRequest) -> Dict[str, Any]:
    """Detect anomalies in log patterns compared to baseline."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.detect_anomalies(
        log_group_names=request.log_group_names,
        time_range_minutes=request.time_range_minutes,
        baseline_minutes=request.baseline_minutes,
        sensitivity=request.sensitivity,
        region=region,
        credentials=credentials,
    )


@router.post("/correlate")
async def correlate_log_events(request: CorrelateLogsRequest) -> Dict[str, Any]:
    """Correlate logs across multiple services using trace ID or correlation ID."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.correlate_logs(
        log_group_names=request.log_group_names,
        correlation_id=request.correlation_id,
        trace_id=request.trace_id,
        time_range_minutes=request.time_range_minutes,
        region=region,
        credentials=credentials,
    )


@router.get("/discover-log-groups")
async def discover_log_groups_endpoint(
    prefix: Optional[str] = Query(None, description="Log group name prefix"),
    tag_key: Optional[str] = Query(None, description="Tag key to filter by"),
    tag_value: Optional[str] = Query(None, description="Tag value to filter by"),
    limit: int = Query(50, ge=1, le=200, description="Max log groups to return"),
    region: str = Query("us-east-1", description="AWS region"),
    profile: Optional[str] = Query(None, description="AWS CLI profile name"),
) -> Dict[str, Any]:
    """Discover CloudWatch log groups by name prefix or resource tags."""
    credentials, resolved_region = await _resolve_credentials(None, region, aws_profile=profile)
    return await log_watch_service.discover_log_groups(
        prefix=prefix,
        tag_key=tag_key,
        tag_value=tag_value,
        limit=limit,
        region=resolved_region,
        credentials=credentials,
    )


@router.get("/aws-profiles")
async def list_aws_profiles_endpoint() -> Dict[str, Any]:
    """List AWS profiles from the shared credentials/config files with expiry state.

    Powers the CloudWatch node's profile dropdown so operators pick a *valid*
    profile at the node level instead of blindly typing one. Offline — no STS
    calls; expiry comes from each profile's recorded ``aws_expiration``.
    """
    from app.core.aws.aws_credentials import list_aws_profiles
    profiles = list_aws_profiles()
    return {"profiles": profiles, "count": len(profiles)}


@router.post("/metrics")
async def query_metrics(request: MetricDataRequest) -> Dict[str, Any]:
    """Query CloudWatch Metrics using GetMetricData."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.query_metrics(
        metric_queries=request.metric_queries,
        time_range_minutes=request.time_range_minutes,
        region=region,
        credentials=credentials,
    )


@router.post("/metric-statistics")
async def query_metric_statistics(request: MetricStatisticsRequest) -> Dict[str, Any]:
    """Query a single CloudWatch metric via GetMetricStatistics."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.query_metric_statistics(
        namespace=request.namespace,
        metric_name=request.metric_name,
        dimensions=request.dimensions,
        statistics=request.statistics,
        period_seconds=request.period_seconds,
        time_range_minutes=request.time_range_minutes,
        region=region,
        credentials=credentials,
    )


@router.post("/alarms")
async def list_alarms(request: ListAlarmsRequest) -> Dict[str, Any]:
    """List CloudWatch Metric Alarms."""
    credentials, region = await _resolve_credentials(request.credentials, request.region)
    return await log_watch_service.list_alarms(
        alarm_name_prefix=request.alarm_name_prefix,
        state_value=request.state_value,
        max_records=request.max_records,
        region=region,
        credentials=credentials,
    )


@router.get("/stream")
async def stream_log_events(
    log_group_name: str = Query(..., description="CloudWatch log group name to stream"),
    filter_pattern: Optional[str] = Query(None, description="Optional filter pattern"),
    poll_interval_seconds: int = Query(10, ge=5, le=60, description="Polling interval in seconds"),
    region: str = Query("us-east-1", description="AWS region"),
) -> StreamingResponse:
    """Stream new CloudWatch log events as Server-Sent Events (SSE)."""
    credentials, resolved_region = await _resolve_credentials(None, region)
    return StreamingResponse(
        log_watch_service.stream_log_events(
            log_group_name=log_group_name,
            filter_pattern=filter_pattern,
            poll_interval_seconds=poll_interval_seconds,
            region=resolved_region,
            credentials=credentials,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/alerts")
async def create_new_alert(request: CreateAlertRequest) -> Dict[str, Any]:
    """Create a new alert from log analysis."""
    return await log_watch_service.create_alert(
        log_group=request.log_group,
        alert_type=request.alert_type,
        severity=request.severity,
        message=request.message,
        details=request.details,
    )


@router.get("/alerts")
async def list_alerts(
    status: Optional[str] = Query(None, description="Filter by status"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    log_group: Optional[str] = Query(None, description="Filter by log group"),
    limit: int = Query(50, description="Maximum results"),
) -> Dict[str, Any]:
    """Get alerts with optional filtering."""
    return await log_watch_service.list_alerts(
        status=status,
        severity=severity,
        log_group=log_group,
        limit=limit,
    )


@router.post("/alerts/{alert_id}/acknowledge")
async def ack_alert(alert_id: int, request: AcknowledgeAlertRequest) -> Dict[str, Any]:
    """Acknowledge an alert."""
    return await log_watch_service.acknowledge_alert(
        alert_id=alert_id,
        acknowledged_by=request.acknowledged_by,
        notes=request.notes,
    )


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert_endpoint(alert_id: int, request: ResolveAlertRequest) -> Dict[str, Any]:
    """Resolve an alert."""
    return await log_watch_service.resolve_alert(
        alert_id=alert_id,
        resolved_by=request.resolved_by,
        resolution=request.resolution,
    )


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert_endpoint(
    alert_id: int,
    dismissed_by: str = Query(..., description="User dismissing the alert"),
    reason: str = Query(..., description="Reason for dismissal"),
) -> Dict[str, Any]:
    """Dismiss an alert as false positive."""
    return await log_watch_service.dismiss_alert(
        alert_id=alert_id,
        dismissed_by=dismissed_by,
        reason=reason,
    )


@router.get("/alerts/summary")
async def alert_summary(
    time_range_hours: int = Query(24, description="Time range in hours"),
) -> Dict[str, Any]:
    """Get alert summary for dashboard."""
    return await log_watch_service.alert_summary(time_range_hours=time_range_hours)


@router.get("/knowledge-entries")
async def list_knowledge_entries(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, description="Maximum results"),
) -> Dict[str, Any]:
    """Get knowledge entries from the knowledge base."""
    return await log_watch_service.list_knowledge_entries(category=category, limit=limit)


@router.get("/known-issues", include_in_schema=False)
async def list_known_issues_compat(
    category: Optional[str] = Query(None),
    limit: int = Query(50),
) -> Dict[str, Any]:
    return await log_watch_service.list_knowledge_entries(category=category, limit=limit)


@router.post("/knowledge-entries")
async def add_knowledge_entry(request: CreateKnowledgeEntryRequest) -> Dict[str, Any]:
    """Add a knowledge entry to the knowledge base."""
    return await log_watch_service.add_knowledge_entry(
        title=request.title,
        description=request.description,
        symptoms=request.symptoms,
        solution=request.solution,
        category=request.category,
    )


@router.post("/known-issues", include_in_schema=False)
async def add_known_issue_compat(request: CreateKnowledgeEntryRequest) -> Dict[str, Any]:
    return await log_watch_service.add_knowledge_entry(
        title=request.title,
        description=request.description,
        symptoms=request.symptoms,
        solution=request.solution,
        category=request.category,
    )


@router.get("/patterns")
async def list_patterns(
    pattern_type: Optional[str] = Query(None, description="Filter by type"),
    limit: int = Query(100, description="Maximum results"),
) -> List[Dict[str, Any]]:
    """List log patterns from knowledge base."""
    return await log_watch_service.list_patterns(pattern_type=pattern_type, limit=limit)


@router.post("/patterns")
async def add_pattern(request: AddPatternRequest) -> Dict[str, Any]:
    """Add a log pattern to knowledge base."""
    return await log_watch_service.add_pattern(
        name=request.name,
        pattern=request.pattern,
        pattern_type=request.pattern_type,
        severity=request.severity,
        description=request.description,
    )


@router.get("/patterns/search")
async def search_patterns(
    query: str = Query(..., description="Search query"),
    limit: int = Query(10, description="Maximum results"),
    threshold: float = Query(0.7, description="Similarity threshold"),
) -> List[Dict[str, Any]]:
    """Search for similar log patterns using vector similarity."""
    return await log_watch_service.search_patterns(
        query=query,
        limit=limit,
        threshold=threshold,
    )


@router.get("/baselines")
async def list_baselines(
    log_group: Optional[str] = Query(None, description="Filter by log group"),
) -> List[Dict[str, Any]]:
    """List baseline metrics."""
    return await log_watch_service.list_baselines(log_group=log_group)


@router.post("/baselines")
async def set_baseline(request: SetBaselineRequest) -> Dict[str, Any]:
    """Set or update a baseline metric."""
    return await log_watch_service.set_baseline(
        metric_name=request.metric_name,
        log_group=request.log_group,
        normal_range_min=request.normal_range_min,
        normal_range_max=request.normal_range_max,
        threshold_warning=request.threshold_warning,
        threshold_critical=request.threshold_critical,
        time_window=request.time_window,
    )


@router.get("/analysis-history")
async def list_analysis_history(
    log_group: Optional[str] = Query(None, description="Filter by log group"),
    analysis_type: Optional[str] = Query(None, description="Filter by analysis type"),
    limit: int = Query(50, description="Maximum results"),
) -> List[Dict[str, Any]]:
    """Get analysis history."""
    return await log_watch_service.list_analysis_history(
        log_group=log_group,
        analysis_type=analysis_type,
        limit=limit,
    )
