"""MCP Tools for CloudWatch Log watching and analysis.

This module provides tools for watching multiple CloudWatch log groups,
analyzing patterns, detecting anomalies, and correlating logs across services.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Callable
from functools import wraps

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def handle_exceptions(func: Callable) -> Callable:
    """Decorator to handle exceptions in MCP tools.
    
    Args:
        func: Function to wrap
        
    Returns:
        Wrapped function
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"AWS ClientError [{error_code}]: {error_msg}")
            return {
                "error": True,
                "error_code": error_code,
                "message": error_msg
            }
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            return {
                "error": True,
                "message": str(e)
            }
    return wrapper


class CloudWatchLogWatcher:
    """CloudWatch Logs watcher for multiple log groups."""
    
    def __init__(self, region: str = "us-east-1"):
        """Initialize CloudWatch Logs client.
        
        Args:
            region: AWS region
        """
        self.region = region
        self.client = boto3.client('logs', region_name=region)
        self._watch_tasks: Dict[str, asyncio.Task] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
    
    async def fetch_logs(
        self,
        log_group_name: str,
        start_time: datetime,
        end_time: datetime,
        filter_pattern: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Fetch logs from a CloudWatch log group.
        
        Args:
            log_group_name: Name of the log group
            start_time: Start time for log query
            end_time: End time for log query
            filter_pattern: CloudWatch Logs filter pattern
            limit: Maximum number of log events to return
            
        Returns:
            List of log events
        """
        logs = []
        next_token = None
        
        # Convert to milliseconds timestamp
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        
        while True:
            params = {
                'logGroupName': log_group_name,
                'startTime': start_ms,
                'endTime': end_ms,
                'limit': limit
            }
            
            if filter_pattern:
                params['filterPattern'] = filter_pattern
            if next_token:
                params['nextToken'] = next_token
            
            # Run sync operation in executor
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.filter_log_events(**params)
            )
            
            events = response.get('events', [])
            logs.extend(events)
            
            next_token = response.get('nextToken')
            if not next_token or len(logs) >= limit:
                break
        
        return logs[:limit]
    
    async def query_with_insights(
        self,
        log_group_names: List[str],
        query_string: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """Run CloudWatch Logs Insights query.
        
        Args:
            log_group_names: List of log group names
            query_string: Insights query string
            start_time: Start time for query
            end_time: End time for query
            limit: Maximum results
            
        Returns:
            Query results
        """
        # Start query
        start_query_response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.client.start_query(
                logGroupNames=log_group_names,
                startTime=int(start_time.timestamp()),
                endTime=int(end_time.timestamp()),
                queryString=query_string,
                limit=limit
            )
        )
        
        query_id = start_query_response['queryId']
        
        # Poll for results
        while True:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.get_query_results(queryId=query_id)
            )
            
            status = response['status']
            
            if status == 'Complete':
                return {
                    'query_id': query_id,
                    'status': status,
                    'results': response.get('results', []),
                    'statistics': response.get('statistics', {})
                }
            elif status in ['Failed', 'Cancelled']:
                return {
                    'query_id': query_id,
                    'status': status,
                    'error': response.get('statistics', {}).get('statusMessage', 'Query failed')
                }
            
            # Wait before polling again
            await asyncio.sleep(1)


# Global watcher instance
_watcher: Optional[CloudWatchLogWatcher] = None


def get_watcher(region: str = "us-east-1") -> CloudWatchLogWatcher:
    """Get or create CloudWatch Log watcher instance.
    
    Args:
        region: AWS region
        
    Returns:
        CloudWatchLogWatcher instance
    """
    global _watcher
    if _watcher is None:
        _watcher = CloudWatchLogWatcher(region=region)
    return _watcher


# ============================================
# MCP TOOL FUNCTIONS
# ============================================

@handle_exceptions
async def watch_log_groups(
    log_group_names: List[str],
    time_range_minutes: int = 60,
    filter_pattern: Optional[str] = None,
    region: str = "us-east-1"
) -> Dict[str, Any]:
    """Watch multiple CloudWatch log groups and fetch recent logs.
    
    This tool retrieves logs from multiple log groups simultaneously,
    enabling cross-service log analysis.
    
    Args:
        log_group_names: List of CloudWatch log group names to watch
        time_range_minutes: Time range in minutes to look back (default: 60)
        filter_pattern: Optional CloudWatch Logs filter pattern
        region: AWS region (default: us-east-1)
        
    Returns:
        Dictionary containing logs from each log group and summary statistics
        
    Example:
        ```python
        result = await watch_log_groups(
            log_group_names=[
                "/aws/lambda/my-function",
                "/aws/apigateway/my-api"
            ],
            time_range_minutes=30,
            filter_pattern="[timestamp, message, level=ERROR*]"
        )
        ```
    """
    watcher = get_watcher(region)
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)
    
    results = {}
    total_events = 0
    errors = []
    
    # Fetch logs from all log groups concurrently
    tasks = []
    for log_group in log_group_names:
        task = watcher.fetch_logs(
            log_group_name=log_group,
            start_time=start_time,
            end_time=end_time,
            filter_pattern=filter_pattern
        )
        tasks.append((log_group, task))
    
    for log_group, task in tasks:
        try:
            logs = await task
            results[log_group] = {
                "event_count": len(logs),
                "events": logs[:100],  # Limit to 100 events per group
                "time_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat()
                }
            }
            total_events += len(logs)
        except Exception as e:
            errors.append({
                "log_group": log_group,
                "error": str(e)
            })
            results[log_group] = {
                "event_count": 0,
                "events": [],
                "error": str(e)
            }
    
    return {
        "success": True,
        "log_groups": results,
        "summary": {
            "total_log_groups": len(log_group_names),
            "successful_log_groups": len(log_group_names) - len(errors),
            "total_events": total_events,
            "time_range_minutes": time_range_minutes
        },
        "errors": errors if errors else None
    }


@handle_exceptions
async def analyze_log_patterns(
    log_group_names: List[str],
    time_range_minutes: int = 60,
    pattern_types: Optional[List[str]] = None,
    region: str = "us-east-1"
) -> Dict[str, Any]:
    """Analyze log patterns across multiple log groups.
    
    This tool identifies common patterns, error frequencies, and trends
    across the specified log groups.
    
    Args:
        log_group_names: List of CloudWatch log group names
        time_range_minutes: Time range in minutes to analyze
        pattern_types: Types of patterns to look for (error, warning, info, custom)
        region: AWS region
        
    Returns:
        Pattern analysis results including frequencies and trends
        
    Example:
        ```python
        result = await analyze_log_patterns(
            log_group_names=["/aws/lambda/my-function"],
            pattern_types=["error", "warning"]
        )
        ```
    """
    watcher = get_watcher(region)
    
    if pattern_types is None:
        pattern_types = ["error", "warning", "info"]
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)
    
    # Build Insights query for pattern analysis
    pattern_filters = {
        "error": "level = ERROR OR message like /ERROR|Error|error|Exception|exception/",
        "warning": "level = WARN OR message like /WARN|Warning|warning/",
        "info": "level = INFO OR message like /INFO|Info|info/"
    }
    
    results = {}
    
    for pattern_type in pattern_types:
        filter_clause = pattern_filters.get(pattern_type, "")
        query_string = f"""
        fields @timestamp, @message, @logStream
        | filter {filter_clause}
        | stats count() by bin(5m)
        | sort @timestamp desc
        """
        
        try:
            query_result = await watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=query_string,
                start_time=start_time,
                end_time=end_time
            )
            
            results[pattern_type] = {
                "status": query_result.get("status"),
                "data": query_result.get("results", [])[:20],
                "statistics": query_result.get("statistics")
            }
        except Exception as e:
            results[pattern_type] = {
                "status": "error",
                "error": str(e)
            }
    
    # Get overall statistics
    stats_query = """
    fields @timestamp, @message
    | stats count() as total_events,
            min(@timestamp) as first_event,
            max(@timestamp) as last_event
    """
    
    try:
        stats_result = await watcher.query_with_insights(
            log_group_names=log_group_names,
            query_string=stats_query,
            start_time=start_time,
            end_time=end_time
        )
    except Exception as e:
        stats_result = {"error": str(e)}
    
    return {
        "success": True,
        "patterns": results,
        "statistics": stats_result,
        "time_range": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "minutes": time_range_minutes
        },
        "log_groups_analyzed": log_group_names
    }


@handle_exceptions
async def detect_anomalies(
    log_group_names: List[str],
    time_range_minutes: int = 60,
    baseline_minutes: int = 1440,  # 24 hours
    sensitivity: str = "medium",
    region: str = "us-east-1"
) -> Dict[str, Any]:
    """Detect anomalies in log patterns compared to baseline.
    
    This tool compares current log patterns against a historical baseline
    to identify unusual activity, error spikes, or pattern deviations.
    
    Args:
        log_group_names: List of CloudWatch log group names
        time_range_minutes: Current time range to analyze
        baseline_minutes: Baseline time range for comparison (default: 24 hours)
        sensitivity: Anomaly detection sensitivity (low, medium, high)
        region: AWS region
        
    Returns:
        Detected anomalies with severity scores and details
        
    Example:
        ```python
        result = await detect_anomalies(
            log_group_names=["/aws/lambda/my-function"],
            sensitivity="high"
        )
        ```
    """
    watcher = get_watcher(region)
    
    end_time = datetime.now(timezone.utc)
    current_start = end_time - timedelta(minutes=time_range_minutes)
    baseline_start = end_time - timedelta(minutes=baseline_minutes + time_range_minutes)
    baseline_end = end_time - timedelta(minutes=baseline_minutes)
    
    # Sensitivity thresholds
    thresholds = {
        "low": 3.0,      # 3x deviation
        "medium": 2.0,   # 2x deviation
        "high": 1.5      # 1.5x deviation
    }
    threshold = thresholds.get(sensitivity, 2.0)
    
    # Query for error counts in current period
    current_query = """
    fields @timestamp, @message
    | filter level = ERROR OR message like /ERROR|Error|Exception/
    | stats count() as error_count by bin(5m)
    | sort @timestamp desc
    """
    
    # Query for baseline error counts
    baseline_query = """
    fields @timestamp, @message
    | filter level = ERROR OR message like /ERROR|Error|Exception/
    | stats count() as error_count by bin(5m)
    | sort @timestamp desc
    """
    
    anomalies = []
    
    try:
        # Get current period data
        current_result = await watcher.query_with_insights(
            log_group_names=log_group_names,
            query_string=current_query,
            start_time=current_start,
            end_time=end_time
        )
        
        # Get baseline data
        baseline_result = await watcher.query_with_insights(
            log_group_names=log_group_names,
            query_string=baseline_query,
            start_time=baseline_start,
            end_time=baseline_end
        )
        
        # Calculate baseline average
        baseline_counts = []
        for result in baseline_result.get("results", []):
            for field in result:
                if field.get("field") == "error_count":
                    try:
                        baseline_counts.append(float(field.get("value", 0)))
                    except (ValueError, TypeError):
                        pass
        
        baseline_avg = sum(baseline_counts) / len(baseline_counts) if baseline_counts else 0
        
        # Check current period for anomalies
        for result in current_result.get("results", []):
            timestamp = None
            count = 0
            
            for field in result:
                if field.get("field") == "@timestamp":
                    timestamp = field.get("value")
                elif field.get("field") == "error_count":
                    try:
                        count = float(field.get("value", 0))
                    except (ValueError, TypeError):
                        pass
            
            if baseline_avg > 0 and count > baseline_avg * threshold:
                deviation = count / baseline_avg if baseline_avg > 0 else float('inf')
                anomalies.append({
                    "timestamp": timestamp,
                    "current_count": count,
                    "baseline_average": round(baseline_avg, 2),
                    "deviation_factor": round(deviation, 2),
                    "severity": "high" if deviation > 3 else "medium" if deviation > 2 else "low"
                })
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
    
    return {
        "success": True,
        "anomalies": anomalies,
        "summary": {
            "total_anomalies": len(anomalies),
            "high_severity": len([a for a in anomalies if a["severity"] == "high"]),
            "medium_severity": len([a for a in anomalies if a["severity"] == "medium"]),
            "low_severity": len([a for a in anomalies if a["severity"] == "low"]),
            "baseline_period_minutes": baseline_minutes,
            "current_period_minutes": time_range_minutes,
            "sensitivity": sensitivity
        },
        "log_groups_analyzed": log_group_names
    }


@handle_exceptions
async def correlate_logs(
    log_group_names: List[str],
    correlation_id: Optional[str] = None,
    time_range_minutes: int = 60,
    trace_id: Optional[str] = None,
    region: str = "us-east-1"
) -> Dict[str, Any]:
    """Correlate logs across multiple services using trace ID or correlation ID.
    
    This tool traces requests across multiple log groups to identify
    the full request flow and pinpoint issues.
    
    Args:
        log_group_names: List of CloudWatch log group names
        correlation_id: Correlation ID to search for
        time_range_minutes: Time range in minutes to search
        trace_id: AWS X-Ray trace ID to correlate
        region: AWS region
        
    Returns:
        Correlated log events across services with timeline
        
    Example:
        ```python
        result = await correlate_logs(
            log_group_names=["/aws/lambda/api", "/aws/lambda/processor"],
            trace_id="1-5f0c7e8d-abcdef1234567890"
        )
        ```
    """
    watcher = get_watcher(region)
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)
    
    # Build filter pattern
    filter_parts = []
    if correlation_id:
        filter_parts.append(f'message like "{correlation_id}"')
    if trace_id:
        filter_parts.append(f'message like "{trace_id}"')
    
    filter_pattern = " OR ".join(filter_parts) if filter_parts else None
    
    # Fetch logs from all groups
    all_events = []
    
    for log_group in log_group_names:
        try:
            logs = await watcher.fetch_logs(
                log_group_name=log_group,
                start_time=start_time,
                end_time=end_time,
                filter_pattern=filter_pattern
            )
            
            for event in logs:
                event["log_group"] = log_group
                all_events.append(event)
        except Exception as e:
            logger.warning(f"Error fetching logs from {log_group}: {e}")
    
    # Sort by timestamp
    all_events.sort(key=lambda x: x.get("timestamp", 0))
    
    # Build timeline
    timeline = []
    for event in all_events:
        timestamp_ms = event.get("timestamp", 0)
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        
        timeline.append({
            "timestamp": timestamp.isoformat(),
            "timestamp_ms": timestamp_ms,
            "log_group": event.get("log_group"),
            "log_stream": event.get("logStreamName"),
            "message": event.get("message", "")[:500],  # Truncate long messages
            "event_id": event.get("eventId")
        })
    
    # Identify service flow
    service_flow = []
    seen_groups = set()
    for event in timeline:
        group = event["log_group"]
        if group not in seen_groups:
            service_flow.append(group)
            seen_groups.add(group)
    
    return {
        "success": True,
        "timeline": timeline,
        "summary": {
            "total_events": len(timeline),
            "services_involved": list(seen_groups),
            "service_flow": service_flow,
            "time_range_minutes": time_range_minutes
        },
        "correlation_id": correlation_id,
        "trace_id": trace_id
    }
