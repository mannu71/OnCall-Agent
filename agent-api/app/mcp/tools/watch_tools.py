"""MCP Tools for CloudWatch Log watching and analysis.

This module provides tools for watching multiple CloudWatch log groups,
analyzing patterns, detecting anomalies, and correlating logs across services.
"""
import asyncio
import logging
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Callable
from functools import wraps

import ssl

from app.config import settings
from app.core.concurrency.thread_pools import run_in_aws_pool

# Disable SSL certificate verification when AWS_SSL_VERIFY=false.
if not settings.aws_ssl_verify:
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

import boto3
from botocore.config import Config as BotocoreConfig
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
            if error_code == "ExpiredTokenException" or "ExpiredToken" in str(e):
                return {
                    "error": True,
                    "error_code": error_code,
                    "message": (
                        "[EXPIRED_CREDENTIALS] AWS session token has expired. "
                        "No further CloudWatch tool calls will succeed until credentials are refreshed. "
                        "Stop all tool use immediately and report this finding: "
                        "AWS credentials must be renewed in the LLM configuration settings before retrying."
                    ),
                }
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


def compute_time_buckets(
    start_time: datetime, end_time: datetime, bucket_minutes: int,
) -> List[tuple]:
    """Split [start_time, end_time] into newest-first buckets of ≤ bucket_minutes.

    Returns a list of ``(bucket_start, bucket_end)`` tuples ordered most-recent
    first (so an early-stopping consumer sees the freshest events first). The
    last (oldest) bucket may be shorter than *bucket_minutes*. Pure and
    deterministic — the unit test for the Phase 4 bucketing math.
    """
    if bucket_minutes <= 0 or end_time <= start_time:
        return [(start_time, end_time)]
    delta = timedelta(minutes=bucket_minutes)
    buckets: List[tuple] = []
    cur_end = end_time
    while cur_end > start_time:
        cur_start = max(start_time, cur_end - delta)
        buckets.append((cur_start, cur_end))
        cur_end = cur_start
    return buckets


class CloudWatchLogWatcher:
    """CloudWatch Logs watcher for multiple log groups."""
    
    def __init__(
        self,
        region: str = "us-east-1",
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        aws_profile: Optional[str] = None,
        use_env_credentials: bool = False
    ):
        """Initialize CloudWatch Logs client.
        
        Args:
            region: AWS region
            aws_access_key_id: AWS Access Key ID (optional)
            aws_secret_access_key: AWS Secret Access Key (optional)
            aws_session_token: AWS Session Token for temporary credentials (optional)
            aws_profile: AWS profile name from ~/.aws/credentials (optional)
            use_env_credentials: Use environment variables for credentials
        """
        self.region = region
        self._watch_tasks: Dict[str, asyncio.Task] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        
        if use_env_credentials:
            session = boto3.Session(region_name=region)
        elif aws_access_key_id and aws_secret_access_key:
            session = boto3.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name=region
            )
        elif aws_profile:
            session = boto3.Session(profile_name=aws_profile, region_name=region)
        else:
            session = boto3.Session(region_name=region)
        
        ssl_verify = settings.aws_ssl_verify
        client_kwargs: Dict[str, Any] = {"region_name": region}
        if not ssl_verify:
            client_kwargs["verify"] = False
            client_kwargs["config"] = BotocoreConfig(retries={"max_attempts": 3})

        self.client = session.client("logs", **client_kwargs)
    
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
            response = await run_in_aws_pool(
                lambda: self.client.filter_log_events(**params)
            )
            
            events = response.get('events', [])
            logs.extend(events)
            
            next_token = response.get('nextToken')
            if not next_token or len(logs) >= limit:
                break
        
        return logs[:limit]
    
    async def query_with_insights_bucketed(
        self,
        log_group_names: List[str],
        query_string: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
        bucket_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run an Insights query over a long range as sequential time buckets.

        For ranges beyond ``bucket_minutes`` (default ``cloudwatch_bucket_minutes``)
        the window is split into newest-first buckets, each ≤ bucket size, and
        queried in turn (reusing :meth:`query_with_insights` per bucket, so each
        keeps the retry/sampling/rate-limit machinery). Results are concatenated
        newest-first and the scan stops early once *limit* rows are collected —
        recent events are usually the relevant ones. Scan statistics are summed
        across buckets so Phase 3 cost accounting still sees the whole range.
        """
        bm = bucket_minutes or settings.cloudwatch_bucket_minutes
        buckets = compute_time_buckets(start_time, end_time, bm)
        merged: List[Dict[str, Any]] = []
        stats = {"bytesScanned": 0.0, "recordsScanned": 0.0, "recordsMatched": 0.0}
        status = "Complete"
        partial = False
        for bstart, bend in buckets:
            res = await self.query_with_insights(
                log_group_names=log_group_names,
                query_string=query_string,
                start_time=bstart,
                end_time=bend,
                limit=limit,
            )
            merged.extend(res.get("results", []) or [])
            st = res.get("statistics") or {}
            stats["bytesScanned"] += float(st.get("bytesScanned", 0) or 0)
            stats["recordsScanned"] += float(st.get("recordsScanned", 0) or 0)
            stats["recordsMatched"] += float(st.get("recordsMatched", 0) or 0)
            if res.get("partial"):
                partial = True
            if res.get("status") and res["status"] != "Complete":
                status = res["status"]
            if len(merged) >= limit:
                merged = merged[:limit]
                break
        out: Dict[str, Any] = {
            "status": status,
            "results": merged,
            "statistics": stats,
            "buckets": len(buckets),
        }
        if partial:
            out["partial"] = True
        return out

    async def query_with_insights(
        self,
        log_group_names: List[str],
        query_string: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
        max_attempts: int = 3,
        timeout_seconds: float = 90.0,
    ) -> Dict[str, Any]:
        """Run CloudWatch Logs Insights query with retry + sampling fallback.

        Resilience strategy (Phase 4):
          - Up to ``max_attempts`` attempts with exponential backoff (2s, 8s, 30s).
          - Throttling / transient ClientErrors are retried automatically.
          - If every attempt times out, fall back to a *sampled* query: shrink
            the window to the last 25% of the original range and append a
            ``| limit 1000`` clause. The response is flagged ``partial=True``
            with the sampling_ratio so downstream consumers (and the agent)
            know the result is degraded but non-empty.

        Args:
            log_group_names: List of log group names
            query_string: Insights query string
            start_time: Start time for query
            end_time: End time for query
            limit: Maximum results
            max_attempts: Number of full-window attempts before sampling.
            timeout_seconds: Per-attempt timeout.

        Returns:
            Query results dict. May include ``partial=True`` and ``sampling_ratio``.
        """
        # Long-range bucketing (Phase 4): when the window exceeds the bucket size
        # (default 24h), split into sequential buckets so each Insights query
        # stays within CloudWatch's practical per-query range. Callers passing a
        # ≤24h window (the historical cap) are unaffected — no window exceeds the
        # 1440-min default bucket, so this never fires for them.
        _bm = settings.cloudwatch_bucket_minutes
        if _bm > 0 and (end_time - start_time).total_seconds() / 60.0 > _bm:
            return await self.query_with_insights_bucketed(
                log_group_names=log_group_names,
                query_string=query_string,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                bucket_minutes=_bm,
            )

        backoffs = [2.0, 8.0, 30.0]
        last_error: Optional[str] = None
        last_query_id: Optional[str] = None

        async def _execute_once(qs: str, qstart: datetime, qend: datetime, qlimit: int) -> Dict[str, Any]:
            """Run a single Insights query and poll to completion or timeout."""
            # Smooth StartQuery bursts (Phase 3) so a wide fan-out doesn't trip
            # CloudWatch's per-account request-rate limit and throttle the run.
            from app.core.aws import cloudwatch_ratelimit
            await cloudwatch_ratelimit.acquire(
                self.region, "StartQuery", settings.cloudwatch_ratelimit_startquery_rps,
            )
            start_resp = await run_in_aws_pool(
                lambda: self.client.start_query(
                    logGroupNames=log_group_names,
                    startTime=int(qstart.timestamp()),
                    endTime=int(qend.timestamp()),
                    queryString=qs,
                    limit=qlimit,
                ),
            )
            qid = start_resp['queryId']

            async def _poll() -> Dict[str, Any]:
                while True:
                    response = await run_in_aws_pool(
                        lambda: self.client.get_query_results(queryId=qid),
                    )
                    status = response['status']
                    if status == 'Complete':
                        return {
                            'query_id': qid,
                            'status': status,
                            'results': response.get('results', []),
                            'statistics': response.get('statistics', {}),
                        }
                    if status in ('Failed', 'Cancelled'):
                        return {
                            'query_id': qid,
                            'status': status,
                            'error': response.get('statistics', {}).get('statusMessage', 'Query failed'),
                        }
                    await asyncio.sleep(1)

            try:
                return await asyncio.wait_for(_poll(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                # Cancel the Insights query to avoid ongoing AWS charges.
                try:
                    await run_in_aws_pool(
                        lambda: self.client.stop_query(queryId=qid),
                    )
                except Exception as stop_err:  # pragma: no cover - best-effort
                    logger.warning("query_with_insights: stop_query failed for %s: %s", qid, stop_err)
                return {
                    'query_id': qid,
                    'status': 'Timeout',
                    'error': f'Insights query exceeded {timeout_seconds:.0f}s and was stopped',
                }

        # Retry full-window attempts with exponential backoff.
        for attempt in range(max_attempts):
            try:
                result = await _execute_once(query_string, start_time, end_time, limit)
                last_query_id = result.get('query_id')
                if result.get('status') == 'Complete':
                    return result
                last_error = result.get('error')
                # Throttling / transient failures fall through to backoff+retry.
                if result.get('status') == 'Failed' and last_error and 'throttl' not in last_error.lower():
                    # Non-retriable failure — return immediately.
                    return result
            except ClientError as e:
                code = e.response.get('Error', {}).get('Code', '')
                last_error = f"{code}: {e.response.get('Error', {}).get('Message', str(e))}"
                if code not in ('ThrottlingException', 'LimitExceededException', 'ServiceUnavailable'):
                    raise

            if attempt < max_attempts - 1:
                # Jitter (±25%) so parallel queries that throttle together don't
                # retry in lockstep and re-collide on the same throttle window.
                _base = backoffs[min(attempt, len(backoffs) - 1)]
                await asyncio.sleep(_base * random.uniform(0.75, 1.25))

        # Sampling fallback: shrink window to last 25% and cap to 1000 rows.
        window = end_time - start_time
        sampled_start = end_time - (window / 4) if window.total_seconds() > 0 else start_time
        sampled_query = query_string.rstrip()
        if '| limit' not in sampled_query.lower():
            sampled_query = sampled_query + "\n| limit 1000"
        logger.warning(
            "query_with_insights: full-window attempts exhausted, falling back to "
            "sampled query (25%% window, limit 1000). last_error=%s", last_error,
        )
        try:
            sampled = await _execute_once(sampled_query, sampled_start, end_time, min(limit, 1000))
        except ClientError as e:
            return {
                'query_id': last_query_id,
                'status': 'Failed',
                'error': f'Sampling fallback failed: {e}',
                'partial': True,
                'sampling_ratio': 0.25,
            }
        sampled['partial'] = True
        sampled['sampling_ratio'] = 0.25
        sampled['fallback_reason'] = last_error or 'timeout'
        return sampled


# ---------------------------------------------------------------------------
# Severity taxonomy (Phase 1)
# ---------------------------------------------------------------------------
# Insights filter clauses keyed by canonical severity bucket. Each clause uses
# CloudWatch Insights `like` regex syntax and covers:
#   - plain text words (ERROR, Warning, etc.)
#   - structured JSON ("level":"error", "severity":"critical")
#   - syslog priority prefixes (<0> .. <7>)
#   - Java/Python/UNIX vocabulary (SEVERE, EMERG, ALERT, FATAL, CRITICAL)
SEVERITY_PATTERNS: Dict[str, str] = {
    "critical": (
        'level in ["CRITICAL", "FATAL", "EMERG", "ALERT", "SEVERE"] '
        'OR @message like /(?i)\\b(critical|fatal|emerg(ency)?|alert|severe|panic)\\b/ '
        'OR @message like /"(level|severity)"\\s*:\\s*"(?i)(critical|fatal|emergency|alert|severe)"/ '
        'OR @message like /^<[0-2]>/'
    ),
    "error": (
        'level in ["ERROR", "ERR"] '
        'OR @message like /(?i)\\b(error|exception|err|failure|failed|traceback)\\b/ '
        'OR @message like /"(level|severity)"\\s*:\\s*"(?i)(error|err)"/ '
        'OR @message like /^<3>/'
    ),
    "warning": (
        'level in ["WARN", "WARNING"] '
        'OR @message like /(?i)\\b(warn|warning|deprecated)\\b/ '
        'OR @message like /"(level|severity)"\\s*:\\s*"(?i)(warn|warning)"/ '
        'OR @message like /^<4>/'
    ),
    "info": (
        'level in ["INFO", "NOTICE"] '
        'OR @message like /(?i)\\b(info|notice)\\b/ '
        'OR @message like /"(level|severity)"\\s*:\\s*"(?i)(info|notice)"/ '
        'OR @message like /^<[5-6]>/'
    ),
    "debug": (
        'level in ["DEBUG", "TRACE"] '
        'OR @message like /(?i)\\b(debug|trace)\\b/ '
        'OR @message like /"(level|severity)"\\s*:\\s*"(?i)(debug|trace)"/ '
        'OR @message like /^<7>/'
    ),
}


# ---------------------------------------------------------------------------
# Typed normalization tokens (Phase 2)
# ---------------------------------------------------------------------------
_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9:.Z+\-]+'
)
_IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b')
_HEX_RE = re.compile(r'0x[0-9a-fA-F]+')
# Duration: number immediately followed by ms|s|m|h (no word boundary needed
# before unit because digit-letter is a natural boundary).
_DURATION_RE = re.compile(r'\b(\d+(?:\.\d+)?)(ms|us|ns|s|m|h)\b', re.IGNORECASE)
# HTTP status: 3-digit number that looks like an HTTP code, typically preceded
# by "status", "code", "HTTP", or appearing standalone in a range we care about.
_HTTP_RE = re.compile(
    r'\b(?:status[_\s-]?code\s*[:=]?\s*|status\s*[:=]?\s*|HTTP[/ ]?[0-9.]*\s+)?'
    r'([1-5]\d{2})\b'
)
_NUM_RE = re.compile(r'\b\d+(?:\.\d+)?\b')


def _bucket_duration(value: float, unit: str) -> str:
    """Return a magnitude bucket for a duration in any unit.

    Buckets are chosen to preserve operationally meaningful gradation:
      - short:  < 100ms  (fast paths)
      - med:    100ms..10s (typical request latencies)
      - long:   10s..1min (slow requests / soft timeouts)
      - xlong:  >= 1min   (hung or hard-timeout territory)
    """
    unit = unit.lower()
    multipliers = {"ns": 1e-6, "us": 1e-3, "ms": 1.0, "s": 1000.0, "m": 60_000.0, "h": 3_600_000.0}
    ms = value * multipliers.get(unit, 1.0)
    if ms < 100.0:
        return "<DURATION:short>"
    if ms < 10_000.0:
        return "<DURATION:med>"
    if ms < 60_000.0:
        return "<DURATION:long>"
    return "<DURATION:xlong>"


def _bucket_num(value_str: str) -> str:
    try:
        value = float(value_str)
    except ValueError:
        return "<NUM:med>"
    if value < 10:
        return "<NUM:small>"
    if value < 1000:
        return "<NUM:med>"
    return "<NUM:large>"


def _bucket_http(code_str: str) -> str:
    cls = code_str[0]
    if cls in ("4", "5"):
        return f"<HTTP:{cls}xx>"
    return f"<NUM:med>"


def _parse_insights_row(row: List[Dict[str, Any]]) -> Dict[str, str]:
    """Flatten one CloudWatch Insights result row to a field→value dict."""
    return {
        f.get("field", ""): f.get("value", "")
        for f in row
        if isinstance(f, dict) and f.get("field")
    }


def _insights_quality_meta(*query_results: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a compact data_quality block from one or more Insights responses."""
    partial = False
    min_ratio = 1.0
    for qr in query_results:
        if not isinstance(qr, dict):
            continue
        if qr.get("partial"):
            partial = True
            try:
                min_ratio = min(min_ratio, float(qr.get("sampling_ratio", 0.25)))
            except (TypeError, ValueError):
                min_ratio = 0.25
    if not partial:
        return {}
    return {"partial": True, "sampling_ratio": min_ratio}


def _compute_baseline_profile(counts: List[float]) -> Dict[str, float]:
    """Mean/std/EWMA/trend profile for a series of bucket error counts."""
    if not counts:
        return {
            "baseline_avg": 0.0,
            "baseline_std": 0.0,
            "ewma": 0.0,
            "trend_slope": 0.0,
            "trend_std": 0.0,
        }

    baseline_avg = sum(counts) / len(counts)
    if len(counts) >= 2:
        variance = sum((x - baseline_avg) ** 2 for x in counts) / len(counts)
        baseline_std = variance ** 0.5
    else:
        baseline_std = 0.0

    ewma_alpha = 0.3
    ewma_value = counts[0]
    for v in counts[1:]:
        ewma_value = ewma_alpha * v + (1 - ewma_alpha) * ewma_value

    trend_slope = 0.0
    trend_std = 0.0
    trend_window = counts[-6:] if len(counts) >= 2 else []
    if len(trend_window) >= 2:
        n = len(trend_window)
        mean_x = (n - 1) / 2.0
        mean_y = sum(trend_window) / n
        num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(trend_window))
        den = sum((i - mean_x) ** 2 for i in range(n))
        trend_slope = num / den if den else 0.0
        deltas = [trend_window[i] - trend_window[i - 1] for i in range(1, n)]
        if len(deltas) >= 2:
            d_mean = sum(deltas) / len(deltas)
            trend_std = (sum((d - d_mean) ** 2 for d in deltas) / len(deltas)) ** 0.5

    return {
        "baseline_avg": baseline_avg,
        "baseline_std": baseline_std,
        "ewma": ewma_value,
        "trend_slope": trend_slope,
        "trend_std": trend_std,
    }


def _score_error_bucket(
    count: float,
    profile: Dict[str, float],
    eff_z_threshold: float,
    eff_threshold: float,
) -> Optional[Dict[str, Any]]:
    """Return anomaly scoring fields when *count* exceeds baseline thresholds."""
    baseline_avg = profile["baseline_avg"]
    baseline_std = profile["baseline_std"]
    ewma_value = profile["ewma"]
    trend_slope = profile["trend_slope"]
    trend_std = profile["trend_std"]

    is_anomaly = False
    z_score = 0.0
    deviation = 0.0
    residual_z = 0.0
    slope_z = 0.0
    anomaly_reasons: List[str] = []

    if baseline_std > 0:
        z_score = (count - baseline_avg) / baseline_std
        if z_score > eff_z_threshold:
            is_anomaly = True
            anomaly_reasons.append("zscore")
    elif baseline_avg > 0:
        deviation = count / baseline_avg
        if deviation > eff_threshold:
            is_anomaly = True
            anomaly_reasons.append("deviation")

    if baseline_std > 0 and ewma_value:
        residual = count - ewma_value
        residual_z = residual / baseline_std
        if (residual_z > eff_z_threshold) and (z_score > eff_z_threshold / 2):
            is_anomaly = True
            if "ewma" not in anomaly_reasons:
                anomaly_reasons.append("ewma")

    if trend_std > 0:
        slope_z = trend_slope / trend_std
        if slope_z > 2.0:
            is_anomaly = True
            if "trend" not in anomaly_reasons:
                anomaly_reasons.append("trend")

    if not is_anomaly:
        return None

    strongest = max(abs(z_score), abs(residual_z), abs(deviation), abs(slope_z))
    if strongest >= 4.0:
        severity = "critical"
    elif strongest >= 3.0:
        severity = "high"
    elif strongest >= 2.0:
        severity = "medium"
    else:
        severity = "low"

    return {
        "current_count": count,
        "baseline_average": round(baseline_avg, 2),
        "z_score": round(z_score, 2),
        "severity": severity,
        "reasons": anomaly_reasons,
    }


def _normalize_message(msg: str) -> str:
    """Normalise a log message for semantic pattern grouping.

    Replaces dynamic tokens with *typed* placeholders so that pattern grouping
    preserves severity gradation:
      - UUIDs       -> <UUID>
      - ISO timestamps -> <TIMESTAMP>
      - IPv4[:port] -> <IP>
      - hex literals -> <HEX>
      - durations   -> <DURATION:short|long>
      - HTTP codes  -> <HTTP:4xx|5xx>
      - numbers     -> <NUM:small|med|large>

    Ordering matters: high-specificity patterns run first so a UUID is not
    chewed up by the generic number regex.
    """
    out = _UUID_RE.sub('<UUID>', msg)
    out = _TIMESTAMP_RE.sub('<TIMESTAMP>', out)
    out = _IP_RE.sub('<IP>', out)
    out = _HEX_RE.sub('<HEX>', out)
    out = _DURATION_RE.sub(
        lambda m: _bucket_duration(float(m.group(1)), m.group(2)),
        out,
    )
    out = _HTTP_RE.sub(lambda m: _bucket_http(m.group(1)), out)
    out = _NUM_RE.sub(lambda m: _bucket_num(m.group(0)), out)
    return out[:150]


def get_watcher(
    region: str = "us-east-1",
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
    aws_profile: Optional[str] = None,
    use_env_credentials: bool = False
) -> CloudWatchLogWatcher:
    """Create CloudWatch Log watcher instance with credentials.
    
    Args:
        region: AWS region
        aws_access_key_id: AWS Access Key ID (optional)
        aws_secret_access_key: AWS Secret Access Key (optional)
        aws_session_token: AWS Session Token for temporary credentials (optional)
        aws_profile: AWS profile name from ~/.aws/credentials (optional)
        use_env_credentials: Use environment variables for credentials
        
    Returns:
        CloudWatchLogWatcher instance
    """
    return CloudWatchLogWatcher(
        region=region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_session_token=aws_session_token,
        aws_profile=aws_profile,
        use_env_credentials=use_env_credentials
    )


def _extract_credentials(credentials: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract AWS credentials from credentials dict.
    
    Args:
        credentials: Credentials dictionary or None
        
    Returns:
        Dictionary with credential parameters for get_watcher
    """
    if not credentials:
        return {}
    
    result: Dict[str, Any] = {
        "aws_access_key_id": credentials.get("access_key_id"),
        "aws_secret_access_key": credentials.get("secret_access_key"),
        "aws_profile": credentials.get("aws_profile"),
        "use_env_credentials": credentials.get("use_env_credentials", False),
    }
    # Include session_token only when present so temporary STS credentials work
    session_token = credentials.get("session_token")
    if session_token:
        result["aws_session_token"] = session_token
    return result


# ============================================
# MCP TOOL FUNCTIONS
# ============================================

@handle_exceptions
async def watch_log_groups(
    log_group_names: List[str],
    time_range_minutes: int = 60,
    filter_pattern: Optional[str] = None,
    region: str = "us-east-1",
    regions: Optional[List[str]] = None,
    credentials: Optional[Dict[str, Any]] = None,
    max_events_per_group: int = 500,
) -> Dict[str, Any]:
    """Watch multiple CloudWatch log groups and fetch recent logs.

    This tool retrieves logs from multiple log groups simultaneously,
    enabling cross-service log analysis. When *regions* is supplied the
    query fans out to every region and results are tagged with their
    origin region.

    Args:
        log_group_names: List of CloudWatch log group names to watch.
        time_range_minutes: Time range in minutes to look back (default: 60).
        filter_pattern: Optional CloudWatch Logs filter pattern.
        region: Primary AWS region (default: us-east-1). Ignored when *regions*
            is provided.
        regions: Optional list of AWS regions to query in parallel.  When
            provided, each log group is queried in every listed region and
            results include a ``region`` field on each event.
        credentials: AWS credentials configuration (optional).
        max_events_per_group: Maximum log events returned per group (default
            500, max 2000). A ``truncated`` flag is set when more events
            exist beyond this limit.

    Returns:
        Dictionary containing logs from each log group and summary statistics.
    """
    # Clamp max_events_per_group to a safe ceiling.
    max_events_per_group = min(max_events_per_group, 2000)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)

    # Determine the set of regions to fan-out across.
    active_regions: List[str] = regions if regions and len(regions) > 1 else [region]
    multi_region = len(active_regions) > 1

    results: Dict[str, Any] = {}
    total_events = 0
    errors: List[Dict[str, Any]] = []

    # Build (region, log_group, coroutine) tuples for concurrent fetching.
    fetch_tasks = []
    for reg in active_regions:
        watcher = get_watcher(region=reg, **_extract_credentials(credentials))
        for log_group in log_group_names:
            coro = watcher.fetch_logs(
                log_group_name=log_group,
                start_time=start_time,
                end_time=end_time,
                filter_pattern=filter_pattern,
                limit=max_events_per_group,
            )
            fetch_tasks.append((reg, log_group, coro))

    # Execute all fetches concurrently. asyncio.gather schedules every coroutine
    # at once so the boto3 calls run in parallel across the AWS thread pool;
    # return_exceptions keeps one failing group from cancelling the rest.
    fetched = await asyncio.gather(
        *(coro for _, _, coro in fetch_tasks),
        return_exceptions=True,
    )

    for (reg, log_group, _), logs in zip(fetch_tasks, fetched):
        result_key = f"{log_group}[{reg}]" if multi_region else log_group
        if isinstance(logs, Exception):
            errors.append({"log_group": result_key, "region": reg, "error": str(logs)})
            results[result_key] = {
                "event_count": 0,
                "total_fetched": 0,
                "truncated": False,
                "events": [],
                "region": reg,
                "error": str(logs),
            }
            continue
        # Tag each event with its origin region when doing multi-region.
        if multi_region:
            for ev in logs:
                ev["region"] = reg
        total_fetched = len(logs)
        capped = logs[:max_events_per_group]
        results[result_key] = {
            "event_count": total_fetched,
            "total_fetched": total_fetched,
            "truncated": total_fetched > max_events_per_group,
            "events": capped,
            "region": reg,
            "time_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
        }
        total_events += total_fetched

    return {
        "success": True,
        "log_groups": results,
        "summary": {
            "total_log_groups": len(log_group_names),
            "regions_queried": active_regions,
            "successful_log_groups": len(log_group_names) * len(active_regions) - len(errors),
            "total_events": total_events,
            "time_range_minutes": time_range_minutes,
            "max_events_per_group": max_events_per_group,
        },
        "errors": errors if errors else None,
    }


@handle_exceptions
async def analyze_log_patterns(
    log_group_names: List[str],
    time_range_minutes: int = 60,
    pattern_types: Optional[List[str]] = None,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
    severity_excludes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Analyze log patterns across multiple log groups.
    
    This tool identifies common patterns, error frequencies, and trends
    across the specified log groups.
    
    Args:
        log_group_names: List of CloudWatch log group names
        time_range_minutes: Time range in minutes to analyze
        pattern_types: Types of patterns to look for (error, warning, info, custom)
        region: AWS region
        credentials: AWS credentials configuration (optional)
        
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
    watcher = get_watcher(region=region, **_extract_credentials(credentials))

    from app.workflow.tools.cloudwatch_drilldown import apply_severity_excludes
    
    if pattern_types is None:
        pattern_types = ["error", "warning", "info"]
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)
    
    # Build Insights query for pattern analysis.
    # Patterns cover plain text, structured JSON ("level":"error"),
    # syslog priorities (<0>..<7>), Java logging (SEVERE/WARNING),
    # and Python/standard severity vocabulary.
    pattern_filters = SEVERITY_PATTERNS
    
    results = {}
    quality_sources: List[Dict[str, Any]] = []

    # Build one Insights bucket query per requested pattern type.
    pattern_queries: List[tuple] = []
    for pattern_type in pattern_types:
        filter_clause = apply_severity_excludes(
            pattern_filters.get(pattern_type, ""), severity_excludes,
        )
        query_string = f"""
        fields @timestamp, @message, @logStream
        | filter {filter_clause}
        | stats count() by bin(5m)
        | sort @timestamp desc
        """
        pattern_queries.append((pattern_type, query_string))

    # Unique-pattern query (distinct errors grouped by message). Built here so
    # it can run concurrently with the per-type bucket queries.
    _error_filter = apply_severity_excludes(
        SEVERITY_PATTERNS["error"], severity_excludes,
    )
    unique_query = f"""
    fields @timestamp, @message, @logStream
    | filter {_error_filter}
    | stats count() as occurrence_count,
            count_distinct(@logStream) as affected_streams,
            min(@timestamp) as first_seen,
            max(@timestamp) as last_seen
      by substr(@message, 0, 200) as error_pattern
    | sort occurrence_count desc
    | limit 50
    """

    # Fire every Insights query at once — they are independent and each one
    # otherwise blocks on its own start_query + poll loop (seconds each).
    _gathered = await asyncio.gather(
        *(
            watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=qs,
                start_time=start_time,
                end_time=end_time,
            )
            for _, qs in pattern_queries
        ),
        watcher.query_with_insights(
            log_group_names=log_group_names,
            query_string=unique_query,
            start_time=start_time,
            end_time=end_time,
        ),
        return_exceptions=True,
    )
    *_pattern_query_results, _unique_outcome = _gathered

    for (pattern_type, _), query_result in zip(pattern_queries, _pattern_query_results):
        if isinstance(query_result, Exception):
            results[pattern_type] = {"status": "error", "error": str(query_result)}
            continue
        quality_sources.append(query_result)
        entry: Dict[str, Any] = {
            "status": query_result.get("status"),
            "data": query_result.get("results", [])[:20],
            "statistics": query_result.get("statistics"),
        }
        if query_result.get("partial"):
            entry["partial"] = True
            entry["sampling_ratio"] = query_result.get("sampling_ratio")
        results[pattern_type] = entry

    # -------------------------------------------------------------------
    # Unique patterns: group errors by message to identify distinct issues
    # instead of just counting them in 5-minute buckets.
    # -------------------------------------------------------------------
    unique_patterns = []
    try:
        if isinstance(_unique_outcome, Exception):
            raise _unique_outcome
        unique_result = _unique_outcome
        quality_sources.append(unique_result)
        raw_unique = unique_result.get("results", [])
        # Post-process: semantically group near-duplicate patterns by
        # normalising dynamic tokens (IDs, timestamps, numbers).
        grouped: Dict[str, Dict[str, Any]] = {}
        for entry in raw_unique:
            raw_msg = ""
            occ = 0
            affected = 0
            first_seen = ""
            last_seen = ""
            for field in entry:
                f = field.get("field", "")
                v = field.get("value", "")
                if f == "error_pattern":
                    raw_msg = v or ""
                elif f == "occurrence_count":
                    try:
                        occ = int(float(v or 0))
                    except (ValueError, TypeError):
                        occ = 0
                elif f == "affected_streams":
                    try:
                        affected = int(float(v or 0))
                    except (ValueError, TypeError):
                        affected = 0
                elif f == "first_seen":
                    first_seen = v
                elif f == "last_seen":
                    last_seen = v
            key = _normalize_message(raw_msg)
            if key in grouped:
                grouped[key]["occurrence_count"] += occ
                grouped[key]["affected_streams"] = max(grouped[key]["affected_streams"], affected)
                if first_seen and (not grouped[key]["first_seen"] or first_seen < grouped[key]["first_seen"]):
                    grouped[key]["first_seen"] = first_seen
                if last_seen and last_seen > grouped[key].get("last_seen", ""):
                    grouped[key]["last_seen"] = last_seen
            else:
                grouped[key] = {
                    "normalized_pattern": key,
                    "example_message": raw_msg[:300],
                    "occurrence_count": occ,
                    "affected_streams": affected,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                }
        # Count-descending is the candidate ordering, not the final selection.
        # summarise_patterns re-ranks these by severity × rarity × volume when
        # cloudwatch_pattern_ranking is on, so the pool has to be wide enough
        # that a rare high-severity pattern is still in it — cutting at 15 here
        # drops the one-off FATAL before the ranker ever sees it. These are
        # small dicts and the summariser caps the agent-facing output anyway.
        _pool = settings.cloudwatch_pattern_candidates if settings.cloudwatch_pattern_ranking else 15
        unique_patterns = sorted(
            grouped.values(), key=lambda x: x["occurrence_count"], reverse=True
        )[:_pool]
    except Exception as e:
        logger.warning("analyze_log_patterns: unique_patterns query failed: %s", e)

    data_quality = _insights_quality_meta(*quality_sources)

    return {
        "success": True,
        "patterns": results,
        "unique_patterns": unique_patterns,
        "time_range": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "minutes": time_range_minutes
        },
        "log_groups_analyzed": log_group_names,
        "data_quality": data_quality,
    }


@handle_exceptions
async def detect_anomalies(
    log_group_names: List[str],
    time_range_minutes: int = 60,
    baseline_minutes: int = 1440,
    sensitivity: str = "medium",
    per_group_sensitivity: Optional[Dict[str, str]] = None,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Detect anomalies in log patterns compared to baseline.
    
    This tool compares current log patterns against a historical baseline
    to identify unusual activity, error spikes, or pattern deviations.
    
    Args:
        log_group_names: List of CloudWatch log group names
        time_range_minutes: Current time range to analyze
        baseline_minutes: Baseline time range for comparison (default: 24 hours)
        sensitivity: Global anomaly detection sensitivity (low, medium, high)
        per_group_sensitivity: Per-log-group sensitivity overrides. Keys are
            log group names, values are 'low', 'medium', or 'high'. Groups not
            listed fall back to the global *sensitivity*.
        region: AWS region
        credentials: AWS credentials configuration (optional)
        
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
    watcher = get_watcher(region=region, **_extract_credentials(credentials))
    
    end_time = datetime.now(timezone.utc)
    current_start = end_time - timedelta(minutes=time_range_minutes)
    baseline_start = end_time - timedelta(minutes=baseline_minutes + time_range_minutes)
    baseline_end = end_time - timedelta(minutes=baseline_minutes)
    
    # Sensitivity thresholds (used for simple-deviation fallback)
    _deviation_thresholds = {
        "low": 3.0,
        "medium": 2.0,
        "high": 1.5,
    }
    # Z-score thresholds mirror the same names.
    _z_thresholds = {
        "low": 3.0,
        "medium": 2.0,
        "high": 1.5,
    }
    # Global defaults
    threshold = _deviation_thresholds.get(sensitivity, 2.0)
    
    # Query for error counts in current period (uses Phase 1 severity taxonomy).
    # Group by @log so multi-group investigations stay attributed without extra queries.
    _error_filter = SEVERITY_PATTERNS["error"]
    bucket_query = (
        "fields @timestamp, @message, @log\n"
        f"| filter {_error_filter}\n"
        "| stats count() as error_count by bin(5m), @log\n"
        "| sort @timestamp desc\n"
    )

    anomalies: List[Dict[str, Any]] = []
    baseline_avg = 0.0
    baseline_std = 0.0
    data_quality: Dict[str, Any] = {}

    try:
        # Current and baseline windows are independent — run them concurrently.
        current_result, baseline_result = await asyncio.gather(
            watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=bucket_query,
                start_time=current_start,
                end_time=end_time,
            ),
            watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=bucket_query,
                start_time=baseline_start,
                end_time=baseline_end,
            ),
        )

        data_quality = _insights_quality_meta(current_result, baseline_result)

        # Per-log-group baseline profiles from the baseline window.
        group_counts: Dict[str, List[float]] = defaultdict(list)
        all_baseline_counts: List[float] = []
        for row in baseline_result.get("results", []):
            parsed = _parse_insights_row(row)
            lg = parsed.get("@log") or "_all"
            try:
                val = float(parsed.get("error_count", 0))
            except (ValueError, TypeError):
                val = 0.0
            group_counts[lg].append(val)
            all_baseline_counts.append(val)

        profiles = {lg: _compute_baseline_profile(vals) for lg, vals in group_counts.items()}
        if not profiles:
            profiles["_all"] = _compute_baseline_profile(all_baseline_counts)

        baseline_avg = (
            sum(all_baseline_counts) / len(all_baseline_counts) if all_baseline_counts else 0.0
        )
        if len(all_baseline_counts) >= 2:
            variance = sum((x - baseline_avg) ** 2 for x in all_baseline_counts) / len(
                all_baseline_counts
            )
            baseline_std = variance ** 0.5

        for row in current_result.get("results", []):
            parsed = _parse_insights_row(row)
            timestamp = parsed.get("@timestamp")
            log_group = parsed.get("@log")
            if not log_group and len(log_group_names) == 1:
                log_group = log_group_names[0]
            try:
                count = float(parsed.get("error_count", 0))
            except (ValueError, TypeError):
                count = 0.0

            profile_key = log_group if log_group and log_group in profiles else "_all"
            profile = profiles.get(profile_key) or profiles.get("_all") or _compute_baseline_profile([])

            eff_sensitivity = sensitivity
            if per_group_sensitivity and log_group:
                eff_sensitivity = per_group_sensitivity.get(log_group, sensitivity)
            elif per_group_sensitivity and len(log_group_names) == 1:
                eff_sensitivity = per_group_sensitivity.get(log_group_names[0], sensitivity)

            eff_z_threshold = _z_thresholds.get(eff_sensitivity, 2.0)
            eff_threshold = _deviation_thresholds.get(eff_sensitivity, 2.0)

            scored = _score_error_bucket(count, profile, eff_z_threshold, eff_threshold)
            if scored is None:
                continue

            anomalies.append({
                "timestamp": timestamp,
                "log_group": log_group,
                **scored,
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
            "critical_severity": len([a for a in anomalies if a["severity"] == "critical"]),
            "high_severity": len([a for a in anomalies if a["severity"] == "high"]),
            "medium_severity": len([a for a in anomalies if a["severity"] == "medium"]),
            "low_severity": len([a for a in anomalies if a["severity"] == "low"]),
            "baseline_period_minutes": baseline_minutes,
            "current_period_minutes": time_range_minutes,
            "sensitivity": sensitivity,
            "baseline_average": round(baseline_avg, 2),
            "baseline_std_dev": round(baseline_std, 2) if baseline_std else None,
        },
        "log_groups_analyzed": log_group_names,
        "data_quality": data_quality,
    }


@handle_exceptions
async def correlate_logs(
    log_group_names: List[str],
    correlation_id: Optional[str] = None,
    time_range_minutes: int = 60,
    trace_id: Optional[str] = None,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None
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
        credentials: AWS credentials configuration (optional)
        
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
    watcher = get_watcher(region=region, **_extract_credentials(credentials))
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=time_range_minutes)

    timeline: List[Dict[str, Any]] = []
    data_quality: Dict[str, Any] = {}

    if correlation_id or trace_id:
        # One cross-group Insights query — correct substring match syntax.
        filters: List[str] = []
        if correlation_id:
            filters.append(f"@message like /{re.escape(correlation_id)}/")
        if trace_id:
            filters.append(f"@message like /{re.escape(trace_id)}/")
        filter_clause = " or ".join(filters)
        query_string = (
            "fields @timestamp, @message, @logStream, @log\n"
            f"| filter {filter_clause}\n"
            "| sort @timestamp asc\n"
            "| limit 500\n"
        )
        try:
            query_result = await watcher.query_with_insights(
                log_group_names=log_group_names,
                query_string=query_string,
                start_time=start_time,
                end_time=end_time,
                limit=500,
            )
            data_quality = _insights_quality_meta(query_result)
            for row in query_result.get("results", []):
                parsed = _parse_insights_row(row)
                timeline.append({
                    "timestamp": parsed.get("@timestamp", ""),
                    "log_group": parsed.get("@log"),
                    "log_stream": parsed.get("@logStream"),
                    "message": (parsed.get("@message") or "")[:1500],
                })
        except Exception as e:
            logger.warning("correlate_logs Insights query failed: %s", e)
            return {"success": False, "error": str(e)}
    else:
        # No ID — fetch recent events per group concurrently (no broken filter
        # syntax). Independent per-group fetches run in parallel via gather.
        all_events: List[Dict[str, Any]] = []
        _group_logs = await asyncio.gather(
            *(
                watcher.fetch_logs(
                    log_group_name=log_group,
                    start_time=start_time,
                    end_time=end_time,
                    filter_pattern=None,
                    limit=200,
                )
                for log_group in log_group_names
            ),
            return_exceptions=True,
        )
        for log_group, logs in zip(log_group_names, _group_logs):
            if isinstance(logs, Exception):
                logger.warning("Error fetching logs from %s: %s", log_group, logs)
                continue
            for event in logs:
                event["log_group"] = log_group
                all_events.append(event)

        all_events.sort(key=lambda x: x.get("timestamp", 0))
        for event in all_events:
            timestamp_ms = event.get("timestamp", 0)
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            timeline.append({
                "timestamp": timestamp.isoformat(),
                "timestamp_ms": timestamp_ms,
                "log_group": event.get("log_group"),
                "log_stream": event.get("logStreamName"),
                "message": event.get("message", "")[:1500],
                "event_id": event.get("eventId"),
            })
        if len(timeline) >= 200 * len(log_group_names):
            data_quality = {"truncated": True, "hint": "pass correlation_id or trace_id to narrow"}

    seen_groups: set = set()
    service_flow: List[str] = []
    for event in timeline:
        group = event.get("log_group")
        if group and group not in seen_groups:
            service_flow.append(group)
            seen_groups.add(group)

    result: Dict[str, Any] = {
        "success": True,
        "timeline": timeline,
        "summary": {
            "total_events": len(timeline),
            "services_involved": list(seen_groups),
            "service_flow": service_flow,
            "time_range_minutes": time_range_minutes,
        },
        "correlation_id": correlation_id,
        "trace_id": trace_id,
    }
    if data_quality:
        result["data_quality"] = data_quality
    return result


@handle_exceptions
async def discover_log_groups(
    prefix: Optional[str] = None,
    tag_key: Optional[str] = None,
    tag_value: Optional[str] = None,
    limit: int = 50,
    region: str = "us-east-1",
    credentials: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Discover CloudWatch log groups by name prefix or resource tags.

    Engineers can call this tool when they know a service name or tag but
    not the exact log group path.  The *prefix* term is matched as a
    case-sensitive substring anywhere in the log group name (e.g. "kyc"
    finds "/aws/lambda/kyc-handler"); tag search paginates before filtering.

    Args:
        prefix: Substring to match anywhere in the log group name, e.g.
            'kyc' or '/aws/lambda/kyc-' to find KYC Lambda log groups.
        tag_key: Tag key to filter by (e.g. 'Environment').
        tag_value: Tag value to filter by (e.g. 'production').  Requires
            *tag_key*.
        limit: Maximum number of log groups to return (default 50).
        region: AWS region (default: us-east-1).
        credentials: AWS credentials configuration (optional).

    Returns:
        Dictionary with matching log groups, their ARNs, stored bytes, and
        retention policy.
    """
    watcher = get_watcher(region=region, **_extract_credentials(credentials))
    client = watcher.client

    log_groups: List[Dict[str, Any]] = []
    next_token = None
    truncated = False

    # Token-AND, case-insensitive matching.  A path-like query such as
    # "/ecs/compliance" is split into tokens ["ecs", "compliance"] and matches
    # any name that contains BOTH tokens anywhere — so the segments need not be
    # contiguous (it matches "/aws/ecs/containerinsights/compliance-test/..."),
    # and case does not matter.  A single plain term ("lambda", "/aws/lambda/")
    # behaves like the previous substring search.
    tokens = [t for t in re.split(r"[\s/,]+", prefix) if t] if prefix else []
    lowered_tokens = [t.lower() for t in tokens]

    # Narrow server-side with the most selective (longest) token to limit data
    # transfer; remaining tokens are applied client-side.  logGroupNamePattern
    # is a case-sensitive substring match, so we only use it when it is safe
    # (the token is lowercase, matching the overwhelmingly lowercase naming
    # convention); otherwise we list and filter client-side.
    server_filter = max(tokens, key=len) if tokens else None
    describe_kwargs: Dict[str, Any] = {"limit": 50}
    if server_filter and server_filter == server_filter.lower():
        describe_kwargs["logGroupNamePattern"] = server_filter

    def _matches(name: str) -> bool:
        if not lowered_tokens:
            return True
        low = name.lower()
        return all(tok in low for tok in lowered_tokens)

    # Bound raw pagination: client-side AND-filtering can reject many rows, so
    # we may need to scan more pages than `limit` to fill `limit` matches.
    fetch_cap = max(limit * 5, 250)
    fetched = 0

    while len(log_groups) < limit and fetched < fetch_cap:
        if next_token:
            describe_kwargs["nextToken"] = next_token

        response = await run_in_aws_pool(
            lambda: client.describe_log_groups(**describe_kwargs),
        )

        for grp in response.get("logGroups", []):
            fetched += 1
            name = grp.get("logGroupName") or ""
            if not _matches(name):
                continue
            log_groups.append({
                "name": name,
                "arn": grp.get("arn"),
                "stored_bytes": grp.get("storedBytes", 0),
                "retention_days": grp.get("retentionInDays"),
                "creation_time": grp.get("creationTime"),
            })
            if len(log_groups) >= limit:
                break

        next_token = response.get("nextToken")
        if not next_token:
            break

    # More groups may exist than we returned — let the caller surface a hint.
    if next_token:
        truncated = True

    search_method = "tokens" if tokens else "all"

    # Optional tag filtering (applied after prefix discovery).
    if tag_key and log_groups:
        search_method = "tag" if not prefix else "prefix+tag"
        # Cap at 200 groups to avoid excessive list_tags_for_resource calls.
        sample = log_groups[:200]
        # Run tag lookups concurrently but bounded so we don't flood the API
        # or the AWS thread pool with up to 200 simultaneous calls.
        _tag_sem = asyncio.Semaphore(10)

        async def _check_tags(grp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            arn = grp.get("arn")
            if not arn:
                return None
            async with _tag_sem:
                try:
                    tags_resp = await run_in_aws_pool(
                        lambda a=arn: client.list_tags_for_resource(resourceArn=a),
                    )
                except Exception as tag_err:
                    logger.debug(
                        "discover_log_groups: skipping tag check for %s: %s", arn, tag_err
                    )
                    return None
            tags = tags_resp.get("tags", {})
            if tag_value:
                return grp if tags.get(tag_key) == tag_value else None
            return grp if tag_key in tags else None

        _checked = await asyncio.gather(*(_check_tags(g) for g in sample))
        log_groups = [g for g in _checked if g is not None]

    return {
        "success": True,
        "log_groups": log_groups[:limit],
        "count": min(len(log_groups), limit),
        "truncated": truncated,
        "search_method": search_method,
        "prefix": prefix,
        "tag_key": tag_key,
        "tag_value": tag_value,
    }
