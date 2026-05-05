"""Tests for generic error classification for unknown providers.

Tests that errors from unknown providers (vLLM, Ollama, llama.cpp, etc.)
are correctly classified using the generic error classifier.
"""
import pytest
from app.core.error_classifier import classify_error, FailoverReason


class GenericAPIError(Exception):
    """Generic API error for testing unknown providers."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class TestGenericErrorClassifier:
    """Test generic error classification for unknown providers."""
    
    def test_vllm_context_overflow_via_generic_classifier(self):
        """vLLM context overflow should be detected by generic classifier."""
        error = GenericAPIError(
            "prompt length 150000 exceeds the max_model_len 128000",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        assert result.should_compress is True
    
    def test_ollama_context_overflow_via_generic_classifier(self):
        """Ollama context overflow should be detected by generic classifier."""
        error = GenericAPIError(
            "context length exceeded, truncating input",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
    
    def test_llama_cpp_context_overflow_via_generic_classifier(self):
        """llama.cpp context overflow should be detected by generic classifier."""
        error = GenericAPIError(
            "slot context: 8192 tokens, prompt 10000 tokens",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
    
    def test_generic_rate_limit_pattern(self):
        """Generic rate limit patterns should be detected."""
        error = GenericAPIError(
            "rate limit exceeded",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True
    
    def test_generic_auth_pattern(self):
        """Generic auth patterns should be detected."""
        error = GenericAPIError(
            "invalid api key",
            status_code=401
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.AUTH_PERMANENT
        assert result.should_fallback is True
    
    def test_generic_model_not_found_pattern(self):
        """Generic model not found patterns should be detected."""
        error = GenericAPIError(
            "model not found",
            status_code=404
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.MODEL_NOT_FOUND
        assert result.should_fallback is True
    
    def test_generic_server_error(self):
        """Generic 500 errors should be classified as SERVER_ERROR."""
        error = GenericAPIError(
            "internal server error",
            status_code=500
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.SERVER_ERROR
        assert result.retryable is True
    
    def test_generic_overloaded(self):
        """Generic 503 errors should be classified as OVERLOADED."""
        error = GenericAPIError(
            "service unavailable",
            status_code=503
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.OVERLOADED
        assert result.retryable is True
    
    def test_generic_bad_request(self):
        """Generic 400 errors without patterns should be FORMAT_ERROR."""
        error = GenericAPIError(
            "bad request",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.FORMAT_ERROR
        assert result.retryable is False
    
    def test_unknown_error_without_status_code(self):
        """Errors without status code or patterns should be UNKNOWN."""
        error = GenericAPIError("something went wrong")
        result = classify_error(error)
        assert result.reason == FailoverReason.UNKNOWN
        assert result.retryable is False


class TestGenericErrorPriority:
    """Test that pattern matching takes priority over status codes."""
    
    def test_context_overflow_pattern_overrides_generic_400(self):
        """Context overflow pattern should override generic 400 handling."""
        error = GenericAPIError(
            "prompt is too long",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        # Should NOT be FORMAT_ERROR
        assert result.reason != FailoverReason.FORMAT_ERROR
    
    def test_rate_limit_pattern_overrides_status_code(self):
        """Rate limit pattern should be detected even without 429 status."""
        error = GenericAPIError(
            "too many requests",
            status_code=None
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.RATE_LIMIT
    
    def test_auth_pattern_overrides_status_code(self):
        """Auth pattern should be detected even without 401 status."""
        error = GenericAPIError(
            "authentication failed",
            status_code=None
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.AUTH_PERMANENT
