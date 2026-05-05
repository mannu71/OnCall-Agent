"""Tests for retry system callback integration and logging."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.error_classifier import ClassifiedError, FailoverReason
from app.core.retry import RetryPolicy, with_retry


@pytest.mark.asyncio
async def test_on_context_overflow_callback_invoked():
    """Test that on_context_overflow callback is invoked on context overflow errors."""
    # Create a mock function that fails with context overflow
    mock_fn = AsyncMock(side_effect=Exception("context_length_exceeded"))
    
    # Create mock callback
    on_context_overflow = AsyncMock()
    
    # Mock classify_error to return context overflow
    with patch("app.core.retry.classify_error") as mock_classify:
        mock_classify.return_value = ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            message="Context length exceeded",
            retryable=True,
            should_compress=True,
            should_fallback=False,
            should_rotate_credential=False,
        )
        
        # Should exhaust retries and raise
        # Note: Stops at 3 attempts due to total delay limit
        with pytest.raises(Exception, match="context_length_exceeded"):
            await with_retry(
                mock_fn,
                max_retries=2,
                on_context_overflow=on_context_overflow,
            )
        
        # Callback should be invoked for each retry attempt (stops at 3 due to delay limit)
        assert on_context_overflow.call_count == 3


@pytest.mark.asyncio
async def test_on_fallback_callback_invoked():
    """Test that on_fallback callback is invoked on auth/billing errors."""
    # Create a mock function that fails with auth error
    mock_fn = AsyncMock(side_effect=Exception("insufficient_quota"))
    
    # Create mock callback
    on_fallback = AsyncMock()
    
    # Mock classify_error to return billing error
    with patch("app.core.retry.classify_error") as mock_classify:
        classified = ClassifiedError(
            reason=FailoverReason.BILLING,
            message="Insufficient quota",
            retryable=False,
            should_compress=False,
            should_fallback=True,
            should_rotate_credential=False,
        )
        mock_classify.return_value = classified
        
        # Should raise immediately (not retryable)
        with pytest.raises(Exception, match="insufficient_quota"):
            await with_retry(
                mock_fn,
                max_retries=2,
                on_fallback=on_fallback,
            )
        
        # Callback should be invoked once
        assert on_fallback.call_count == 1
        on_fallback.assert_called_with(classified)


@pytest.mark.asyncio
async def test_on_retry_callback_receives_classified_error():
    """Test that on_retry callback receives ClassifiedError with full details."""
    # Create a mock function that fails
    mock_fn = AsyncMock(side_effect=Exception("rate_limit_exceeded"))
    
    # Create mock callback
    on_retry = AsyncMock()
    
    # Mock classify_error to return rate limit error
    with patch("app.core.retry.classify_error") as mock_classify:
        classified = ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            message="Rate limit exceeded",
            retryable=True,
            should_compress=False,
            should_fallback=False,
            should_rotate_credential=False,
        )
        mock_classify.return_value = classified
        
        # Should exhaust retries and raise
        # Note: Stops at 3 attempts due to total delay limit
        with pytest.raises(Exception, match="rate_limit_exceeded"):
            await with_retry(
                mock_fn,
                max_retries=2,
                on_retry=on_retry,
            )
        
        # Callback should be invoked with attempt number and classified error (stops at 3)
        assert on_retry.call_count == 3
        # First call: attempt 1
        on_retry.assert_any_call(1, classified)
        # Last call: attempt 3
        on_retry.assert_any_call(3, classified)


@pytest.mark.asyncio
async def test_callback_failure_does_not_block_retry():
    """Test that callback failures don't prevent retry logic from continuing."""
    # Create a mock function that succeeds on second attempt
    call_count = 0
    
    async def mock_fn():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("temporary_error")
        return "success"
    
    # Create failing callbacks
    on_context_overflow = AsyncMock(side_effect=Exception("callback_failed"))
    on_retry = AsyncMock(side_effect=Exception("callback_failed"))
    
    # Mock classify_error to return context overflow
    with patch("app.core.retry.classify_error") as mock_classify:
        mock_classify.return_value = ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            message="Context overflow",
            retryable=True,
            should_compress=True,
            should_fallback=False,
            should_rotate_credential=False,
        )
        
        # Should succeed despite callback failures
        result = await with_retry(
            mock_fn,
            max_retries=2,
            on_context_overflow=on_context_overflow,
            on_retry=on_retry,
        )
        
        assert result == "success"
        assert call_count == 2


@pytest.mark.asyncio
async def test_detailed_logging_includes_classified_error_info(caplog):
    """Test that retry logging includes detailed classified error information."""
    # Create a mock function that fails
    mock_fn = AsyncMock(side_effect=Exception("test_error"))
    
    # Mock classify_error
    with patch("app.core.retry.classify_error") as mock_classify:
        mock_classify.return_value = ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            message="Rate limit exceeded",
            retryable=True,
            should_compress=False,
            should_fallback=False,
            should_rotate_credential=False,
        )
        
        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception, match="test_error"):
                await with_retry(
                    mock_fn,
                    max_retries=1,
                )
        
        # Check that log includes classified error details
        log_messages = [record.message for record in caplog.records]
        assert any("reason=rate_limit" in msg for msg in log_messages)
        assert any("retryable=True" in msg for msg in log_messages)
        assert any("should_compress=False" in msg for msg in log_messages)
        assert any("should_fallback=False" in msg for msg in log_messages)


@pytest.mark.asyncio
async def test_warning_when_callback_not_provided(caplog):
    """Test that warnings are logged when callbacks are recommended but not provided."""
    # Create a mock function that fails with context overflow
    mock_fn = AsyncMock(side_effect=Exception("context_overflow"))
    
    # Mock classify_error to return context overflow (should_compress=True)
    with patch("app.core.retry.classify_error") as mock_classify:
        mock_classify.return_value = ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            message="Context overflow",
            retryable=True,
            should_compress=True,
            should_fallback=False,
            should_rotate_credential=False,
        )
        
        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception, match="context_overflow"):
                await with_retry(
                    mock_fn,
                    max_retries=1,
                    # No on_context_overflow callback provided
                )
        
        # Check that warning is logged
        log_messages = [record.message for record in caplog.records]
        assert any(
            "no on_context_overflow callback provided" in msg 
            for msg in log_messages
        )


@pytest.mark.asyncio
async def test_warning_when_fallback_callback_not_provided(caplog):
    """Test that warnings are logged when fallback is recommended but callback not provided."""
    # Create a mock function that fails with billing error
    mock_fn = AsyncMock(side_effect=Exception("billing_error"))
    
    # Mock classify_error to return billing error (should_fallback=True)
    with patch("app.core.retry.classify_error") as mock_classify:
        mock_classify.return_value = ClassifiedError(
            reason=FailoverReason.BILLING,
            message="Billing error",
            retryable=False,
            should_compress=False,
            should_fallback=True,
            should_rotate_credential=False,
        )
        
        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception, match="billing_error"):
                await with_retry(
                    mock_fn,
                    max_retries=1,
                    # No on_fallback callback provided
                )
        
        # Check that warning is logged
        log_messages = [record.message for record in caplog.records]
        assert any(
            "no on_fallback callback provided" in msg 
            for msg in log_messages
        )

