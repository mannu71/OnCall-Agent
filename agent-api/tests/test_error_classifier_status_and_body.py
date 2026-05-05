"""Unit tests for HTTP status code and error body parsing enhancements.

Tests for Requirements 3.4, 3.5, 3.7:
- HTTP status code extraction from error cause chain
- Structured error body parsing
- Recovery hints population
"""

import pytest
from unittest.mock import MagicMock

from app.core.error_classifier import (
    classify_error,
    _extract_status_code,
    _extract_error_body,
    _classify_openai_error,
    _classify_anthropic_error,
    _classify_boto_error,
    ClassifiedError,
    FailoverReason,
)


class MockResponse:
    """Mock HTTP response object."""
    def __init__(self, status_code: int = None, json_body: dict = None, text_body: str = None):
        self.status_code = status_code
        self._json_body = json_body
        self._text_body = text_body
    
    def json(self):
        return self._json_body
    
    @property
    def text(self):
        return self._text_body


class MockErrorWithResponse(Exception):
    """Mock error with response object."""
    def __init__(self, message: str, response: MockResponse = None, status_code: int = None):
        super().__init__(message)
        self.response = response
        if status_code:
            self.status_code = status_code


class MockErrorWithCause(Exception):
    """Mock error with cause chain."""
    def __init__(self, message: str, cause: Exception = None):
        super().__init__(message)
        self.__cause__ = cause


class MockOpenAIError(Exception):
    """Mock OpenAI error for testing."""
    def __init__(self, message: str, error_type: str = "APIStatusError", status_code: int = None, response: MockResponse = None):
        super().__init__(message)
        self.__class__.__name__ = error_type
        self.__class__.__module__ = "openai.error"
        if status_code:
            self.status_code = status_code
        if response:
            self.response = response


class TestStatusCodeExtraction:
    """Tests for HTTP status code extraction from error cause chain."""

    def test_extract_status_code_direct_attribute(self):
        """Status code should be extracted from direct attribute."""
        error = MockErrorWithResponse("Error", status_code=429)
        status_code = _extract_status_code(error)
        assert status_code == 429

    def test_extract_status_code_from_response(self):
        """Status code should be extracted from response object."""
        response = MockResponse(status_code=503)
        error = MockErrorWithResponse("Error", response=response)
        status_code = _extract_status_code(error)
        assert status_code == 503

    def test_extract_status_code_from_cause_chain(self):
        """Status code should be extracted from error cause chain."""
        inner_error = MockErrorWithResponse("Inner error", status_code=500)
        outer_error = MockErrorWithCause("Outer error", cause=inner_error)
        status_code = _extract_status_code(outer_error)
        assert status_code == 500

    def test_extract_status_code_from_nested_cause_chain(self):
        """Status code should be extracted from deeply nested cause chain."""
        innermost_error = MockErrorWithResponse("Innermost error", status_code=404)
        middle_error = MockErrorWithCause("Middle error", cause=innermost_error)
        outer_error = MockErrorWithCause("Outer error", cause=middle_error)
        status_code = _extract_status_code(outer_error)
        assert status_code == 404

    def test_extract_status_code_from_boto_response(self):
        """Status code should be extracted from boto3-style response dict."""
        error = MockErrorWithResponse("Error")
        error.response = {
            "ResponseMetadata": {
                "HTTPStatusCode": 403
            }
        }
        status_code = _extract_status_code(error)
        assert status_code == 403

    def test_extract_status_code_none_when_not_found(self):
        """Should return None when no status code is found."""
        error = Exception("Error without status code")
        status_code = _extract_status_code(error)
        assert status_code is None

    def test_extract_status_code_prevents_infinite_loop(self):
        """Should prevent infinite loops in circular cause chains."""
        error1 = MockErrorWithCause("Error 1")
        error2 = MockErrorWithCause("Error 2", cause=error1)
        # Create circular reference (shouldn't happen in practice, but test safety)
        error1.__cause__ = error2
        
        # Should not hang or crash
        status_code = _extract_status_code(error1)
        assert status_code is None


class TestErrorBodyExtraction:
    """Tests for structured error body parsing."""

    def test_extract_error_body_openai_style(self):
        """Should extract error body from OpenAI-style JSON response."""
        response = MockResponse(
            status_code=400,
            json_body={
                "error": {
                    "code": "invalid_request_error",
                    "message": "Invalid request format",
                    "type": "invalid_request",
                    "param": "messages"
                }
            }
        )
        error = MockErrorWithResponse("Error", response=response)
        error_body = _extract_error_body(error)
        
        assert error_body["error_code"] == "invalid_request_error"
        assert error_body["error_message"] == "Invalid request format"
        assert error_body["error_type"] == "invalid_request"
        assert error_body["error_param"] == "messages"

    def test_extract_error_body_flat_structure(self):
        """Should extract error body from flat JSON structure."""
        response = MockResponse(
            status_code=429,
            json_body={
                "code": "rate_limit_exceeded",
                "message": "Too many requests",
                "type": "rate_limit"
            }
        )
        error = MockErrorWithResponse("Error", response=response)
        error_body = _extract_error_body(error)
        
        assert error_body["error_code"] == "rate_limit_exceeded"
        assert error_body["error_message"] == "Too many requests"
        assert error_body["error_type"] == "rate_limit"

    def test_extract_error_body_boto_style(self):
        """Should extract error body from boto3-style response dict."""
        error = MockErrorWithResponse("Error")
        error.response = {
            "Error": {
                "Code": "ThrottlingException",
                "Message": "Rate exceeded",
                "Type": "Client"
            }
        }
        error_body = _extract_error_body(error)
        
        assert error_body["error_code"] == "ThrottlingException"
        assert error_body["error_message"] == "Rate exceeded"
        assert error_body["error_type"] == "Client"

    def test_extract_error_body_text_fallback(self):
        """Should extract raw text when JSON parsing fails."""
        response = MockResponse(
            status_code=500,
            text_body="Internal Server Error: Database connection failed"
        )
        error = MockErrorWithResponse("Error", response=response)
        error_body = _extract_error_body(error)
        
        assert "raw_text" in error_body
        assert "Database connection failed" in error_body["raw_text"]

    def test_extract_error_body_no_response(self):
        """Should return empty dict when no response is available."""
        error = Exception("Error without response")
        error_body = _extract_error_body(error)
        assert error_body == {}

    def test_extract_error_body_json_parse_error(self):
        """Should handle JSON parsing errors gracefully."""
        response = MockResponse(status_code=500)
        response.json = lambda: (_ for _ in ()).throw(ValueError("Invalid JSON"))
        error = MockErrorWithResponse("Error", response=response)
        error_body = _extract_error_body(error)
        # Should not crash, may be empty or have text fallback
        assert isinstance(error_body, dict)


class TestRecoveryHintsPopulation:
    """Tests for recovery hints in ClassifiedError."""

    def test_billing_error_recovery_hints(self):
        """BILLING errors should have correct recovery hints."""
        error = MockOpenAIError(
            "Error: insufficient credits",
            error_type="RateLimitError"
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.BILLING
        assert result.retryable is False
        assert result.should_compress is False
        assert result.should_rotate_credential is True
        assert result.should_fallback is True

    def test_rate_limit_error_recovery_hints(self):
        """RATE_LIMIT errors should have correct recovery hints."""
        error = MockOpenAIError(
            "Error: rate limit exceeded, try again",
            error_type="RateLimitError"
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.retryable is True
        assert result.should_compress is False
        assert result.should_rotate_credential is False
        assert result.should_fallback is True

    def test_context_overflow_error_recovery_hints(self):
        """CONTEXT_OVERFLOW errors should have correct recovery hints."""
        error = MockOpenAIError(
            "Error: context length exceeded",
            error_type="BadRequestError"
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        assert result.retryable is False
        assert result.should_compress is True
        assert result.should_rotate_credential is False
        assert result.should_fallback is False

    def test_auth_permanent_error_recovery_hints(self):
        """AUTH_PERMANENT errors should have correct recovery hints."""
        error = MockOpenAIError(
            "Error: invalid api key",
            error_type="AuthenticationError"
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.AUTH_PERMANENT
        assert result.retryable is False
        assert result.should_compress is False
        assert result.should_rotate_credential is False
        assert result.should_fallback is True

    def test_server_error_recovery_hints(self):
        """SERVER_ERROR errors should have correct recovery hints."""
        error = MockOpenAIError(
            "Error: internal server error",
            error_type="APIStatusError",
            status_code=500
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.SERVER_ERROR
        assert result.retryable is True
        assert result.should_compress is False
        assert result.should_rotate_credential is False
        assert result.should_fallback is False

    def test_overloaded_error_recovery_hints(self):
        """OVERLOADED errors should have correct recovery hints."""
        error = MockOpenAIError(
            "Error: service unavailable",
            error_type="APIStatusError",
            status_code=503
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.OVERLOADED
        assert result.retryable is True
        assert result.should_compress is False
        assert result.should_rotate_credential is False
        assert result.should_fallback is False


class TestErrorContextPopulation:
    """Tests for error_context field population."""

    def test_error_context_populated_with_structured_body(self):
        """error_context should contain parsed error body."""
        response = MockResponse(
            status_code=400,
            json_body={
                "error": {
                    "code": "invalid_request",
                    "message": "Missing required field",
                    "type": "validation_error"
                }
            }
        )
        error = MockOpenAIError(
            "Error: invalid request",
            error_type="BadRequestError",
            response=response
        )
        result = _classify_openai_error(error)
        
        assert result.error_context is not None
        assert result.error_context.get("error_code") == "invalid_request"
        assert result.error_context.get("error_message") == "Missing required field"
        assert result.error_context.get("error_type") == "validation_error"

    def test_message_field_populated_from_error_body(self):
        """message field should be populated from error body when available."""
        response = MockResponse(
            status_code=429,
            json_body={
                "error": {
                    "message": "Rate limit exceeded for this API key"
                }
            }
        )
        error = MockOpenAIError(
            "Error: rate limit",
            error_type="RateLimitError",
            response=response
        )
        result = _classify_openai_error(error)
        
        assert result.message == "Rate limit exceeded for this API key"

    def test_message_fallback_to_str_error(self):
        """message should fallback to str(error) when error body is empty."""
        error = MockOpenAIError(
            "Error: something went wrong",
            error_type="APIStatusError",
            status_code=500
        )
        result = _classify_openai_error(error)
        
        assert "something went wrong" in result.message

    def test_status_code_populated_in_classified_error(self):
        """status_code should be populated in ClassifiedError."""
        response = MockResponse(status_code=429)
        error = MockOpenAIError(
            "Error: rate limit",
            error_type="APIStatusError",
            status_code=429,
            response=response
        )
        result = _classify_openai_error(error)
        
        assert result.status_code == 429


class TestIntegrationWithExistingClassifiers:
    """Tests to ensure enhancements work with existing classification logic."""

    def test_openai_classification_with_status_and_body(self):
        """OpenAI errors should be classified with status code and body extraction."""
        response = MockResponse(
            status_code=429,
            json_body={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests, please retry"
                }
            }
        )
        error = MockOpenAIError(
            "Error: rate limit",
            error_type="APIStatusError",
            status_code=429,
            response=response
        )
        result = _classify_openai_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.status_code == 429
        assert result.error_context.get("error_code") == "rate_limit_exceeded"
        assert result.message == "Too many requests, please retry"

    def test_anthropic_classification_with_status_and_body(self):
        """Anthropic errors should be classified with status code and body extraction."""
        response = MockResponse(
            status_code=400,
            json_body={
                "error": {
                    "type": "invalid_request_error",
                    "message": "Context length exceeded"
                }
            }
        )
        error = MockOpenAIError(
            "Error: context length",
            error_type="BadRequestError",
            response=response
        )
        error.__class__.__module__ = "anthropic.error"
        result = _classify_anthropic_error(error)
        
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        assert result.status_code == 400
        assert result.should_compress is True

    def test_boto_classification_with_status_and_body(self):
        """Boto errors should be classified with status code and body extraction."""
        error = MockErrorWithResponse("Error")
        error.__class__.__module__ = "botocore.exceptions"
        error.response = {
            "Error": {
                "Code": "ThrottlingException",
                "Message": "Rate exceeded",
                "Type": "Client"
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 429
            }
        }
        result = _classify_boto_error(error)
        
        assert result.reason == FailoverReason.RATE_LIMIT
        assert result.status_code == 429
        assert result.error_context.get("error_code") == "ThrottlingException"
        assert result.message == "Rate exceeded"
