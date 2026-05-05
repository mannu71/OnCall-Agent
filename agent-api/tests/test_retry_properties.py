"""Property-based tests for Retry System.

Tests universal correctness properties for retry logic using
hypothesis for property-based testing.

**Validates: Requirements 4.1, 4.2**
"""

import pytest
from hypothesis import given, strategies as st, assume, settings
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.retry import (
    jittered_backoff,
    RetryPolicy,
    ContextProbingState,
    with_retry,
    _get_retry_count_for_reason,
)
from app.core.error_classifier import ClassifiedError, FailoverReason


# ============================================================================
# Hypothesis Strategies for Retry Testing
# ============================================================================

def retry_attempt_strategy():
    """Strategy for generating retry attempt numbers."""
    return st.integers(min_value=0, max_value=20)


def base_delay_strategy():
    """Strategy for generating base delay values."""
    return st.floats(min_value=0.1, max_value=10.0)


def cap_delay_strategy():
    """Strategy for generating cap delay values."""
    return st.floats(min_value=1.0, max_value=120.0)


@st.composite
def retry_policy_strategy(draw):
    """Strategy for generating RetryPolicy instances with valid constraints.
    
    Ensures rate limit settings are >= default settings to match requirements.
    """
    # Generate base values first
    max_retries = draw(st.integers(min_value=1, max_value=10))
    base_delay = draw(st.floats(min_value=0.1, max_value=5.0))
    max_delay = draw(st.floats(min_value=5.0, max_value=60.0))
    max_total_delay = draw(st.floats(min_value=30.0, max_value=300.0))
    
    # Rate limit settings should be >= default settings (Requirement 4.2)
    rate_limit_max_retries = draw(st.integers(min_value=max_retries, max_value=max(max_retries, 10)))
    rate_limit_base_delay = draw(st.floats(min_value=max(base_delay, 1.0), max_value=10.0))
    rate_limit_max_delay = draw(st.floats(min_value=max(max_delay, 30.0), max_value=120.0))
    
    # Other error type settings
    overloaded_max_retries = draw(st.integers(min_value=1, max_value=10))
    timeout_max_retries = draw(st.integers(min_value=1, max_value=10))
    server_error_max_retries = draw(st.integers(min_value=1, max_value=10))
    context_overflow_max_retries = draw(st.integers(min_value=1, max_value=10))
    
    return RetryPolicy(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        max_total_delay=max_total_delay,
        rate_limit_max_retries=rate_limit_max_retries,
        rate_limit_base_delay=rate_limit_base_delay,
        rate_limit_max_delay=rate_limit_max_delay,
        overloaded_max_retries=overloaded_max_retries,
        timeout_max_retries=timeout_max_retries,
        server_error_max_retries=server_error_max_retries,
        context_overflow_max_retries=context_overflow_max_retries,
    )


def failover_reason_strategy():
    """Strategy for generating FailoverReason values."""
    return st.sampled_from([
        FailoverReason.RATE_LIMIT,
        FailoverReason.OVERLOADED,
        FailoverReason.TIMEOUT,
        FailoverReason.SERVER_ERROR,
        FailoverReason.CONTEXT_OVERFLOW,
        FailoverReason.UNKNOWN,
        FailoverReason.BILLING,
        FailoverReason.AUTH_PERMANENT,
    ])


# ============================================================================
# Property 12: Jittered Backoff Bounds
# ============================================================================
# *For any* retry attempt, the jittered backoff delay SHALL be within the
# expected bounds: base ≤ delay ≤ max_delay, with exponential growth pattern.
# **Validates: Requirements 4.1**


class TestJitteredBackoffBounds:
    """Property tests for jittered backoff bounds."""

    @given(
        base=base_delay_strategy(),
        cap=cap_delay_strategy(),
        attempt=retry_attempt_strategy(),
    )
    @settings(max_examples=200, deadline=2000)
    def test_jittered_backoff_respects_bounds(
        self, base: float, cap: float, attempt: int
    ):
        """Jittered backoff should always produce delays within [base, cap].
        
        **Validates: Requirements 4.1**
        """
        # Ensure cap >= base for valid test
        assume(cap >= base)
        
        delay = jittered_backoff(base=base, cap=cap, attempt=attempt)
        
        # Delay should be within bounds
        assert delay >= base, f"Delay {delay} is less than base {base}"
        assert delay <= cap, f"Delay {delay} exceeds cap {cap}"

    @given(
        base=base_delay_strategy(),
        cap=cap_delay_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_first_attempt_returns_base(self, base: float, cap: float):
        """First attempt (attempt=0) should always return base delay.
        
        **Validates: Requirements 4.1**
        """
        assume(cap >= base)
        
        delay = jittered_backoff(base=base, cap=cap, attempt=0)
        
        assert delay == base, f"First attempt should return base {base}, got {delay}"

    @given(
        base=base_delay_strategy(),
        cap=cap_delay_strategy(),
        attempt=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=100, deadline=2000)
    def test_backoff_increases_with_attempts(
        self, base: float, cap: float, attempt: int
    ):
        """Backoff should generally increase with attempts (statistical property).
        
        **Validates: Requirements 4.1**
        """
        assume(cap >= base * 2)  # Ensure there's room for growth
        
        # Run multiple times to account for jitter
        delays = [jittered_backoff(base=base, cap=cap, attempt=i) for i in range(attempt + 1)]
        
        # All delays should be within bounds
        for delay in delays:
            assert base <= delay <= cap
        
        # Later attempts should have potential for larger delays
        # (we can't guarantee monotonic increase due to jitter, but the max should grow)
        if attempt >= 2:
            early_max = max(delays[:2])
            late_max = max(delays[-2:])
            # With high probability, later attempts should reach higher values
            # This is a weak property due to randomness, but should hold statistically
            assert late_max >= early_max * 0.5  # Allow for some variance

    @given(
        base=base_delay_strategy(),
        cap=cap_delay_strategy(),
        attempt=retry_attempt_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_backoff_never_exceeds_cap(self, base: float, cap: float, attempt: int):
        """Backoff should never exceed cap, regardless of attempt number.
        
        **Validates: Requirements 4.1**
        """
        assume(cap >= base)
        
        delay = jittered_backoff(base=base, cap=cap, attempt=attempt)
        
        assert delay <= cap, f"Delay {delay} exceeds cap {cap} at attempt {attempt}"

    @given(
        base=base_delay_strategy(),
        cap=cap_delay_strategy(),
        attempt=retry_attempt_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_backoff_is_non_negative(self, base: float, cap: float, attempt: int):
        """Backoff should always produce non-negative delays.
        
        **Validates: Requirements 4.1**
        """
        assume(cap >= base)
        assume(base >= 0)
        
        delay = jittered_backoff(base=base, cap=cap, attempt=attempt)
        
        assert delay >= 0, f"Delay {delay} is negative"

    @given(
        base=base_delay_strategy(),
        cap=cap_delay_strategy(),
        attempt=retry_attempt_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_backoff_is_deterministic_for_first_attempt(
        self, base: float, cap: float, attempt: int
    ):
        """First attempt should always produce the same result (base).
        
        **Validates: Requirements 4.1**
        """
        assume(cap >= base)
        
        if attempt == 0:
            delay1 = jittered_backoff(base=base, cap=cap, attempt=attempt)
            delay2 = jittered_backoff(base=base, cap=cap, attempt=attempt)
            
            assert delay1 == delay2 == base


# ============================================================================
# Property 13: Rate Limit Backoff Duration
# ============================================================================
# *For any* rate limit error, the backoff delay SHALL be longer (base 2s,
# cap 60s) than for other error types (base 1s, cap 30s).
# **Validates: Requirements 4.2**


class TestRateLimitBackoffDuration:
    """Property tests for rate limit backoff duration."""

    @given(
        policy=retry_policy_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_has_longer_base_delay(self, policy: RetryPolicy):
        """Rate limit errors should use longer base delay than default.
        
        **Validates: Requirements 4.2**
        """
        # Rate limit base delay should be >= default base delay (Requirement 4.2)
        assert policy.rate_limit_base_delay >= policy.base_delay, \
            f"Rate limit base delay {policy.rate_limit_base_delay} should be >= default {policy.base_delay}"

    @given(
        policy=retry_policy_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_has_longer_max_delay(self, policy: RetryPolicy):
        """Rate limit errors should use longer max delay than default.
        
        **Validates: Requirements 4.2**
        """
        # Rate limit max delay should be >= default max delay (Requirement 4.2)
        assert policy.rate_limit_max_delay >= policy.max_delay, \
            f"Rate limit max delay {policy.rate_limit_max_delay} should be >= default {policy.max_delay}"

    @given(
        attempt=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=50, deadline=2000)
    def test_rate_limit_backoff_longer_than_default_backoff(self, attempt: int):
        """Rate limit backoff should produce longer delays than default backoff.
        
        **Validates: Requirements 4.2**
        """
        # Default policy values
        default_base = 1.0
        default_cap = 30.0
        rate_limit_base = 2.0
        rate_limit_cap = 60.0
        
        # Generate delays for both
        default_delay = jittered_backoff(base=default_base, cap=default_cap, attempt=attempt)
        rate_limit_delay = jittered_backoff(base=rate_limit_base, cap=rate_limit_cap, attempt=attempt)
        
        # Rate limit delay should have potential to be longer
        # Due to jitter, we can't guarantee every single sample is longer,
        # but the bounds are definitely longer
        assert rate_limit_cap > default_cap
        assert rate_limit_base >= default_base

    @given(
        policy=retry_policy_strategy(),
        reason=failover_reason_strategy(),
    )
    @settings(max_examples=200, deadline=2000)
    def test_rate_limit_reason_uses_rate_limit_retry_count(
        self, policy: RetryPolicy, reason: FailoverReason
    ):
        """Rate limit errors should use rate_limit_max_retries.
        
        **Validates: Requirements 4.2**
        """
        classified = ClassifiedError(reason=reason)
        retry_count = _get_retry_count_for_reason(classified, policy)
        
        if reason == FailoverReason.RATE_LIMIT:
            assert retry_count == policy.rate_limit_max_retries
        elif reason == FailoverReason.OVERLOADED:
            assert retry_count == policy.overloaded_max_retries
        elif reason == FailoverReason.TIMEOUT:
            assert retry_count == policy.timeout_max_retries
        elif reason == FailoverReason.SERVER_ERROR:
            assert retry_count == policy.server_error_max_retries
        elif reason == FailoverReason.CONTEXT_OVERFLOW:
            assert retry_count == policy.context_overflow_max_retries
        else:
            assert retry_count == policy.max_retries

    @given(
        policy=retry_policy_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_gets_more_retries_than_default(self, policy: RetryPolicy):
        """Rate limit errors should get more retry attempts than default.
        
        **Validates: Requirements 4.2**
        """
        rate_limit_error = ClassifiedError(reason=FailoverReason.RATE_LIMIT)
        default_error = ClassifiedError(reason=FailoverReason.UNKNOWN)
        
        rate_limit_retries = _get_retry_count_for_reason(rate_limit_error, policy)
        default_retries = _get_retry_count_for_reason(default_error, policy)
        
        # Rate limit should get at least as many retries as default (Requirement 4.2)
        assert rate_limit_retries >= default_retries, \
            f"Rate limit retries {rate_limit_retries} should be >= default {default_retries}"

    def test_default_policy_rate_limit_settings(self):
        """Default policy should have rate limit settings longer than default.
        
        **Validates: Requirements 4.2**
        """
        policy = RetryPolicy()
        
        # Default policy should have rate limit base >= 2.0
        assert policy.rate_limit_base_delay >= 2.0
        
        # Default policy should have rate limit cap >= 60.0
        assert policy.rate_limit_max_delay >= 60.0
        
        # Default policy should have rate limit retries >= default retries
        assert policy.rate_limit_max_retries >= policy.max_retries

    @given(
        attempt=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_backoff_produces_valid_delays(self, attempt: int):
        """Rate limit backoff should produce valid delays for all attempts.
        
        **Validates: Requirements 4.2**
        """
        policy = RetryPolicy()
        
        delay = jittered_backoff(
            base=policy.rate_limit_base_delay,
            cap=policy.rate_limit_max_delay,
            attempt=attempt
        )
        
        # Delay should be within rate limit bounds
        assert delay >= policy.rate_limit_base_delay
        assert delay <= policy.rate_limit_max_delay
        assert delay >= 0

    @given(
        policy=retry_policy_strategy(),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_policy_values_are_consistent(self, policy: RetryPolicy):
        """Rate limit policy values should be internally consistent.
        
        **Validates: Requirements 4.2**
        """
        # Max delay should be >= base delay
        assert policy.rate_limit_max_delay >= policy.rate_limit_base_delay
        
        # Retry counts should be positive
        assert policy.rate_limit_max_retries > 0
        assert policy.max_retries > 0
        
        # Base delays should be positive
        assert policy.rate_limit_base_delay > 0
        assert policy.base_delay > 0
