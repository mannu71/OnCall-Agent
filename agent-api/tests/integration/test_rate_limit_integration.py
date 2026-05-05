"""Integration test for rate limit tracking in ReactStrategy.

This test demonstrates the full flow of rate limit tracking from
LLM API response through to state capture and warning logging.

**Validates: Requirements 16.4**
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from langchain_core.messages import AIMessage, HumanMessage

from app.workflow.strategies.react import ReactStrategy


@pytest.mark.asyncio
async def test_rate_limit_tracking_end_to_end():
    """Test complete rate limit tracking flow in agent execution.
    
    This integration test verifies:
    1. Rate limit headers are captured from LLM responses
    2. State is updated correctly
    3. Warnings are logged when approaching limits
    4. The feature can be toggled via settings
    
    **Validates: Requirements 16.4**
    """
    with patch("app.workflow.strategies.react.settings") as mock_settings:
        # Enable rate limit tracking
        mock_settings.rate_limit_tracking_enabled = True
        mock_settings.rate_limit_warning_threshold = 0.80
        mock_settings.context_compression_enabled = False
        
        # Create strategy
        strategy = ReactStrategy()
        
        # Create mock logger
        mock_logger = Mock()
        
        # Simulate LLM response with rate limit headers
        ai_message = AIMessage(
            content="This is the agent's response",
            response_metadata={
                "headers": {
                    "x-ratelimit-limit-requests": "50",
                    "x-ratelimit-remaining-requests": "8",  # 84% used - above threshold
                    "x-ratelimit-reset-requests": "60.0",
                    "x-ratelimit-limit-tokens": "10000",
                    "x-ratelimit-remaining-tokens": "2000",  # 80% used - at threshold
                    "x-ratelimit-reset-tokens": "60.0",
                }
            }
        )
        
        # Capture rate limits
        strategy._capture_rate_limits(
            ai_message,
            provider="anthropic",
            logger_instance=mock_logger,
            execution_id="test-integration-001"
        )
        
        # Verify state was captured
        assert strategy._rate_limit_state is not None
        assert strategy._rate_limit_state.provider == "anthropic"
        
        # Verify requests bucket
        assert strategy._rate_limit_state.requests_min.limit == 50
        assert strategy._rate_limit_state.requests_min.remaining == 8
        assert strategy._rate_limit_state.requests_min.used == 42
        assert strategy._rate_limit_state.requests_min.usage_pct == 0.84
        
        # Verify tokens bucket
        assert strategy._rate_limit_state.tokens_min.limit == 10000
        assert strategy._rate_limit_state.tokens_min.remaining == 2000
        assert strategy._rate_limit_state.tokens_min.used == 8000
        assert strategy._rate_limit_state.tokens_min.usage_pct == 0.80
        
        # Verify debug log was called
        assert mock_logger.debug.called
        debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
        assert any("Rate limits updated" in call for call in debug_calls)
        
        # Verify warning was logged (both buckets at/above threshold)
        assert mock_logger.warning.called
        warning_call = mock_logger.warning.call_args
        warning_message = warning_call[0][0]
        
        # Should warn about both buckets
        assert "Rate limit warning" in warning_message
        assert "Requests/min at 84%" in warning_message
        assert "Tokens/min at 80%" in warning_message
        
        # Verify extra data
        extra = warning_call[1]["extra"]
        assert extra["provider"] == "anthropic"
        assert extra["execution_id"] == "test-integration-001"
        assert "rate_limit_warnings" in extra
        assert len(extra["rate_limit_warnings"]) == 2


@pytest.mark.asyncio
async def test_rate_limit_tracking_with_multiple_messages():
    """Test rate limit tracking across multiple LLM responses.
    
    Simulates a multi-turn conversation where rate limits decrease
    with each API call.
    
    **Validates: Requirements 16.4**
    """
    with patch("app.workflow.strategies.react.settings") as mock_settings:
        mock_settings.rate_limit_tracking_enabled = True
        mock_settings.rate_limit_warning_threshold = 0.80
        mock_settings.context_compression_enabled = False
        
        strategy = ReactStrategy()
        mock_logger = Mock()
        
        # First API call - plenty of capacity
        message1 = AIMessage(
            content="First response",
            response_metadata={
                "headers": {
                    "x-ratelimit-limit-requests": "50",
                    "x-ratelimit-remaining-requests": "49",
                    "x-ratelimit-reset-requests": "60.0",
                }
            }
        )
        strategy._capture_rate_limits(message1, "openai", mock_logger, "exec-001")
        
        # Verify no warning (only 2% used)
        assert strategy._rate_limit_state.requests_min.usage_pct < 0.80
        assert not mock_logger.warning.called
        
        # Second API call - approaching limit
        message2 = AIMessage(
            content="Second response",
            response_metadata={
                "headers": {
                    "x-ratelimit-limit-requests": "50",
                    "x-ratelimit-remaining-requests": "9",  # 82% used
                    "x-ratelimit-reset-requests": "55.0",
                }
            }
        )
        strategy._capture_rate_limits(message2, "openai", mock_logger, "exec-001")
        
        # Verify warning is now logged
        assert strategy._rate_limit_state.requests_min.usage_pct > 0.80
        assert mock_logger.warning.called
        
        warning_message = mock_logger.warning.call_args[0][0]
        assert "Requests/min at 82%" in warning_message


@pytest.mark.asyncio
async def test_rate_limit_tracking_disabled_integration():
    """Test that rate limit tracking can be disabled without affecting execution.
    
    **Validates: Requirements 16.4**
    """
    with patch("app.workflow.strategies.react.settings") as mock_settings:
        # Disable rate limit tracking
        mock_settings.rate_limit_tracking_enabled = False
        mock_settings.context_compression_enabled = False
        
        strategy = ReactStrategy()
        mock_logger = Mock()
        
        # Simulate LLM response with rate limit headers
        ai_message = AIMessage(
            content="Response with headers",
            response_metadata={
                "headers": {
                    "x-ratelimit-limit-requests": "50",
                    "x-ratelimit-remaining-requests": "5",  # 90% used
                    "x-ratelimit-reset-requests": "60.0",
                }
            }
        )
        
        # Capture rate limits (should be no-op)
        strategy._capture_rate_limits(
            ai_message,
            provider="anthropic",
            logger_instance=mock_logger,
            execution_id="test-disabled"
        )
        
        # Verify state was NOT updated
        assert strategy._rate_limit_state is None
        
        # Verify no logs were made
        assert not mock_logger.debug.called
        assert not mock_logger.warning.called
