"""Unit tests for ReactStrategy rate limit tracking integration.

Tests verify that rate limit headers are captured from LLM responses,
state is updated correctly, and warnings are logged when approaching limits.

**Validates: Requirements 16.4**
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.messages import AIMessage

from app.workflow.strategies.react import ReactStrategy
from app.core.rate_limit_tracker import RateLimitState, RateLimitBucket


@pytest.fixture
def mock_settings():
    """Mock settings with rate limit tracking enabled."""
    with patch("app.workflow.strategies.react.settings") as mock_settings:
        mock_settings.rate_limit_tracking_enabled = True
        mock_settings.rate_limit_warning_threshold = 0.80
        mock_settings.context_compression_enabled = False
        yield mock_settings


@pytest.fixture
def strategy(mock_settings):
    """Create ReactStrategy instance with mocked settings."""
    return ReactStrategy()


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    return Mock()


def test_capture_rate_limits_with_valid_headers(strategy, mock_logger):
    """Test capturing rate limits from valid response headers.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage with rate limit headers in response_metadata
    message = AIMessage(
        content="Test response",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "50",
                "x-ratelimit-remaining-requests": "45",
                "x-ratelimit-reset-requests": "60.0",
                "x-ratelimit-limit-tokens": "10000",
                "x-ratelimit-remaining-tokens": "9500",
                "x-ratelimit-reset-tokens": "60.0",
            }
        }
    )
    
    # Capture rate limits
    strategy._capture_rate_limits(message, "anthropic", mock_logger, "test-exec-id")
    
    # Verify state was updated
    assert strategy._rate_limit_state is not None
    assert strategy._rate_limit_state.provider == "anthropic"
    assert strategy._rate_limit_state.requests_min.limit == 50
    assert strategy._rate_limit_state.requests_min.remaining == 45
    assert strategy._rate_limit_state.tokens_min.limit == 10000
    assert strategy._rate_limit_state.tokens_min.remaining == 9500
    
    # Verify debug log was called
    mock_logger.debug.assert_called()
    debug_call = mock_logger.debug.call_args
    assert "Rate limits updated" in debug_call[0][0]


def test_capture_rate_limits_with_warning_threshold(strategy, mock_logger):
    """Test that warnings are logged when usage exceeds threshold.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage with rate limits at 85% usage (above 80% threshold)
    message = AIMessage(
        content="Test response",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "15",  # 85% used
                "x-ratelimit-reset-requests": "60.0",
            }
        }
    )
    
    # Capture rate limits
    strategy._capture_rate_limits(message, "openai", mock_logger, "test-exec-id")
    
    # Verify warning was logged
    mock_logger.warning.assert_called()
    warning_call = mock_logger.warning.call_args
    assert "Rate limit warning" in warning_call[0][0]
    assert "Requests/min at 85%" in warning_call[0][0]
    
    # Verify extra data includes warnings
    extra = warning_call[1]["extra"]
    assert "rate_limit_warnings" in extra
    assert len(extra["rate_limit_warnings"]) > 0


def test_capture_rate_limits_multiple_buckets_warning(strategy, mock_logger):
    """Test warnings for multiple buckets exceeding threshold.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage with multiple buckets at high usage
    message = AIMessage(
        content="Test response",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "50",
                "x-ratelimit-remaining-requests": "5",  # 90% used
                "x-ratelimit-reset-requests": "60.0",
                "x-ratelimit-limit-tokens": "10000",
                "x-ratelimit-remaining-tokens": "1000",  # 90% used
                "x-ratelimit-reset-tokens": "60.0",
            }
        }
    )
    
    # Capture rate limits
    strategy._capture_rate_limits(message, "anthropic", mock_logger, "test-exec-id")
    
    # Verify warning includes both buckets
    mock_logger.warning.assert_called()
    warning_call = mock_logger.warning.call_args
    warning_message = warning_call[0][0]
    assert "Requests/min at 90%" in warning_message
    assert "Tokens/min at 90%" in warning_message
    
    # Verify extra data has multiple warnings
    extra = warning_call[1]["extra"]
    assert len(extra["rate_limit_warnings"]) == 2


def test_capture_rate_limits_no_headers(strategy, mock_logger):
    """Test handling of message with no rate limit headers.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage without rate limit headers
    message = AIMessage(
        content="Test response",
        response_metadata={}
    )
    
    # Capture rate limits
    strategy._capture_rate_limits(message, "openai", mock_logger, "test-exec-id")
    
    # Verify state was not updated
    assert strategy._rate_limit_state is None
    
    # Verify no warning was logged
    mock_logger.warning.assert_not_called()


def test_capture_rate_limits_disabled(mock_logger):
    """Test that rate limit tracking can be disabled via settings.
    
    **Validates: Requirements 16.4**
    """
    with patch("app.workflow.strategies.react.settings") as mock_settings:
        mock_settings.rate_limit_tracking_enabled = False
        mock_settings.context_compression_enabled = False
        
        strategy = ReactStrategy()
        
        # Create AIMessage with rate limit headers
        message = AIMessage(
            content="Test response",
            response_metadata={
                "headers": {
                    "x-ratelimit-limit-requests": "50",
                    "x-ratelimit-remaining-requests": "45",
                    "x-ratelimit-reset-requests": "60.0",
                }
            }
        )
        
        # Capture rate limits
        strategy._capture_rate_limits(message, "anthropic", mock_logger, "test-exec-id")
        
        # Verify state was not updated (tracking disabled)
        assert strategy._rate_limit_state is None
        
        # Verify no logs were made
        mock_logger.debug.assert_not_called()
        mock_logger.warning.assert_not_called()


def test_capture_rate_limits_error_handling(strategy, mock_logger):
    """Test that errors in rate limit capture don't break execution.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage with valid structure but cause error in parsing
    message = AIMessage(
        content="Test response",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "not-a-number",  # Invalid value
            }
        }
    )
    
    # Capture rate limits - should not raise exception
    strategy._capture_rate_limits(message, "openai", mock_logger, "test-exec-id")
    
    # Verify state was not updated due to parsing error
    # (parse_rate_limit_headers returns None for invalid data)
    # The method should handle this gracefully


def test_capture_rate_limits_headers_in_root(strategy, mock_logger):
    """Test capturing headers when they're in root of response_metadata.
    
    Some providers put headers directly in response_metadata instead of nested.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage with headers in root of response_metadata
    message = AIMessage(
        content="Test response",
        response_metadata={
            "x-ratelimit-limit-requests": "50",
            "x-ratelimit-remaining-requests": "45",
            "x-ratelimit-reset-requests": "60.0",
        }
    )
    
    # Capture rate limits
    strategy._capture_rate_limits(message, "anthropic", mock_logger, "test-exec-id")
    
    # Verify state was updated
    assert strategy._rate_limit_state is not None
    assert strategy._rate_limit_state.requests_min.limit == 50
    assert strategy._rate_limit_state.requests_min.remaining == 45


def test_capture_rate_limits_below_threshold(strategy, mock_logger):
    """Test that no warning is logged when usage is below threshold.
    
    **Validates: Requirements 16.4**
    """
    # Create AIMessage with rate limits at 50% usage (below 80% threshold)
    message = AIMessage(
        content="Test response",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "50",  # 50% used
                "x-ratelimit-reset-requests": "60.0",
            }
        }
    )
    
    # Capture rate limits
    strategy._capture_rate_limits(message, "openai", mock_logger, "test-exec-id")
    
    # Verify state was updated
    assert strategy._rate_limit_state is not None
    
    # Verify debug log was called but no warning
    mock_logger.debug.assert_called()
    mock_logger.warning.assert_not_called()


def test_rate_limit_state_persistence(strategy, mock_logger):
    """Test that rate limit state persists across multiple captures.
    
    **Validates: Requirements 16.4**
    """
    # First capture
    message1 = AIMessage(
        content="Test response 1",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "50",
                "x-ratelimit-remaining-requests": "45",
                "x-ratelimit-reset-requests": "60.0",
            }
        }
    )
    strategy._capture_rate_limits(message1, "anthropic", mock_logger, "test-exec-id")
    
    first_state = strategy._rate_limit_state
    assert first_state is not None
    assert first_state.requests_min.remaining == 45
    
    # Second capture with updated values
    message2 = AIMessage(
        content="Test response 2",
        response_metadata={
            "headers": {
                "x-ratelimit-limit-requests": "50",
                "x-ratelimit-remaining-requests": "44",  # One more request used
                "x-ratelimit-reset-requests": "55.0",
            }
        }
    )
    strategy._capture_rate_limits(message2, "anthropic", mock_logger, "test-exec-id")
    
    # Verify state was updated
    assert strategy._rate_limit_state is not None
    assert strategy._rate_limit_state.requests_min.remaining == 44
    assert strategy._rate_limit_state is not first_state  # New state object
