"""
Tests for streaming callbacks.

This module tests:
- StreamCallback protocol compliance
- LoggingStreamCallback implementation
- Tool preview generation
- TTY and non-TTY output modes
- Error classification logging
"""

import logging
import pytest
from unittest.mock import Mock, patch

from app.core.streaming.callbacks import (
    LoggingStreamCallback,
    build_tool_preview,
)
from app.core.error_classifier import ClassifiedError, FailoverReason


class TestBuildToolPreview:
    """Test tool preview generation."""
    
    def test_file_operation_preview(self):
        """File operations should show file path."""
        preview = build_tool_preview("read_file", {"path": "src/main.py"})
        assert preview == "src/main.py"
        
        preview = build_tool_preview("write_file", {"file": "config.json"})
        assert preview == "config.json"
    
    def test_terminal_operation_preview(self):
        """Terminal operations should show command."""
        preview = build_tool_preview("terminal", {"command": "npm test"})
        assert preview == "npm test"
        
        preview = build_tool_preview("shell_exec", {"cmd": "ls -la"})
        assert preview == "ls -la"
    
    def test_search_operation_preview(self):
        """Search operations should show query."""
        preview = build_tool_preview("web_search", {"query": "python async"})
        assert preview == "python async"
        
        preview = build_tool_preview("grep_search", {"pattern": "TODO"})
        assert preview == "TODO"
    
    def test_database_operation_preview(self):
        """Database operations should show query or table."""
        preview = build_tool_preview("sql_query", {"query": "SELECT * FROM users"})
        assert preview == "SELECT * FROM users"
        
        preview = build_tool_preview("db_read", {"table": "orders"})
        assert preview == "orders"
    
    def test_web_operation_preview(self):
        """Web operations should show URL."""
        preview = build_tool_preview("http_fetch", {"url": "https://api.example.com"})
        assert preview == "https://api.example.com"
    
    def test_truncation(self):
        """Long previews should be truncated."""
        long_path = "a" * 100
        preview = build_tool_preview("read_file", {"path": long_path}, max_len=40)
        assert len(preview) == 40
        assert preview.endswith("...")
    
    def test_no_suitable_argument(self):
        """Should return None when no suitable argument found."""
        preview = build_tool_preview("unknown_tool", {"foo": 123, "bar": True})
        assert preview is None
    
    def test_empty_args(self):
        """Should return None for empty args."""
        preview = build_tool_preview("some_tool", {})
        assert preview is None
    
    def test_fallback_to_first_string(self):
        """Should use first string argument as fallback."""
        preview = build_tool_preview("custom_tool", {"data": "some value"})
        assert preview == "some value"


class TestLoggingStreamCallback:
    """Test LoggingStreamCallback implementation."""
    
    @pytest.fixture
    def mock_logger(self):
        """Create mock logger."""
        return Mock(spec=logging.Logger)
    
    @pytest.fixture
    def callback(self, mock_logger):
        """Create callback with mock logger."""
        return LoggingStreamCallback(
            logger=mock_logger,
            execution_id="test-exec-123",
            tty_mode=False  # Force non-TTY for consistent testing
        )
    
    @pytest.mark.asyncio
    async def test_on_llm_token(self, callback, mock_logger):
        """Test LLM token logging."""
        # Send 50 tokens to trigger logging
        for i in range(50):
            await callback.on_llm_token("token")
        
        # Should log at 50 tokens
        assert mock_logger.debug.called
        call_args = mock_logger.debug.call_args
        # Check the formatted message (args[0]) and the token count (args[1])
        assert "tokens generated" in call_args[0][0]
        assert call_args[0][1] == 50
    
    @pytest.mark.asyncio
    async def test_on_tool_call_with_preview(self, callback, mock_logger):
        """Test tool call logging with preview."""
        await callback.on_tool_call(
            "read_file",
            {"path": "main.py"},
            preview="main.py"
        )
        
        assert mock_logger.info.called
        call_args = mock_logger.info.call_args
        assert "read_file" in call_args[0][0]
        
        # Check extra fields
        extra = call_args[1]["extra"]
        assert extra["execution_id"] == "test-exec-123"
        assert extra["tool_name"] == "read_file"
        assert extra["tool_preview"] == "main.py"
    
    @pytest.mark.asyncio
    async def test_on_tool_call_auto_preview(self, callback, mock_logger):
        """Test tool call logging with auto-generated preview."""
        await callback.on_tool_call(
            "terminal",
            {"command": "npm test"}
        )
        
        assert mock_logger.info.called
        call_args = mock_logger.info.call_args
        
        # Check that preview was auto-generated
        extra = call_args[1]["extra"]
        assert extra["tool_preview"] == "npm test"
    
    @pytest.mark.asyncio
    async def test_on_tool_result_success(self, callback, mock_logger):
        """Test tool result logging for successful execution."""
        await callback.on_tool_result(
            "read_file",
            "File contents here",
            duration=0.5
        )
        
        assert mock_logger.info.called
        call_args = mock_logger.info.call_args
        assert "read_file" in call_args[0][0]
        
        # Check extra fields
        extra = call_args[1]["extra"]
        assert extra["tool_name"] == "read_file"
        assert extra["duration"] == 0.5
        assert extra["result_length"] == 18
    
    @pytest.mark.asyncio
    async def test_on_tool_result_error(self, callback, mock_logger):
        """Test tool result logging for error result."""
        await callback.on_tool_result(
            "terminal",
            "Error: Command failed",
            duration=1.0
        )
        
        assert mock_logger.info.called
        # Error detection is for display formatting, still logs as info
    
    @pytest.mark.asyncio
    async def test_on_tool_result_truncation(self, callback, mock_logger):
        """Test that long results are truncated in logs."""
        long_result = "x" * 500
        await callback.on_tool_result("tool", long_result)
        
        call_args = mock_logger.info.call_args
        extra = call_args[1]["extra"]
        
        # Preview should be truncated
        assert len(extra["result_preview"]) <= 203  # 200 + "..."
        assert extra["result_length"] == 500
    
    @pytest.mark.asyncio
    async def test_on_error_with_classification(self, callback, mock_logger):
        """Test error logging with classification."""
        classified = ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            retryable=True,
            should_compress=False,
            should_fallback=False,
        )
        
        await callback.on_error("Rate limit exceeded", classified)
        
        assert mock_logger.error.called
        call_args = mock_logger.error.call_args
        assert "Rate limit" in call_args[0][0]
        
        # Check extra fields
        extra = call_args[1]["extra"]
        # FailoverReason enum value is lowercase with underscores
        assert extra["error_reason"] == "rate_limit"
        assert extra["retryable"] is True
        assert extra["should_compress"] is False
    
    @pytest.mark.asyncio
    async def test_on_error_without_classification(self, callback, mock_logger):
        """Test error logging without classification."""
        await callback.on_error("Unknown error occurred")
        
        assert mock_logger.error.called
        call_args = mock_logger.error.call_args
        assert "Unknown error" in call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_on_complete_with_metadata(self, callback, mock_logger):
        """Test completion logging with metadata."""
        metadata = {
            "token_usage": {"total_tokens": 1500},
            "duration": 10.5,
            "model": "gpt-4"
        }
        
        await callback.on_complete("Final answer here", metadata)
        
        assert mock_logger.info.called
        call_args = mock_logger.info.call_args
        assert "complete" in call_args[0][0].lower()
        
        # Check extra fields
        extra = call_args[1]["extra"]
        assert extra["execution_id"] == "test-exec-123"
        assert extra["output_length"] == 17
        assert "metadata" in extra
    
    @pytest.mark.asyncio
    async def test_on_complete_without_metadata(self, callback, mock_logger):
        """Test completion logging without metadata."""
        await callback.on_complete("Final answer")
        
        assert mock_logger.info.called
        call_args = mock_logger.info.call_args
        
        extra = call_args[1]["extra"]
        assert extra["output_length"] == 12
    
    @pytest.mark.asyncio
    async def test_tty_mode_formatting(self, mock_logger):
        """Test TTY mode uses emoji formatting."""
        callback = LoggingStreamCallback(
            logger=mock_logger,
            execution_id="test",
            tty_mode=True
        )
        
        await callback.on_tool_call("read_file", {"path": "test.py"}, "test.py")
        
        call_args = mock_logger.info.call_args
        # TTY mode should use emoji
        assert "🔧" in call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_auto_tty_detection(self, mock_logger):
        """Test automatic TTY detection."""
        with patch('sys.stdout.isatty', return_value=True):
            callback = LoggingStreamCallback(logger=mock_logger)
            assert callback.tty_mode is True
        
        with patch('sys.stdout.isatty', return_value=False):
            callback = LoggingStreamCallback(logger=mock_logger)
            assert callback.tty_mode is False
    
    def test_looks_like_error(self):
        """Test error detection in results."""
        callback = LoggingStreamCallback()
        
        assert callback._looks_like_error("Error: Something failed")
        assert callback._looks_like_error("Failed: Connection timeout")
        assert callback._looks_like_error("Exception: ValueError")
        assert callback._looks_like_error("Traceback (most recent call last)")
        assert callback._looks_like_error("Fatal: Cannot continue")
        
        assert not callback._looks_like_error("Success")
        assert not callback._looks_like_error("Completed successfully")
        assert not callback._looks_like_error("")


class TestStreamCallbackProtocol:
    """Test StreamCallback protocol compliance."""
    
    @pytest.mark.asyncio
    async def test_logging_callback_implements_protocol(self):
        """LoggingStreamCallback should implement StreamCallback protocol."""
        callback = LoggingStreamCallback()
        
        # Should have all required methods
        assert hasattr(callback, 'on_llm_token')
        assert hasattr(callback, 'on_tool_call')
        assert hasattr(callback, 'on_tool_result')
        assert hasattr(callback, 'on_error')
        assert hasattr(callback, 'on_complete')
        
        # Methods should be callable
        assert callable(callback.on_llm_token)
        assert callable(callback.on_tool_call)
        assert callable(callback.on_tool_result)
        assert callable(callback.on_error)
        assert callable(callback.on_complete)
    
    @pytest.mark.asyncio
    async def test_custom_callback_implementation(self):
        """Custom callbacks can implement the protocol."""
        
        class CustomCallback:
            def __init__(self):
                self.events = []
            
            async def on_llm_token(self, token: str) -> None:
                self.events.append(("token", token))
            
            async def on_tool_call(self, tool_name: str, args: dict, preview=None) -> None:
                self.events.append(("tool_call", tool_name))
            
            async def on_tool_result(self, tool_name: str, result: str, duration=None) -> None:
                self.events.append(("tool_result", tool_name))
            
            async def on_error(self, error: str, classified=None) -> None:
                self.events.append(("error", error))
            
            async def on_complete(self, output: str, metadata=None) -> None:
                self.events.append(("complete", output))
        
        callback = CustomCallback()
        
        # Test all methods
        await callback.on_llm_token("test")
        await callback.on_tool_call("tool", {})
        await callback.on_tool_result("tool", "result")
        await callback.on_error("error")
        await callback.on_complete("output")
        
        # Verify events were recorded
        assert len(callback.events) == 5
        assert callback.events[0] == ("token", "test")
        assert callback.events[1] == ("tool_call", "tool")
        assert callback.events[2] == ("tool_result", "tool")
        assert callback.events[3] == ("error", "error")
        assert callback.events[4] == ("complete", "output")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
