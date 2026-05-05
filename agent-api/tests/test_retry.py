"""Unit tests for retry module.

Tests for jittered backoff, retry policies, and context probing integration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.retry import (
    jittered_backoff,
    RetryPolicy,
    ContextProbingState,
    with_retry,
    _get_retry_count_for_reason,
)
from app.core.error_classifier import ClassifiedError, FailoverReason


class TestJitteredBackoff:
    """Tests for jittered_backoff function."""

    def test_first_attempt_returns_base(self):
        """First attempt should return base delay."""
        result = jittered_backoff(base=1.0, cap=30.0, attempt=0)
        assert result == 1.0

    def test_backoff_increases_with_attempts(self):
        """Backoff should generally increase with attempts."""
        # Run multiple times to account for jitter
        results = [jittered_backoff(base=1.0, cap=30.0, attempt=i) for i in range(5)]
        # Each result should be <= cap
        for r in results:
            assert r <= 30.0
        # Later attempts should generally be larger (with high probability)
        assert results[4] >= results[0]

    def test_backoff_respects_cap(self):
        """Backoff should never exceed cap."""
        for attempt in range(10):
            result = jittered_backoff(base=1.0, cap=10.0, attempt=attempt)
            assert result <= 10.0


class TestRetryPolicy:
    """Tests for RetryPolicy dataclass."""

    def test_default_values(self):
        """Should have sensible default values."""
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.rate_limit_max_retries == 5
        assert policy.context_overflow_max_retries == 5
        assert policy.rate_limit_base_delay == 2.0
        assert policy.rate_limit_max_delay == 60.0
        assert policy.max_total_delay == 120.0

    def test_custom_values(self):
        """Should allow custom values."""
        policy = RetryPolicy(
            max_retries=10,
            rate_limit_max_retries=20,
            rate_limit_base_delay=3.0,
            rate_limit_max_delay=90.0,
            max_total_delay=180.0,
        )
        assert policy.max_retries == 10
        assert policy.rate_limit_max_retries == 20
        assert policy.rate_limit_base_delay == 3.0
        assert policy.rate_limit_max_delay == 90.0
        assert policy.max_total_delay == 180.0


class TestContextProbingState:
    """Tests for ContextProbingState dataclass."""

    def test_initialization(self):
        """Should initialize with default values."""
        state = ContextProbingState(current_context_length=128_000)
        assert state.current_context_length == 128_000
        assert state.probed_lengths == []

    def test_record_probe(self):
        """Should record probed lengths."""
        state = ContextProbingState(current_context_length=128_000)
        state.record_probe(64_000)
        assert 64_000 in state.probed_lengths
        state.record_probe(32_000)
        assert 32_000 in state.probed_lengths


class TestGetRetryCountForReason:
    """Tests for _get_retry_count_for_reason function."""

    def test_rate_limit_retry_count(self):
        """Rate limit should use rate_limit_max_retries."""
        policy = RetryPolicy(rate_limit_max_retries=10)
        classified = ClassifiedError(reason=FailoverReason.RATE_LIMIT)
        result = _get_retry_count_for_reason(classified, policy)
        assert result == 10

    def test_context_overflow_retry_count(self):
        """Context overflow should use context_overflow_max_retries."""
        policy = RetryPolicy(context_overflow_max_retries=7)
        classified = ClassifiedError(reason=FailoverReason.CONTEXT_OVERFLOW)
        result = _get_retry_count_for_reason(classified, policy)
        assert result == 7

    def test_unknown_reason_uses_default(self):
        """Unknown reason should use default max_retries."""
        policy = RetryPolicy(max_retries=5)
        classified = ClassifiedError(reason=FailoverReason.UNKNOWN)
        result = _get_retry_count_for_reason(classified, policy)
        assert result == 5


class TestWithRetry:
    """Tests for with_retry function."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Should not retry on success."""
        call_count = 0

        async def success_fn():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await with_retry(success_fn)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self):
        """Should retry on transient errors."""
        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("temporary error")
            return "success"

        with patch("app.core.retry.classify_error") as mock_classify:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
            )
            result = await with_retry(flaky_fn, max_retries=5)
            assert result == "success"
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_context_probing_on_overflow(self):
        """Should trigger context probing on context overflow."""
        call_count = 0
        probed_lengths = []

        async def overflow_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("context length exceeded")
            return "success"

        async def on_probing(length: int):
            probed_lengths.append(length)

        state = ContextProbingState(current_context_length=128_000)

        with patch("app.core.retry.classify_error") as mock_classify:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.CONTEXT_OVERFLOW,
                retryable=True,
                should_compress=True,
            )
            result = await with_retry(
                overflow_fn,
                max_retries=5,
                on_context_probing=on_probing,
                context_probing_state=state,
            )
            assert result == "success"
            assert len(probed_lengths) > 0
            assert state.current_context_length < 128_000

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self):
        """Should raise immediately for non-retryable errors."""

        async def fail_fn():
            raise Exception("auth error")

        with patch("app.core.retry.classify_error") as mock_classify:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.AUTH_PERMANENT,
                retryable=False,
            )
            with pytest.raises(Exception, match="auth error"):
                await with_retry(fail_fn, max_retries=5)

    @pytest.mark.asyncio
    async def test_on_retry_callback_called(self):
        """Should call on_retry callback before each retry."""
        call_count = 0
        retry_calls = []

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("temporary error")
            return "success"

        async def on_retry(attempt: int, classified: ClassifiedError):
            retry_calls.append((attempt, classified.reason))

        with patch("app.core.retry.classify_error") as mock_classify:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
            )
            result = await with_retry(flaky_fn, max_retries=5, on_retry=on_retry)
            assert result == "success"
            assert len(retry_calls) == 2  # 2 retries before success

    @pytest.mark.asyncio
    async def test_rate_limit_uses_specific_backoff(self):
        """Should use rate limit specific backoff settings."""
        call_count = 0
        wait_times = []

        async def rate_limited_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("rate limit exceeded")
            return "success"

        with patch("app.core.retry.classify_error") as mock_classify, \
             patch("app.core.retry.asyncio.sleep") as mock_sleep:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                retryable=True,
            )
            
            async def capture_sleep(duration):
                wait_times.append(duration)
            
            mock_sleep.side_effect = capture_sleep
            
            policy = RetryPolicy(
                rate_limit_base_delay=2.0,
                rate_limit_max_delay=60.0,
            )
            result = await with_retry(rate_limited_fn, policy=policy)
            assert result == "success"
            # Verify wait times are within rate limit bounds
            for wait in wait_times:
                assert wait >= 2.0  # base delay
                assert wait <= 60.0  # max delay

    @pytest.mark.asyncio
    async def test_total_delay_enforcement(self):
        """Should enforce max_total_delay limit."""
        call_count = 0

        async def slow_retry_fn():
            nonlocal call_count
            call_count += 1
            raise Exception("persistent error")

        with patch("app.core.retry.classify_error") as mock_classify, \
             patch("app.core.retry.jittered_backoff") as mock_backoff:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
            )
            # Return large delays to exceed max_total_delay
            mock_backoff.return_value = 50.0
            
            policy = RetryPolicy(
                max_retries=10,
                max_total_delay=100.0,
            )
            
            with pytest.raises(Exception, match="persistent error"):
                await with_retry(slow_retry_fn, policy=policy)
            
            # Should stop before max_retries due to total delay limit
            assert call_count < 10

    @pytest.mark.asyncio
    async def test_policy_parameter_overrides_max_retries(self):
        """Should use policy parameter instead of max_retries when provided."""
        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise Exception("temporary error")
            return "success"

        with patch("app.core.retry.classify_error") as mock_classify:
            mock_classify.return_value = ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
            )
            
            policy = RetryPolicy(max_retries=10)
            result = await with_retry(
                flaky_fn,
                max_retries=2,  # This should be ignored
                policy=policy,
            )
            assert result == "success"
            assert call_count == 4

    @pytest.mark.asyncio
    async def test_error_type_specific_retry_counts(self):
        """Should use error-type-specific retry counts."""
        # Test rate limit gets more retries
        policy = RetryPolicy(
            max_retries=3,
            rate_limit_max_retries=5,
        )
        
        rate_limit_error = ClassifiedError(reason=FailoverReason.RATE_LIMIT)
        assert _get_retry_count_for_reason(rate_limit_error, policy) == 5
        
        server_error = ClassifiedError(reason=FailoverReason.SERVER_ERROR)
        assert _get_retry_count_for_reason(server_error, policy) == 3
