"""API endpoints for CloudWatch Log Watch Analyzer."""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_model_key_repo
from app.infrastructure.persistence import ModelKeyRepository
from app.mcp.tools.watch_tools import (
    watch_log_groups,
    analyze_log_patterns,
    detect_anomalies,
    correlate_logs
)
from app.mcp.tools.alert_tools import (
    create_alert,
    get_alerts,
    acknowledge_alert,
    resolve_alert,
    dismiss_alert,
    get_alert_summary,
    get_known_issues,
    create_known_issue
)
from app.services.knowledge_base import knowledge_base
from app.core.retry import with_retry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/log-watch", tags=["Log Watch Analyzer"])


async def _resolve_aws_credentials(
    request_credentials: Optional[Any],
    region: str,
    *,
    repo: ModelKeyRepository,
) -> tuple[Optional[Dict[str, Any]], str]:
    """Return (credentials_dict, region) from request body or DB fallback."""
    if request_credentials:
        return request_credentials.model_dump(), region

    # Fall back to credentials stored in the model_keys table
    try:
        for key_name in ("AWS CloudWatch", "cloudwatch", "AWS Bedrock", "bedrock", "aws bedrock", "aws"):
            mk = await repo.get_by_provider(key_name, include_secrets=True)
            if mk and mk.get("access_key_id"):
                creds: Dict[str, Any] = {
                    "access_key_id": mk["access_key_id"],
                    "secret_access_key": mk.get("secret_access_key"),
                }
                if mk.get("session_token"):
                    creds["session_token"] = mk["session_token"]
                # Use stored region only when caller left it at default
                if mk.get("region") and region == "us-east-1":
                    region = mk["region"]
                return creds, region
    except Exception as e:
        logger.warning("Could not load AWS credentials from model_keys: %s", e)

    return None, region


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class AWSCredentials(BaseModel):
    """AWS credentials configuration."""
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    aws_profile: Optional[str] = Field(None, description="AWS profile name from ~/.aws/credentials")
    use_env_credentials: bool = Field(False, description="Use environment variables for credentials")


class TestConnectionRequest(BaseModel):
    """Request model for testing AWS credentials."""
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class WatchLogGroupsRequest(BaseModel):
    """Request model for watching log groups."""
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    time_range_minutes: int = Field(60, description="Time range in minutes to look back")
    filter_pattern: Optional[str] = Field(None, description="CloudWatch Logs filter pattern")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class AnalyzePatternsRequest(BaseModel):
    """Request model for pattern analysis."""
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    time_range_minutes: int = Field(60, description="Time range in minutes to analyze")
    pattern_types: Optional[List[str]] = Field(None, description="Pattern types to analyze")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class DetectAnomaliesRequest(BaseModel):
    """Request model for anomaly detection."""
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    time_range_minutes: int = Field(60, description="Current time range in minutes")
    baseline_minutes: int = Field(1440, description="Baseline time range for comparison")
    sensitivity: str = Field("medium", description="Detection sensitivity (low, medium, high)")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class CorrelateLogsRequest(BaseModel):
    """Request model for log correlation."""
    log_group_names: List[str] = Field(..., description="List of CloudWatch log group names")
    correlation_id: Optional[str] = Field(None, description="Correlation ID to search for")
    trace_id: Optional[str] = Field(None, description="AWS X-Ray trace ID")
    time_range_minutes: int = Field(60, description="Time range in minutes to search")
    region: str = Field("us-east-1", description="AWS region")
    credentials: Optional[AWSCredentials] = Field(None, description="AWS credentials")


class CreateAlertRequest(BaseModel):
    """Request model for creating alerts."""
    log_group: str = Field(..., description="Log group where issue was detected")
    alert_type: str = Field(..., description="Type of alert")
    severity: str = Field(..., description="Alert severity (critical, high, medium, low, info)")
    message: str = Field(..., description="Alert message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")


class AcknowledgeAlertRequest(BaseModel):
    """Request model for acknowledging alerts."""
    alert_id: int = Field(..., description="Alert ID")
    acknowledged_by: Optional[str] = Field(None, description="User acknowledging")
    notes: Optional[str] = Field(None, description="Acknowledgment notes")


class ResolveAlertRequest(BaseModel):
    """Request model for resolving alerts."""
    alert_id: int = Field(..., description="Alert ID")
    resolved_by: str = Field(..., description="User resolving the alert")
    resolution: str = Field(..., description="Resolution description")


class CreateKnownIssueRequest(BaseModel):
    """Request model for creating known issues."""
    title: str = Field(..., description="Issue title")
    description: str = Field(..., description="Detailed description")
    symptoms: List[str] = Field(..., description="List of symptoms")
    solution: str = Field(..., description="Resolution steps")
    category: str = Field(..., description="Issue category")


class AddPatternRequest(BaseModel):
    """Request model for adding log patterns."""
    name: str = Field(..., description="Pattern name")
    pattern: str = Field(..., description="Pattern regex or text")
    pattern_type: str = Field(..., description="Pattern type")
    severity: int = Field(1, description="Severity level (1-5)")
    description: Optional[str] = Field(None, description="Pattern description")


class SetBaselineRequest(BaseModel):
    """Request model for setting baseline metrics."""
    metric_name: str = Field(..., description="Metric name")
    log_group: str = Field(..., description="Associated log group")
    normal_range_min: float = Field(..., description="Minimum normal value")
    normal_range_max: float = Field(..., description="Maximum normal value")
    threshold_warning: float = Field(..., description="Warning threshold")
    threshold_critical: float = Field(..., description="Critical threshold")
    time_window: str = Field("5m", description="Time window")


# ============================================
# LOG WATCHING ENDPOINTS
# ============================================

@router.post("/test-connection")
async def test_aws_connection(request: TestConnectionRequest) -> Dict[str, Any]:
    """Test AWS credentials by attempting to connect to CloudWatch Logs."""
    import boto3
    import os
    from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError
    from botocore.config import Config
    
    credentials = request.credentials.model_dump() if request.credentials else None
    
    try:
        session_kwargs = {}
        if credentials:
            if credentials.get('aws_profile'):
                session_kwargs['profile_name'] = credentials['aws_profile']
            elif credentials.get('aws_access_key_id') and credentials.get('aws_secret_access_key'):
                session_kwargs['aws_access_key_id'] = credentials['aws_access_key_id']
                session_kwargs['aws_secret_access_key'] = credentials['aws_secret_access_key']
        
        session = boto3.Session(**session_kwargs)
        
        config = Config()
        if os.environ.get('AWS_SSL_VERIFY', 'true').lower() == 'false':
            config = Config(
                connect_timeout=10,
                read_timeout=10,
                retries={'max_attempts': 2}
            )
        
        client = session.client(
            'logs',
            region_name=request.region,
            config=config,
            verify=(os.environ.get('AWS_CA_BUNDLE') if os.environ.get('AWS_CA_BUNDLE') else 
                    False if os.environ.get('AWS_SSL_VERIFY', 'true').lower() == 'false' else None)
        )
        
        async def _call_describe_log_groups():
            return client.describe_log_groups(limit=1)
        
        await with_retry(_call_describe_log_groups, max_retries=3)
        
        return {
            "success": True,
            "message": "Successfully connected to CloudWatch Logs",
            "region": request.region
        }
    except NoCredentialsError:
        return {
            "success": False,
            "message": "No AWS credentials found. Please provide valid credentials.",
            "region": request.region
        }
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        
        if error_code == 'InvalidClientTokenId':
            return {
                "success": False,
                "message": "Invalid AWS Access Key ID or Secret Access Key",
                "region": request.region
            }
        elif error_code == 'UnrecognizedClientException':
            return {
                "success": False,
                "message": "AWS credentials are not valid or have expired",
                "region": request.region
            }
        elif error_code == 'AccessDenied':
            return {
                "success": False,
                "message": "Access denied. Check IAM permissions for CloudWatch Logs",
                "region": request.region
            }
        else:
            return {
                "success": False,
                "message": f"AWS Error: {error_msg}",
                "region": request.region
            }
    except BotoCoreError as e:
        return {
            "success": False,
            "message": f"Connection error: {str(e)}",
            "region": request.region
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Unexpected error: {str(e)}",
            "region": request.region
        }


@router.post("/watch")
async def watch_logs(request: WatchLogGroupsRequest) -> Dict[str, Any]:
    """Watch multiple CloudWatch log groups and fetch recent logs.
    
    This endpoint retrieves logs from multiple log groups simultaneously,
    enabling cross-service log analysis.
    """
    credentials, region = await _resolve_aws_credentials(request.credentials, request.region)
    return await with_retry(
        watch_log_groups,
        log_group_names=request.log_group_names,
        time_range_minutes=request.time_range_minutes,
        filter_pattern=request.filter_pattern,
        region=region,
        credentials=credentials,
        max_retries=2,
    )


@router.post("/analyze-patterns")
async def analyze_patterns(request: AnalyzePatternsRequest) -> Dict[str, Any]:
    """Analyze log patterns across multiple log groups.
    
    This endpoint identifies common patterns, error frequencies, and trends
    across the specified log groups.
    """
    credentials, region = await _resolve_aws_credentials(request.credentials, request.region)
    return await with_retry(
        analyze_log_patterns,
        log_group_names=request.log_group_names,
        time_range_minutes=request.time_range_minutes,
        pattern_types=request.pattern_types,
        region=region,
        credentials=credentials,
        max_retries=2,
    )


@router.post("/detect-anomalies")
async def detect_log_anomalies(request: DetectAnomaliesRequest) -> Dict[str, Any]:
    """Detect anomalies in log patterns compared to baseline.
    
    This endpoint compares current log patterns against a historical baseline
    to identify unusual activity, error spikes, or pattern deviations.
    """
    credentials, region = await _resolve_aws_credentials(request.credentials, request.region)
    return await with_retry(
        detect_anomalies,
        log_group_names=request.log_group_names,
        time_range_minutes=request.time_range_minutes,
        baseline_minutes=request.baseline_minutes,
        sensitivity=request.sensitivity,
        region=region,
        credentials=credentials,
        max_retries=2,
    )


@router.post("/correlate")
async def correlate_log_events(request: CorrelateLogsRequest) -> Dict[str, Any]:
    """Correlate logs across multiple services using trace ID or correlation ID.
    
    This endpoint traces requests across multiple log groups to identify
    the full request flow and pinpoint issues.
    """
    credentials, region = await _resolve_aws_credentials(request.credentials, request.region)
    return await with_retry(
        correlate_logs,
        log_group_names=request.log_group_names,
        correlation_id=request.correlation_id,
        time_range_minutes=request.time_range_minutes,
        trace_id=request.trace_id,
        region=region,
        credentials=credentials,
        max_retries=2,
    )


# ============================================
# ALERT MANAGEMENT ENDPOINTS
# ============================================

@router.post("/alerts")
async def create_new_alert(request: CreateAlertRequest) -> Dict[str, Any]:
    """Create a new alert from log analysis."""
    return await create_alert(
        log_group=request.log_group,
        alert_type=request.alert_type,
        severity=request.severity,
        message=request.message,
        details=request.details
    )


@router.get("/alerts")
async def list_alerts(
    status: Optional[str] = Query(None, description="Filter by status"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    log_group: Optional[str] = Query(None, description="Filter by log group"),
    limit: int = Query(50, description="Maximum results")
) -> Dict[str, Any]:
    """Get alerts with optional filtering."""
    return await get_alerts(
        status=status,
        severity=severity,
        log_group=log_group,
        limit=limit
    )


@router.post("/alerts/{alert_id}/acknowledge")
async def ack_alert(alert_id: int, request: AcknowledgeAlertRequest) -> Dict[str, Any]:
    """Acknowledge an alert."""
    return await acknowledge_alert(
        alert_id=alert_id,
        acknowledged_by=request.acknowledged_by,
        notes=request.notes
    )


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert_endpoint(alert_id: int, request: ResolveAlertRequest) -> Dict[str, Any]:
    """Resolve an alert."""
    return await resolve_alert(
        alert_id=alert_id,
        resolved_by=request.resolved_by,
        resolution=request.resolution
    )


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert_endpoint(
    alert_id: int,
    dismissed_by: str = Query(..., description="User dismissing the alert"),
    reason: str = Query(..., description="Reason for dismissal")
) -> Dict[str, Any]:
    """Dismiss an alert as false positive."""
    return await dismiss_alert(
        alert_id=alert_id,
        dismissed_by=dismissed_by,
        reason=reason
    )


@router.get("/alerts/summary")
async def alert_summary(
    time_range_hours: int = Query(24, description="Time range in hours")
) -> Dict[str, Any]:
    """Get alert summary for dashboard."""
    return await get_alert_summary(time_range_hours=time_range_hours)


# ============================================
# KNOWLEDGE BASE ENDPOINTS
# ============================================

@router.get("/known-issues")
async def list_known_issues(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, description="Maximum results")
) -> Dict[str, Any]:
    """Get known issues from knowledge base."""
    return await get_known_issues(category=category, limit=limit)


@router.post("/known-issues")
async def add_known_issue(request: CreateKnownIssueRequest) -> Dict[str, Any]:
    """Add a known issue to knowledge base."""
    return await create_known_issue(
        title=request.title,
        description=request.description,
        symptoms=request.symptoms,
        solution=request.solution,
        category=request.category
    )


@router.get("/patterns")
async def list_patterns(
    pattern_type: Optional[str] = Query(None, description="Filter by type"),
    limit: int = Query(100, description="Maximum results")
) -> List[Dict[str, Any]]:
    """List log patterns from knowledge base."""
    return await knowledge_base.list_log_patterns(
        pattern_type=pattern_type,
        limit=limit
    )


@router.post("/patterns")
async def add_pattern(request: AddPatternRequest) -> Dict[str, Any]:
    """Add a log pattern to knowledge base."""
    return await knowledge_base.add_log_pattern(
        name=request.name,
        pattern=request.pattern,
        pattern_type=request.pattern_type,
        severity=request.severity,
        description=request.description
    )


@router.get("/patterns/search")
async def search_patterns(
    query: str = Query(..., description="Search query"),
    limit: int = Query(10, description="Maximum results"),
    threshold: float = Query(0.7, description="Similarity threshold")
) -> List[Dict[str, Any]]:
    """Search for similar log patterns using vector similarity."""
    return await knowledge_base.search_similar_patterns(
        query=query,
        limit=limit,
        threshold=threshold
    )


@router.get("/baselines")
async def list_baselines(
    log_group: Optional[str] = Query(None, description="Filter by log group")
) -> List[Dict[str, Any]]:
    """List baseline metrics."""
    return await knowledge_base.get_baseline_metrics(log_group=log_group)


@router.post("/baselines")
async def set_baseline(request: SetBaselineRequest) -> Dict[str, Any]:
    """Set or update a baseline metric."""
    return await knowledge_base.set_baseline_metric(
        metric_name=request.metric_name,
        log_group=request.log_group,
        normal_range_min=request.normal_range_min,
        normal_range_max=request.normal_range_max,
        threshold_warning=request.threshold_warning,
        threshold_critical=request.threshold_critical,
        time_window=request.time_window
    )


@router.get("/analysis-history")
async def list_analysis_history(
    log_group: Optional[str] = Query(None, description="Filter by log group"),
    analysis_type: Optional[str] = Query(None, description="Filter by analysis type"),
    limit: int = Query(50, description="Maximum results")
) -> List[Dict[str, Any]]:
    """Get analysis history."""
    return await knowledge_base.get_analysis_history(
        log_group=log_group,
        analysis_type=analysis_type,
        limit=limit
    )