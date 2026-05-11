"""Unit tests for rate limit tracking dataclasses.

**Validates: Requirements 5.2, 5.3, 5.4**
"""

import time
import pytest
from app.core.rate_limit_tracker import (
    RateLimitBucket,
    RateLimitState,
    parse_rate_limit_headers,
    format_rate_limit_display,
    format_rate_limit_compact,
)


class TestRateLimitBucket:
    """Tests for RateLimitBucket dataclass."""
    
    def test_used_calculation(self):
        """Test that used property correctly calculates consumed units."""
        bucket = RateLimitBucket(limit=100, remaining=30)
        assert bucket.used == 70
    
    def test_used_never_negative(self):
        """Test that used property never returns negative values."""
        bucket = RateLimitBucket(limit=100, remaining=150)
        assert bucket.used == 0
    
    def test_usage_pct_calculation(self):
        """Test that usage_pct correctly calculates percentage."""
        bucket = RateLimitBucket(limit=100, remaining=25)
        assert bucket.usage_pct == 0.75
    
    def test_usage_pct_zero_limit(self):
        """Test that usage_pct handles zero limit gracefully."""
        bucket = RateLimitBucket(limit=0, remaining=0)
        assert bucket.usage_pct == 0.0
    
    def test_usage_pct_full_usage(self):
        """Test usage_pct when bucket is fully consumed."""
        bucket = RateLimitBucket(limit=50, remaining=0)
        assert bucket.usage_pct == 1.0
    
    def test_usage_pct_no_usage(self):
        """Test usage_pct when bucket is unused."""
        bucket = RateLimitBucket(limit=50, remaining=50)
        assert bucket.usage_pct == 0.0
    
    def test_remaining_seconds_now_no_elapsed_time(self):
        """Test remaining_seconds_now when no time has elapsed."""
        now = time.time()
        bucket = RateLimitBucket(
            limit=100,
            remaining=50,
            reset_seconds=60.0,
            captured_at=now
        )
        # Should be approximately 60 seconds (allowing small time drift)
        assert 59.0 <= bucket.remaining_seconds_now <= 61.0
    
    def test_remaining_seconds_now_with_elapsed_time(self):
        """Test remaining_seconds_now adjusts for elapsed time."""
        past = time.time() - 30.0  # 30 seconds ago
        bucket = RateLimitBucket(
            limit=100,
            remaining=50,
            reset_seconds=60.0,
            captured_at=past
        )
        # Should be approximately 30 seconds remaining
        assert 29.0 <= bucket.remaining_seconds_now <= 31.0
    
    def test_remaining_seconds_now_already_reset(self):
        """Test remaining_seconds_now when reset time has passed."""
        past = time.time() - 120.0  # 2 minutes ago
        bucket = RateLimitBucket(
            limit=100,
            remaining=50,
            reset_seconds=60.0,
            captured_at=past
        )
        # Should be 0.0 since reset time has passed
        assert bucket.remaining_seconds_now == 0.0
    
    def test_remaining_seconds_now_no_captured_at(self):
        """Test remaining_seconds_now when captured_at is not set."""
        bucket = RateLimitBucket(
            limit=100,
            remaining=50,
            reset_seconds=60.0,
            captured_at=0.0
        )
        # Should return reset_seconds unchanged
        assert bucket.remaining_seconds_now == 60.0


class TestRateLimitState:
    """Tests for RateLimitState dataclass."""
    
    def test_has_data_with_requests_min(self):
        """Test has_data returns True when requests_min has data."""
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=time.time(),
            provider="anthropic"
        )
        assert state.has_data is True
    
    def test_has_data_with_requests_hour(self):
        """Test has_data returns True when requests_hour has data."""
        state = RateLimitState(
            requests_min=RateLimitBucket(),
            requests_hour=RateLimitBucket(limit=1000),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=time.time(),
            provider="openai"
        )
        assert state.has_data is True
    
    def test_has_data_with_tokens_min(self):
        """Test has_data returns True when tokens_min has data."""
        state = RateLimitState(
            requests_min=RateLimitBucket(),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(limit=40000),
            tokens_hour=RateLimitBucket(),
            captured_at=time.time(),
            provider="anthropic"
        )
        assert state.has_data is True
    
    def test_has_data_with_tokens_hour(self):
        """Test has_data returns True when tokens_hour has data."""
        state = RateLimitState(
            requests_min=RateLimitBucket(),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(limit=2000000),
            captured_at=time.time(),
            provider="openai"
        )
        assert state.has_data is True
    
    def test_has_data_no_data(self):
        """Test has_data returns False when all buckets are empty."""
        state = RateLimitState(
            requests_min=RateLimitBucket(),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=time.time(),
            provider="unknown"
        )
        assert state.has_data is False
    
    def test_has_data_multiple_buckets(self):
        """Test has_data returns True when multiple buckets have data."""
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50),
            requests_hour=RateLimitBucket(limit=1000),
            tokens_min=RateLimitBucket(limit=40000),
            tokens_hour=RateLimitBucket(limit=2000000),
            captured_at=time.time(),
            provider="anthropic"
        )
        assert state.has_data is True
    
    def test_dataclass_initialization(self):
        """Test that RateLimitState can be initialized with all fields."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=30, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(limit=1000, remaining=800, reset_seconds=3600.0, captured_at=now),
            tokens_min=RateLimitBucket(limit=40000, remaining=20000, reset_seconds=60.0, captured_at=now),
            tokens_hour=RateLimitBucket(limit=2000000, remaining=1500000, reset_seconds=3600.0, captured_at=now),
            captured_at=now,
            provider="anthropic"
        )
        
        assert state.requests_min.limit == 50
        assert state.requests_hour.limit == 1000
        assert state.tokens_min.limit == 40000
        assert state.tokens_hour.limit == 2000000
        assert state.provider == "anthropic"
        assert state.captured_at == now



class TestParseRateLimitHeaders:
    """Tests for parse_rate_limit_headers function."""
    
    def test_parse_all_headers(self):
        """Test parsing when all rate limit headers are present."""
        headers = {
            "x-ratelimit-limit-requests": "50",
            "x-ratelimit-remaining-requests": "30",
            "x-ratelimit-reset-requests": "60.0",
            "x-ratelimit-limit-requests-1h": "1000",
            "x-ratelimit-remaining-requests-1h": "800",
            "x-ratelimit-reset-requests-1h": "3600.0",
            "x-ratelimit-limit-tokens": "40000",
            "x-ratelimit-remaining-tokens": "20000",
            "x-ratelimit-reset-tokens": "60.0",
            "x-ratelimit-limit-tokens-1h": "2000000",
            "x-ratelimit-remaining-tokens-1h": "1500000",
            "x-ratelimit-reset-tokens-1h": "3600.0",
        }
        
        state = parse_rate_limit_headers(headers, provider="anthropic")
        
        assert state is not None
        assert state.provider == "anthropic"
        assert state.requests_min.limit == 50
        assert state.requests_min.remaining == 30
        assert state.requests_hour.limit == 1000
        assert state.tokens_min.limit == 40000
        assert state.tokens_hour.limit == 2000000
        assert state.has_data is True
    
    def test_parse_case_insensitive_headers(self):
        """Test that header parsing is case-insensitive."""
        headers = {
            "X-RateLimit-Limit-Requests": "50",
            "X-RATELIMIT-REMAINING-REQUESTS": "30",
            "x-RateLimit-Reset-Requests": "60.0",
        }
        
        state = parse_rate_limit_headers(headers, provider="openai")
        
        assert state is not None
        assert state.requests_min.limit == 50
        assert state.requests_min.remaining == 30
    
    def test_parse_partial_headers(self):
        """Test parsing when only some headers are present."""
        headers = {
            "x-ratelimit-limit-requests": "50",
            "x-ratelimit-remaining-requests": "30",
            "x-ratelimit-reset-requests": "60.0",
        }
        
        state = parse_rate_limit_headers(headers, provider="anthropic")
        
        assert state is not None
        assert state.requests_min.limit == 50
        assert state.requests_hour.limit == 0
        assert state.tokens_min.limit == 0
        assert state.tokens_hour.limit == 0
    
    def test_parse_no_headers(self):
        """Test parsing when no rate limit headers are present."""
        headers = {
            "content-type": "application/json",
            "content-length": "1234",
        }
        
        state = parse_rate_limit_headers(headers, provider="unknown")
        
        assert state is None
    
    def test_parse_invalid_values(self):
        """Test parsing when header values are invalid."""
        headers = {
            "x-ratelimit-limit-requests": "invalid",
            "x-ratelimit-remaining-requests": "not-a-number",
            "x-ratelimit-reset-requests": "bad-float",
            "x-ratelimit-limit-tokens": "100",  # This one is valid
            "x-ratelimit-remaining-tokens": "50",
        }
        
        state = parse_rate_limit_headers(headers, provider="test")
        
        assert state is not None
        # Invalid values should default to 0
        assert state.requests_min.limit == 0
        assert state.requests_min.remaining == 0
        # Valid values should be parsed
        assert state.tokens_min.limit == 100
        assert state.tokens_min.remaining == 50
    
    def test_parse_empty_string_values(self):
        """Test parsing when header values are empty strings."""
        headers = {
            "x-ratelimit-limit-requests": "",
            "x-ratelimit-remaining-requests": "",
            "x-ratelimit-limit-tokens": "100",
        }
        
        state = parse_rate_limit_headers(headers, provider="test")
        
        assert state is not None
        assert state.requests_min.limit == 0
        assert state.tokens_min.limit == 100


class TestFormatRateLimitDisplay:
    """Tests for format_rate_limit_display function."""
    
    def test_format_with_all_buckets(self):
        """Test formatting when all buckets have data."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=30, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(limit=1000, remaining=800, reset_seconds=3600.0, captured_at=now),
            tokens_min=RateLimitBucket(limit=40000, remaining=20000, reset_seconds=60.0, captured_at=now),
            tokens_hour=RateLimitBucket(limit=2000000, remaining=1500000, reset_seconds=3600.0, captured_at=now),
            captured_at=now,
            provider="anthropic"
        )
        
        output = format_rate_limit_display(state)
        
        assert "Rate Limits (anthropic):" in output
        assert "Requests/min" in output
        assert "Requests/hour" in output
        assert "Tokens/min" in output
        assert "Tokens/hour" in output
        assert "20/50" in output  # requests_min used/limit
        assert "200/1,000" in output  # requests_hour used/limit
    
    def test_format_with_warning(self):
        """Test formatting includes warning when usage exceeds 80%."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=5, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=now,
            provider="openai"
        )
        
        output = format_rate_limit_display(state)
        
        assert "⚠️" in output
        assert "Warnings:" in output
        assert "Requests/min at 90% capacity" in output
    
    def test_format_no_data(self):
        """Test formatting when no rate limit data is available."""
        state = RateLimitState(
            requests_min=RateLimitBucket(),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=time.time(),
            provider="unknown"
        )
        
        output = format_rate_limit_display(state)
        
        assert output == "No rate limit data available"
    
    def test_format_partial_buckets(self):
        """Test formatting when only some buckets have data."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=30, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(limit=2000000, remaining=1500000, reset_seconds=3600.0, captured_at=now),
            captured_at=now,
            provider="test"
        )
        
        output = format_rate_limit_display(state)
        
        assert "Requests/min" in output
        assert "Tokens/hour" in output
        # Should not include empty buckets
        assert "Requests/hour" not in output
        assert "Tokens/min" not in output
    
    def test_format_progress_bar(self):
        """Test that progress bar is rendered correctly."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=100, remaining=50, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=now,
            provider="test"
        )
        
        output = format_rate_limit_display(state)
        
        # Should have filled and unfilled characters
        assert "█" in output
        assert "░" in output
        assert "50.0%" in output


class TestFormatRateLimitCompact:
    """Tests for format_rate_limit_compact function."""
    
    def test_compact_format_basic(self):
        """Test compact format with basic data."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=30, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=now,
            provider="anthropic"
        )
        
        output = format_rate_limit_compact(state)
        
        assert "anthropic" in output
        assert "req/min" in output
        assert "40%" in output  # 20/50 = 40%
        assert "30 left" in output
    
    def test_compact_format_with_warning(self):
        """Test compact format includes warning marker when usage high."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=5, reset_seconds=60.0, captured_at=now),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=now,
            provider="openai"
        )
        
        output = format_rate_limit_compact(state)
        
        assert "⚠️" in output
        assert "90%" in output
    
    def test_compact_format_no_data(self):
        """Test compact format when no data available."""
        state = RateLimitState(
            requests_min=RateLimitBucket(),
            requests_hour=RateLimitBucket(),
            tokens_min=RateLimitBucket(),
            tokens_hour=RateLimitBucket(),
            captured_at=time.time(),
            provider="unknown"
        )
        
        output = format_rate_limit_compact(state)
        
        assert output == "No rate limits"
    
    def test_compact_format_shows_most_constrained(self):
        """Test compact format shows the bucket with highest usage."""
        now = time.time()
        state = RateLimitState(
            requests_min=RateLimitBucket(limit=50, remaining=40, reset_seconds=60.0, captured_at=now),  # 20% used
            requests_hour=RateLimitBucket(limit=1000, remaining=100, reset_seconds=3600.0, captured_at=now),  # 90% used
            tokens_min=RateLimitBucket(limit=40000, remaining=30000, reset_seconds=60.0, captured_at=now),  # 25% used
            tokens_hour=RateLimitBucket(),
            captured_at=now,
            provider="test"
        )
        
        output = format_rate_limit_compact(state)
        
        # Should show requests_hour since it has highest usage (90%)
        assert "req/hr" in output
        assert "90%" in output
        assert "100 left" in output
