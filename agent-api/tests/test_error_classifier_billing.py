"""Unit tests for billing vs rate limit disambiguation in error classifier.

Tests for Requirements 3.2: Billing vs Rate Limit Disambiguation
"""

import pytest
from unittest.mock import MagicMock

from app.core.error_classifier import (
    classify_error,
    _classify_openai_error,
    _classify_anthropic_error,
    ClassifiedError,
    FailoverReason,
)


class MockOpenAIError(Exception):
    """Mock OpenAI error for testing."""
    def __init__(self, message: str, error_type: str = "RateLimitError", status_code: int = None):
        super().__init__(message)
        self.__class__.__name__ = error_type
        self.__class__.__module__ = "openai.error"
        if status_code:
            self.status_code = status_code


class MockAnthropicError(Exception):
    """Mock Anthropic error for testing."""
    def __init__(self, message: str, error_type: str = "RateLimitError", status_code: int = None):
        super().__init__(message)
        self.__class__.__name__ = error_type
        self.__class__.__module__ = "anthropic.error"
        if status_code:
            self.status_code = status_code


class TestBillingVsRateLimitDisambiguation:
    """Tests for billing vs rate limit disambiguation."""

    def test_openai_billing_pattern_in_rate_limit_error(self):
        """RateLimitError with billing pattern should classify as BILLING."""
        error = MockOpenAIError("Error: insufficient credits to complete request")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True
        assert result.should_fallback is True

    def test_openai_pure_rate_limit_error(self):
        """RateLimitError without billing pattern should classify as RATE_LIMIT."""
        error = MockOpenAIError("Error: rate limit exceeded, try again in 30 seconds")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True
        assert result.should_fallback is True

    def test_openai_usage_limit_with_transient_signals(self):
        """Usage limit with transient signals should classify as RATE_LIMIT."""
        error = MockOpenAIError("Error: usage limit exceeded, try again after reset")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True

    def test_openai_usage_limit_without_transient_signals(self):
        """Usage limit without transient signals should classify as BILLING."""
        error = MockOpenAIError("Error: quota exceeded for this key")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True

    def test_openai_429_with_billing_pattern(self):
        """429 status with billing pattern should classify as BILLING."""
        error = MockOpenAIError(
            "Error: exceeded your current quota, please check your plan",
            error_type="APIStatusError",
            status_code=429
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.status_code == 429
        assert result.retryable is False
        assert result.should_rotate_credential is True

    def test_openai_429_without_billing_pattern(self):
        """429 status without billing pattern should classify as RATE_LIMIT."""
        error = MockOpenAIError(
            "Error: too many requests, please retry after 60 seconds",
            error_type="APIStatusError",
            status_code=429
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.status_code == 429
        assert result.retryable is True

    def test_openai_402_status_code(self):
        """402 status code should classify as BILLING."""
        error = MockOpenAIError(
            "Payment required",
            error_type="APIStatusError",
            status_code=402
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.status_code == 402
        assert result.retryable is False

    def test_anthropic_billing_pattern_in_rate_limit_error(self):
        """Anthropic RateLimitError with billing pattern should classify as BILLING."""
        error = MockAnthropicError("Error: credits have been exhausted")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True

    def test_anthropic_pure_rate_limit_error(self):
        """Anthropic RateLimitError without billing pattern should classify as RATE_LIMIT."""
        error = MockAnthropicError("Error: rate limit exceeded, please wait")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True

    def test_anthropic_usage_limit_with_transient_signals(self):
        """Anthropic usage limit with transient signals should classify as RATE_LIMIT."""
        error = MockAnthropicError("Error: usage limit exceeded, resets at midnight")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True

    def test_anthropic_usage_limit_without_transient_signals(self):
        """Anthropic usage limit without transient signals should classify as BILLING."""
        error = MockAnthropicError("Error: quota exceeded")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False

    def test_anthropic_429_with_billing_pattern(self):
        """Anthropic 429 with billing pattern should classify as BILLING."""
        error = MockAnthropicError(
            "Error: billing hard limit reached",
            error_type="APIStatusError",
            status_code=429
        )
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.status_code == 429
        assert result.retryable is False

    def test_anthropic_429_without_billing_pattern(self):
        """Anthropic 429 without billing pattern should classify as RATE_LIMIT."""
        error = MockAnthropicError(
            "Error: too many requests",
            error_type="APIStatusError",
            status_code=429
        )
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.status_code == 429
        assert result.retryable is True


class TestBillingPatternExamples:
    """Test specific billing pattern examples."""

    @pytest.mark.parametrize("message", [
        "insufficient credits",
        "insufficient_quota",
        "credit balance too low",
        "credits have been exhausted",
        "top up your credits",
        "payment required",
        "billing hard limit",
        "exceeded your current quota",
        "account is deactivated",
        "plan does not include",
    ])
    def test_billing_patterns_detected(self, message):
        """All billing patterns should be detected."""
        error = MockOpenAIError(f"Error: {message}")
        result = _classify_openai_error(error)
        assert result.reason == FailoverReason.BILLING

    @pytest.mark.parametrize("message", [
        "rate limit exceeded, try again in 30s",
        "too many requests, retry after reset",
        "usage limit exceeded, resets at midnight",
        "quota exceeded, wait 60 seconds",
    ])
    def test_transient_rate_limit_patterns(self, message):
        """Rate limit patterns with transient signals should classify as RATE_LIMIT."""
        error = MockOpenAIError(f"Error: {message}")
        result = _classify_openai_error(error)
        assert result.reason == FailoverReason.RATE_LIMIT
