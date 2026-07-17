"""Rate limit tracking for API responses.

This module provides dataclasses and utilities for tracking API rate limits
from response headers. It supports four rate limit buckets: requests per minute,
requests per hour, tokens per minute, and tokens per hour.

**Validates: Requirements 5.2, 5.3, 5.4**
"""

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RateLimitBucket:
    """One rate-limit window (e.g., requests per minute).
    
    Tracks usage against a specific rate limit bucket and provides
    computed properties for usage percentage and remaining time.
    
    Attributes:
        limit: Maximum allowed in this bucket (e.g., 50 requests/min)
        remaining: How many are left in this window
        reset_seconds: Seconds until the bucket resets (at capture time)
        captured_at: Unix timestamp when this data was captured
    """
    limit: int = 0
    remaining: int = 0
    reset_seconds: float = 0.0
    captured_at: float = 0.0
    
    @property
    def used(self) -> int:
        """Calculate how many units have been used in this bucket."""
        return max(0, self.limit - self.remaining)
    
    @property
    def usage_pct(self) -> float:
        """Calculate usage percentage (0.0 to 1.0).
        
        Returns:
            Usage as a fraction between 0.0 and 1.0.
            Returns 0.0 if limit is 0 to avoid division by zero.
        """
        if self.limit == 0:
            return 0.0
        return self.used / self.limit
    
    @property
    def remaining_seconds_now(self) -> float:
        """Calculate remaining seconds until reset, adjusted for elapsed time.
        
        Adjusts the reset_seconds based on how much time has elapsed since
        the data was captured.
        
        Returns:
            Seconds remaining until reset, or 0.0 if already reset.
        """
        if self.captured_at == 0.0:
            return self.reset_seconds
        
        elapsed = time.time() - self.captured_at
        remaining = self.reset_seconds - elapsed
        return max(0.0, remaining)


@dataclass
class RateLimitState:
    """Full rate-limit state from response headers.
    
    Tracks all four rate limit buckets for a provider:
    - Requests per minute
    - Requests per hour
    - Tokens per minute
    - Tokens per hour
    
    Attributes:
        requests_min: Requests per minute bucket
        requests_hour: Requests per hour bucket
        tokens_min: Tokens per minute bucket
        tokens_hour: Tokens per hour bucket
        captured_at: Unix timestamp when this state was captured
        provider: Provider name (e.g., "anthropic", "openai")
    """
    requests_min: RateLimitBucket
    requests_hour: RateLimitBucket
    tokens_min: RateLimitBucket
    tokens_hour: RateLimitBucket
    captured_at: float
    provider: str
    
    @property
    def has_data(self) -> bool:
        """Check if any bucket has non-zero limit data.
        
        Returns:
            True if at least one bucket has a non-zero limit, False otherwise.
        """
        return (
            self.requests_min.limit > 0
            or self.requests_hour.limit > 0
            or self.tokens_min.limit > 0
            or self.tokens_hour.limit > 0
        )


def parse_rate_limit_headers(
    headers: dict,
    provider: str = "",
) -> Optional[RateLimitState]:
    """Parse x-ratelimit-* headers into RateLimitState.
    
    Parses standard rate limit headers from API responses and constructs
    a structured RateLimitState object with all four buckets.
    
    Headers parsed:
    - x-ratelimit-limit-requests (requests per minute)
    - x-ratelimit-limit-requests-1h (requests per hour)
    - x-ratelimit-limit-tokens (tokens per minute)
    - x-ratelimit-limit-tokens-1h (tokens per hour)
    - x-ratelimit-remaining-* (corresponding remaining values)
    - x-ratelimit-reset-* (corresponding reset times)
    
    Args:
        headers: Dictionary of HTTP response headers (case-insensitive)
        provider: Provider name (e.g., "anthropic", "openai")
    
    Returns:
        RateLimitState if any rate limit headers found, None otherwise.
    
    **Validates: Requirements 5.1**
    """
    # Normalize headers to lowercase for case-insensitive lookup
    normalized_headers = {k.lower(): v for k, v in headers.items()}
    
    def get_int(key: str, default: int = 0) -> int:
        """Get integer value from headers, return default if not found or invalid."""
        value = normalized_headers.get(key.lower(), "")
        try:
            return int(value) if value else default
        except (ValueError, TypeError):
            return default
    
    def get_float(key: str, default: float = 0.0) -> float:
        """Get float value from headers, return default if not found or invalid."""
        value = normalized_headers.get(key.lower(), "")
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default
    
    now = time.time()
    
    # Parse requests per minute bucket
    requests_min = RateLimitBucket(
        limit=get_int("x-ratelimit-limit-requests"),
        remaining=get_int("x-ratelimit-remaining-requests"),
        reset_seconds=get_float("x-ratelimit-reset-requests"),
        captured_at=now,
    )
    
    # Parse requests per hour bucket
    requests_hour = RateLimitBucket(
        limit=get_int("x-ratelimit-limit-requests-1h"),
        remaining=get_int("x-ratelimit-remaining-requests-1h"),
        reset_seconds=get_float("x-ratelimit-reset-requests-1h"),
        captured_at=now,
    )
    
    # Parse tokens per minute bucket
    tokens_min = RateLimitBucket(
        limit=get_int("x-ratelimit-limit-tokens"),
        remaining=get_int("x-ratelimit-remaining-tokens"),
        reset_seconds=get_float("x-ratelimit-reset-tokens"),
        captured_at=now,
    )
    
    # Parse tokens per hour bucket
    tokens_hour = RateLimitBucket(
        limit=get_int("x-ratelimit-limit-tokens-1h"),
        remaining=get_int("x-ratelimit-remaining-tokens-1h"),
        reset_seconds=get_float("x-ratelimit-reset-tokens-1h"),
        captured_at=now,
    )
    
    state = RateLimitState(
        requests_min=requests_min,
        requests_hour=requests_hour,
        tokens_min=tokens_min,
        tokens_hour=tokens_hour,
        captured_at=now,
        provider=provider,
    )
    
    # Return None if no rate limit data found
    if not state.has_data:
        return None
    
    return state


def format_rate_limit_display(state: RateLimitState) -> str:
    """Format rate limit state for terminal/chat display.
    
    Creates a multi-line formatted display with progress bars and warnings
    for each rate limit bucket. Includes warnings when any bucket exceeds
    80% usage.
    
    Args:
        state: RateLimitState to format
    
    Returns:
        Formatted string with progress bars and warnings.
    
    **Validates: Requirements 5.5, 5.6**
    """
    if not state.has_data:
        return "No rate limit data available"
    
    lines = [f"Rate Limits ({state.provider}):"]
    warnings = []
    
    def format_bucket(name: str, bucket: RateLimitBucket, unit: str) -> str:
        """Format a single bucket with progress bar."""
        if bucket.limit == 0:
            return ""
        
        usage_pct = bucket.usage_pct
        bar_width = 20
        filled = int(bar_width * usage_pct)
        bar = "█" * filled + "░" * (bar_width - filled)
        
        # Add warning if usage exceeds 80%
        warning_marker = " ⚠️" if usage_pct >= 0.80 else ""
        
        # Format remaining time
        remaining_sec = bucket.remaining_seconds_now
        if remaining_sec > 3600:
            time_str = f"{remaining_sec / 3600:.1f}h"
        elif remaining_sec > 60:
            time_str = f"{remaining_sec / 60:.1f}m"
        else:
            time_str = f"{remaining_sec:.0f}s"
        
        line = (
            f"  {name:20s} [{bar}] "
            f"{bucket.used:,}/{bucket.limit:,} {unit} "
            f"({usage_pct * 100:.1f}%) "
            f"- resets in {time_str}{warning_marker}"
        )
        
        if usage_pct >= 0.80:
            warnings.append(f"{name} at {usage_pct * 100:.0f}% capacity")
        
        return line
    
    # Format each bucket
    if state.requests_min.limit > 0:
        lines.append(format_bucket("Requests/min", state.requests_min, "req"))
    
    if state.requests_hour.limit > 0:
        lines.append(format_bucket("Requests/hour", state.requests_hour, "req"))
    
    if state.tokens_min.limit > 0:
        lines.append(format_bucket("Tokens/min", state.tokens_min, "tok"))
    
    if state.tokens_hour.limit > 0:
        lines.append(format_bucket("Tokens/hour", state.tokens_hour, "tok"))
    
    # Add warnings section if any
    if warnings:
        lines.append("")
        lines.append("⚠️  Warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")
    
    return "\n".join(lines)


def format_rate_limit_compact(state: RateLimitState) -> str:
    """One-line compact summary for status bars.
    
    Creates a concise one-line summary showing the most constrained
    bucket and any warnings.
    
    Args:
        state: RateLimitState to format
    
    Returns:
        Compact one-line summary string.
    
    **Validates: Requirements 5.7**
    """
    if not state.has_data:
        return "No rate limits"
    
    # Find the bucket with highest usage percentage
    buckets = [
        ("req/min", state.requests_min),
        ("req/hr", state.requests_hour),
        ("tok/min", state.tokens_min),
        ("tok/hr", state.tokens_hour),
    ]
    
    # Filter out empty buckets and find max usage
    active_buckets = [(name, b) for name, b in buckets if b.limit > 0]
    if not active_buckets:
        return "No rate limits"
    
    max_bucket = max(active_buckets, key=lambda x: x[1].usage_pct)
    name, bucket = max_bucket
    
    usage_pct = bucket.usage_pct * 100
    warning = " ⚠️" if usage_pct >= 80 else ""
    
    return f"{state.provider}: {name} {usage_pct:.0f}% ({bucket.remaining:,} left){warning}"
