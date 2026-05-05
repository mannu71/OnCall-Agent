"""Usage and rate limit API endpoints.

This module provides endpoints for monitoring API usage and rate limits.
The rate limit data is captured from API response headers during agent execution.

**Validates: Requirements 5.5, 5.6**
"""

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.rate_limit_tracker import (
    RateLimitState,
    RateLimitBucket,
    format_rate_limit_display,
    format_rate_limit_compact,
)


router = APIRouter(prefix="/usage", tags=["usage"])


# Response models
class RateLimitBucketResponse(BaseModel):
    """Rate limit bucket information."""
    limit: int = Field(..., description="Maximum allowed in this bucket")
    remaining: int = Field(..., description="How many are left in this window")
    used: int = Field(..., description="How many have been used")
    usage_pct: float = Field(..., description="Usage percentage (0.0 to 1.0)")
    reset_seconds: float = Field(..., description="Seconds until the bucket resets")
    
    @classmethod
    def from_bucket(cls, bucket: RateLimitBucket) -> "RateLimitBucketResponse":
        """Create response from RateLimitBucket."""
        return cls(
            limit=bucket.limit,
            remaining=bucket.remaining,
            used=bucket.used,
            usage_pct=bucket.usage_pct,
            reset_seconds=bucket.remaining_seconds_now,
        )


class RateLimitResponse(BaseModel):
    """Rate limit state response."""
    has_data: bool = Field(..., description="Whether rate limit data is available")
    provider: Optional[str] = Field(None, description="Provider name (e.g., 'anthropic', 'openai')")
    requests_min: Optional[RateLimitBucketResponse] = Field(None, description="Requests per minute bucket")
    requests_hour: Optional[RateLimitBucketResponse] = Field(None, description="Requests per hour bucket")
    tokens_min: Optional[RateLimitBucketResponse] = Field(None, description="Tokens per minute bucket")
    tokens_hour: Optional[RateLimitBucketResponse] = Field(None, description="Tokens per hour bucket")
    display: Optional[str] = Field(None, description="Formatted display string with progress bars")
    compact: Optional[str] = Field(None, description="Compact one-line summary")
    captured_at: Optional[float] = Field(None, description="Unix timestamp when data was captured")
    warnings: list[str] = Field(default_factory=list, description="Warnings for buckets exceeding 80% usage")
    
    @classmethod
    def from_state(cls, state: RateLimitState) -> "RateLimitResponse":
        """Create response from RateLimitState."""
        # Collect warnings for buckets exceeding 80%
        warnings = []
        buckets = [
            ("Requests/min", state.requests_min),
            ("Requests/hour", state.requests_hour),
            ("Tokens/min", state.tokens_min),
            ("Tokens/hour", state.tokens_hour),
        ]
        
        for name, bucket in buckets:
            if bucket.limit > 0 and bucket.usage_pct >= 0.80:
                warnings.append(f"{name} at {bucket.usage_pct * 100:.0f}% capacity")
        
        return cls(
            has_data=state.has_data,
            provider=state.provider,
            requests_min=RateLimitBucketResponse.from_bucket(state.requests_min) if state.requests_min.limit > 0 else None,
            requests_hour=RateLimitBucketResponse.from_bucket(state.requests_hour) if state.requests_hour.limit > 0 else None,
            tokens_min=RateLimitBucketResponse.from_bucket(state.tokens_min) if state.tokens_min.limit > 0 else None,
            tokens_hour=RateLimitBucketResponse.from_bucket(state.tokens_hour) if state.tokens_hour.limit > 0 else None,
            display=format_rate_limit_display(state),
            compact=format_rate_limit_compact(state),
            captured_at=state.captured_at,
            warnings=warnings,
        )
    
    @classmethod
    def no_data(cls) -> "RateLimitResponse":
        """Create response for when no rate limit data is available."""
        return cls(
            has_data=False,
            display="No rate limit data available",
            compact="No rate limits",
        )


# Global variable to store the most recent rate limit state
# This is updated by ReactStrategy during execution
_current_rate_limit_state: Optional[RateLimitState] = None


def update_rate_limit_state(state: Optional[RateLimitState]) -> None:
    """Update the current rate limit state.
    
    This function is called by ReactStrategy to update the global rate limit state
    after capturing it from API response headers.
    
    Args:
        state: The new rate limit state, or None to clear
    """
    global _current_rate_limit_state
    _current_rate_limit_state = state


def get_rate_limit_state() -> Optional[RateLimitState]:
    """Get the current rate limit state.
    
    Returns:
        The most recent rate limit state, or None if no data is available
    """
    return _current_rate_limit_state


@router.get("/rate-limits", response_model=RateLimitResponse)
async def get_rate_limits() -> RateLimitResponse:
    """Get current rate limit state.
    
    Returns the most recent rate limit data captured from API response headers
    during agent execution. The data includes usage information for all four
    rate limit buckets:
    - Requests per minute
    - Requests per hour
    - Tokens per minute
    - Tokens per hour
    
    The response includes:
    - Current usage counts and percentages
    - Remaining capacity
    - Reset times (adjusted for elapsed time since capture)
    - Formatted display strings (multi-line and compact)
    - Warnings for buckets exceeding 80% usage
    
    Returns:
        RateLimitResponse with current rate limit state
    
    **Validates: Requirements 5.5, 5.6**
    """
    state = get_rate_limit_state()
    
    if state is None or not state.has_data:
        return RateLimitResponse.no_data()
    
    return RateLimitResponse.from_state(state)


@router.get("/rate-limits/display", response_model=Dict[str, str])
async def get_rate_limits_display() -> Dict[str, str]:
    """Get formatted rate limit display strings.
    
    Returns formatted display strings for terminal/chat display and status bars.
    This is a convenience endpoint for clients that only need the formatted strings.
    
    Returns:
        Dictionary with 'display' (multi-line with progress bars) and 'compact' (one-line summary)
    
    **Validates: Requirements 5.5, 5.7**
    """
    state = get_rate_limit_state()
    
    if state is None or not state.has_data:
        return {
            "display": "No rate limit data available",
            "compact": "No rate limits"
        }
    
    return {
        "display": format_rate_limit_display(state),
        "compact": format_rate_limit_compact(state)
    }
