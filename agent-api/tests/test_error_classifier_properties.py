"""Property-based tests for Error Classifier.

Tests universal correctness properties for error classification using
hypothesis for property-based testing.

**Validates: Requirements 3.1, 3.2, 3.3, 3.6, 3.7**
"""

import pytest
from hypothesis import given, strategies as st, assume, settings
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from app.core.error_classifier import (
    classify_error,
    _classify_openai_error,
    _classify_anthropic_error,
    ClassifiedError,
    FailoverReason,
    _BILLING_PATTERNS,
    _RATE_LIMIT_PATTERNS,
    _USAGE_LIMIT_PATTERNS,
    _USAGE_LIMIT_TRANSIENT_SIGNALS,
    _CONTEXT_OVERFLOW_PATTERNS,
    _MODEL_NOT_FOUND_PATTERNS,
    _AUTH_PATTERNS,
)


# ============================================================================
# Mock Error Classes
# ============================================================================

class MockOpenAIError(Exception):
    """Mock OpenAI error for testing."""
    def __init__(self, message: str, error_type: str = "RateLimitError", status_code: Optional[int] = None):
        super().__init__(message)
        self.__class__.__name__ = error_type
        self.__class__.__module__ = "openai.error"
        if status_code is not None:
            self.status_code = status_code


class MockAnthropicError(Exception):
    """Mock Anthropic error for testing."""
    def __init__(self, message: str, error_type: str = "RateLimitError", status_code: Optional[int] = None):
        super().__init__(message)
        self.__class__.__name__ = error_type
        self.__class__.__module__ = "anthropic.error"
        if status_code is not None:
            self.status_code = status_code


# ============================================================================
# Hypothesis Strategies for Error Generation
# ============================================================================

def error_message_strategy():
    """Strategy for generating error messages."""
    return st.text(min_size=10, max_size=200)


def billing_pattern_strategy():
    """Strategy for generating billing-related error messages."""
    return st.sampled_from(_BILLING_PATTERNS).map(
        lambda pattern: f"Error: {pattern} - please update your payment method"
    )


def rate_limit_pattern_strategy():
    """Strategy for generating rate limit error messages."""
    return st.sampled_from(_RATE_LIMIT_PATTERNS).map(
        lambda pattern: f"Error: {pattern} - try again in 30 seconds"
    )


def usage_limit_with_transient_strategy():
    """Strategy for usage limit messages with transient signals."""
    usage_pattern = st.sampled_from(_USAGE_LIMIT_PATTERNS)
    transient_signal = st.sampled_from(_USAGE_LIMIT_TRANSIENT_SIGNALS)
    return st.builds(
        lambda u, t: f"Error: {u} - {t}",
        usage_pattern,
        transient_signal,
    )


def usage_limit_without_transient_strategy():
    """Strategy for usage limit messages without transient signals."""
    return st.sampled_from(_USAGE_LIMIT_PATTERNS).map(
        lambda pattern: f"Error: {pattern} for your account"
    )


def context_overflow_pattern_strategy():
    """Strategy for generating context overflow error messages."""
    return st.sampled_from(_CONTEXT_OVERFLOW_PATTERNS).map(
        lambda pattern: f"Error: {pattern} exceeded - reduce input size"
    )


def model_not_found_pattern_strategy():
    """Strategy for generating model not found error messages."""
    return st.sampled_from(_MODEL_NOT_FOUND_PATTERNS).map(
        lambda pattern: f"Error: {pattern}"
    )


def auth_pattern_strategy():
    """Strategy for generating auth error messages."""
    return st.sampled_from(_AUTH_PATTERNS).map(
        lambda pattern: f"Error: {pattern}"
    )


def http_status_code_strategy():
    """Strategy for generating HTTP status codes."""
    return st.sampled_from([400, 401, 402, 403, 404, 429, 500, 502, 503, 529])


def provider_strategy():
    """Strategy for generating provider names."""
    return st.sampled_from(["openai", "anthropic", "bedrock", "openrouter"])


# ============================================================================
# Property 7: Error Classification Uniqueness
# ============================================================================
# *For any* API error, the ErrorClassifier SHALL classify it into exactly
# one FailoverReason from the defined taxonomy.
# **Validates: Requirements 3.1**


class TestErrorClassificationUniqueness:
    """Property tests for error classification uniqueness."""

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError", "APIConnectionError", "APITimeoutError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_openai_error_maps_to_exactly_one_reason(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """Each OpenAI error should map to exactly one FailoverReason.
        
        **Validates: Requirements 3.1**
        """
        error = MockOpenAIError(message, error_type, status_code)
        result = _classify_openai_error(error)
        
        # Should return a ClassifiedError with a valid FailoverReason
        assert isinstance(result, ClassifiedError)
        assert isinstance(result.reason, FailoverReason)
        
        # The reason should be one of the defined enum values
        assert result.reason in FailoverReason

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError", "APIConnectionError", "APITimeoutError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_anthropic_error_maps_to_exactly_one_reason(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """Each Anthropic error should map to exactly one FailoverReason.
        
        **Validates: Requirements 3.1**
        """
        error = MockAnthropicError(message, error_type, status_code)
        result = _classify_anthropic_error(error)
        
        # Should return a ClassifiedError with a valid FailoverReason
        assert isinstance(result, ClassifiedError)
        assert isinstance(result.reason, FailoverReason)
        
        # The reason should be one of the defined enum values
        assert result.reason in FailoverReason


# ============================================================================
# Property 8: Billing vs Rate Limit Disambiguation
# ============================================================================
# *For any* error message containing usage limit patterns, the ErrorClassifier
# SHALL correctly distinguish between billing exhaustion (permanent) and
# transient rate limits based on the presence of transient signals.
# **Validates: Requirements 3.2**


class TestBillingVsRateLimitDisambiguation:
    """Property tests for billing vs rate limit disambiguation."""

    @given(message=billing_pattern_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_billing_patterns_always_classify_as_billing_openai(self, message: str):
        """Billing patterns should always classify as BILLING for OpenAI.
        
        **Validates: Requirements 3.2**
        """
        error = MockOpenAIError(message, "RateLimitError")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True
        assert result.should_fallback is True

    @given(message=billing_pattern_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_billing_patterns_always_classify_as_billing_anthropic(self, message: str):
        """Billing patterns should always classify as BILLING for Anthropic.
        
        **Validates: Requirements 3.2**
        """
        error = MockAnthropicError(message, "RateLimitError")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True
        assert result.should_fallback is True

    @given(message=rate_limit_pattern_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_rate_limit_patterns_classify_as_rate_limit_openai(self, message: str):
        """Rate limit patterns should classify as RATE_LIMIT for OpenAI.
        
        **Validates: Requirements 3.2**
        """
        error = MockOpenAIError(message, "RateLimitError")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True
        assert result.should_fallback is True

    @given(message=rate_limit_pattern_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_rate_limit_patterns_classify_as_rate_limit_anthropic(self, message: str):
        """Rate limit patterns should classify as RATE_LIMIT for Anthropic.
        
        **Validates: Requirements 3.2**
        """
        error = MockAnthropicError(message, "RateLimitError")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True
        assert result.should_fallback is True

    @given(message=usage_limit_with_transient_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_usage_limit_with_transient_signals_is_rate_limit_openai(self, message: str):
        """Usage limit with transient signals should classify as RATE_LIMIT for OpenAI.
        
        **Validates: Requirements 3.2**
        """
        error = MockOpenAIError(message, "RateLimitError")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True

    @given(message=usage_limit_with_transient_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_usage_limit_with_transient_signals_is_rate_limit_anthropic(self, message: str):
        """Usage limit with transient signals should classify as RATE_LIMIT for Anthropic.
        
        **Validates: Requirements 3.2**
        """
        error = MockAnthropicError(message, "RateLimitError")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True

    @given(message=usage_limit_without_transient_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_usage_limit_without_transient_signals_is_billing_openai(self, message: str):
        """Usage limit without transient signals should classify as BILLING for OpenAI.
        
        **Validates: Requirements 3.2**
        """
        error = MockOpenAIError(message, "RateLimitError")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True

    @given(message=usage_limit_without_transient_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_usage_limit_without_transient_signals_is_billing_anthropic(self, message: str):
        """Usage limit without transient signals should classify as BILLING for Anthropic.
        
        **Validates: Requirements 3.2**
        """
        error = MockAnthropicError(message, "RateLimitError")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_rotate_credential is True


# ============================================================================
# Property 9: Context Overflow Detection
# ============================================================================
# *For any* error message from Anthropic, OpenAI, vLLM, Ollama, or llama.cpp
# containing context-related patterns, the ErrorClassifier SHALL classify it
# as CONTEXT_OVERFLOW.
# **Validates: Requirements 3.3**


class TestContextOverflowDetection:
    """Property tests for context overflow detection."""

    @given(message=context_overflow_pattern_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_context_overflow_patterns_detected_openai(self, message: str):
        """Context overflow patterns should be detected for OpenAI.
        
        **Validates: Requirements 3.3**
        """
        error = MockOpenAIError(message, "BadRequestError")
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        assert result.retryable is False
        assert result.should_compress is True

    @given(message=context_overflow_pattern_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_context_overflow_patterns_detected_anthropic(self, message: str):
        """Context overflow patterns should be detected for Anthropic.
        
        **Validates: Requirements 3.3**
        """
        error = MockAnthropicError(message, "BadRequestError")
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        assert result.retryable is False
        assert result.should_compress is True


# ============================================================================
# Property 10: Large Session Disconnect Classification
# ============================================================================
# *For any* server disconnect error on a session with >60% context usage or
# >120K tokens or >200 messages, the ErrorClassifier SHALL classify it as
# CONTEXT_OVERFLOW rather than TIMEOUT.
# **Validates: Requirements 3.6**


class TestLargeSessionDisconnectClassification:
    """Property tests for large session disconnect classification.
    
    Note: This property requires context about the session state (token count,
    message count, context usage). The current implementation doesn't have
    this context passed to classify_error(), so this property cannot be
    fully tested without enhancing the API.
    
    For now, we test that server disconnect errors are classified consistently.
    """

    @given(
        error_type=st.sampled_from([
            "APIConnectionError", "APITimeoutError", "ServerDisconnectedError"
        ])
    )
    @settings(max_examples=50, deadline=2000)
    def test_server_disconnect_errors_classified_consistently(self, error_type: str):
        """Server disconnect errors should be classified consistently.
        
        **Validates: Requirements 3.6 (partial)**
        
        Note: Full validation requires session context (tokens, messages).
        """
        error = MockOpenAIError("server disconnected", error_type)
        result = _classify_openai_error(error)
        
        # Should classify as either TIMEOUT or UNKNOWN (not crash)
        assert isinstance(result, ClassifiedError)
        assert result.reason in [FailoverReason.TIMEOUT, FailoverReason.UNKNOWN]


# ============================================================================
# Property 11: Recovery Hint Consistency
# ============================================================================
# *For any* classified error, the recovery hints (retryable, should_compress,
# should_rotate_credential, should_fallback) SHALL be set consistently based
# on the FailoverReason.
# **Validates: Requirements 3.7**


class TestRecoveryHintConsistency:
    """Property tests for recovery hint consistency."""

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_billing_errors_have_consistent_hints_openai(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """BILLING errors should have consistent recovery hints for OpenAI.
        
        **Validates: Requirements 3.7**
        """
        # Create error with billing pattern
        billing_message = f"{message} insufficient credits"
        error = MockOpenAIError(billing_message, error_type, status_code)
        result = _classify_openai_error(error)
        
        if result.reason == FailoverReason.BILLING:
            assert result.retryable is False
            assert result.should_rotate_credential is True
            assert result.should_fallback is True
            assert result.should_compress is False

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_errors_have_consistent_hints_openai(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """RATE_LIMIT errors should have consistent recovery hints for OpenAI.
        
        **Validates: Requirements 3.7**
        """
        # Create error with rate limit pattern
        rate_limit_message = f"{message} rate limit exceeded, try again"
        error = MockOpenAIError(rate_limit_message, error_type, status_code)
        result = _classify_openai_error(error)
        
        if result.reason == FailoverReason.RATE_LIMIT:
            assert result.retryable is True
            assert result.should_fallback is True
            # should_rotate_credential can be True or False
            assert result.should_compress is False

    @given(
        message=error_message_strategy(),
    )
    @settings(max_examples=50, deadline=2000)
    def test_context_overflow_errors_have_consistent_hints_openai(self, message: str):
        """CONTEXT_OVERFLOW errors should have consistent recovery hints for OpenAI.
        
        **Validates: Requirements 3.7**
        """
        # Create error with context overflow pattern
        context_message = f"{message} context length exceeded"
        error = MockOpenAIError(context_message, "BadRequestError")
        result = _classify_openai_error(error)
        
        if result.reason == FailoverReason.CONTEXT_OVERFLOW:
            assert result.retryable is False
            assert result.should_compress is True
            assert result.should_rotate_credential is False
            assert result.should_fallback is False

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_auth_errors_have_consistent_hints_openai(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """AUTH errors should have consistent recovery hints for OpenAI.
        
        **Validates: Requirements 3.7**
        """
        error = MockOpenAIError(message, "AuthenticationError", status_code)
        result = _classify_openai_error(error)
        
        if result.reason == FailoverReason.AUTH_PERMANENT:
            assert result.retryable is False
            assert result.should_fallback is True
            # Other hints can vary

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_billing_errors_have_consistent_hints_anthropic(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """BILLING errors should have consistent recovery hints for Anthropic.
        
        **Validates: Requirements 3.7**
        """
        # Create error with billing pattern
        billing_message = f"{message} credits have been exhausted"
        error = MockAnthropicError(billing_message, error_type, status_code)
        result = _classify_anthropic_error(error)
        
        if result.reason == FailoverReason.BILLING:
            assert result.retryable is False
            assert result.should_rotate_credential is True
            assert result.should_fallback is True
            assert result.should_compress is False

    @given(
        message=error_message_strategy(),
        error_type=st.sampled_from([
            "RateLimitError", "AuthenticationError", "BadRequestError",
            "APIStatusError"
        ]),
        status_code=st.one_of(st.none(), http_status_code_strategy()),
    )
    @settings(max_examples=100, deadline=2000)
    def test_rate_limit_errors_have_consistent_hints_anthropic(
        self, message: str, error_type: str, status_code: Optional[int]
    ):
        """RATE_LIMIT errors should have consistent recovery hints for Anthropic.
        
        **Validates: Requirements 3.7**
        """
        # Create error with rate limit pattern
        rate_limit_message = f"{message} rate limit exceeded, please wait"
        error = MockAnthropicError(rate_limit_message, error_type, status_code)
        result = _classify_anthropic_error(error)
        
        if result.reason == FailoverReason.RATE_LIMIT:
            assert result.retryable is True
            assert result.should_fallback is True
            # should_rotate_credential can be True or False
            assert result.should_compress is False

    @given(
        message=error_message_strategy(),
    )
    @settings(max_examples=50, deadline=2000)
    def test_context_overflow_errors_have_consistent_hints_anthropic(self, message: str):
        """CONTEXT_OVERFLOW errors should have consistent recovery hints for Anthropic.
        
        **Validates: Requirements 3.7**
        """
        # Create error with context overflow pattern
        context_message = f"{message} maximum context length exceeded"
        error = MockAnthropicError(context_message, "BadRequestError")
        result = _classify_anthropic_error(error)
        
        if result.reason == FailoverReason.CONTEXT_OVERFLOW:
            assert result.retryable is False
            assert result.should_compress is True
            assert result.should_rotate_credential is False
            assert result.should_fallback is False
