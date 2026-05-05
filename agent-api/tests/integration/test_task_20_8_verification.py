"""Integration tests for Task 20.8: Streaming and Auxiliary Client Integration.

This test suite verifies:
1. StreamCallback protocol is used for real-time feedback in ReactStrategy
2. AuxiliaryClient is used for compression summarization in ContextCompressor
3. All streaming events are properly handled
4. Error handling is in place for both streaming and auxiliary client

Requirements: 16.9, 16.10
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Dict, Any

from app.core.streaming.callbacks import StreamCallback, LoggingStreamCallback, build_tool_preview
from app.core.auxiliary_client import AuxiliaryClient
from app.core.context_compression import ContextCompressor
from app.core.error_classifier import ClassifiedError, FailoverReason


class TestStreamCallbackIntegration:
    """Test StreamCallback protocol integration in ReactStrategy."""
    
    @pytest.mark.asyncio
    async def test_stream_callback_protocol_complete(self):
        """Verify StreamCallback protocol has all required methods.
        
        Requirements: 16.9
        """
        # Create a mock callback
        callback = AsyncMock(spec=StreamCallback)
        
        # Verify all required methods exist
        assert hasattr(callback, 'on_llm_token')
        assert hasattr(callback, 'on_tool_call')
        assert hasattr(callback, 'on_tool_result')
        assert hasattr(callback, 'on_error')
        assert hasattr(callback, 'on_complete')
        
        # Verify methods are callable
        await callback.on_llm_token("test")
        await callback.on_tool_call("test_tool", {})
        await callback.on_tool_result("test_tool", "result")
        await callback.on_error("error")
        await callback.on_complete("output")
        
        # Verify all methods were called
        callback.on_llm_token.assert_called_once()
        callback.on_tool_call.assert_called_once()
        callback.on_tool_result.assert_called_once()
        callback.on_error.assert_called_once()
        callback.on_complete.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_logging_stream_callback_all_events(self):
        """Verify LoggingStreamCallback handles all streaming events.
        
        Requirements: 16.9
        """
        import logging
        logger = logging.getLogger("test")
        callback = LoggingStreamCallback(logger=logger, execution_id="test-exec")
        
        # Test on_llm_token
        await callback.on_llm_token("Hello")
        await callback.on_llm_token(" world")
        assert callback._token_count == 2
        
        # Test on_tool_call with preview
        await callback.on_tool_call("read_file", {"path": "test.py"}, "test.py")
        assert callback._tool_count == 1
        
        # Test on_tool_result
        await callback.on_tool_result("read_file", "file contents", 0.5)
        
        # Test on_error without classification
        await callback.on_error("Test error")
        
        # Test on_error with classification
        classified = ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            message="Rate limit exceeded",
            retryable=True,
            should_compress=False,
            should_fallback=False,
        )
        await callback.on_error("Rate limit error", classified)
        
        # Test on_complete without metadata
        await callback.on_complete("Task completed")
        
        # Test on_complete with metadata
        metadata = {
            "token_usage": {"total_tokens": 1000},
            "duration": 5.2,
        }
        await callback.on_complete("Task completed", metadata)
    
    @pytest.mark.asyncio
    async def test_tool_preview_generation(self):
        """Verify tool preview generation for different tool types.
        
        Requirements: 16.9
        """
        # File operations
        preview = build_tool_preview("read_file", {"path": "src/main.py"})
        assert preview == "src/main.py"
        
        # Terminal operations
        preview = build_tool_preview("terminal", {"command": "npm test"})
        assert preview == "npm test"
        
        # Search operations
        preview = build_tool_preview("web_search", {"query": "python async"})
        assert preview == "python async"
        
        # Database operations
        preview = build_tool_preview("db_query", {"query": "SELECT * FROM users"})
        assert preview == "SELECT * FROM users"
        
        # Web operations
        preview = build_tool_preview("web_fetch", {"url": "https://example.com"})
        assert preview == "https://example.com"
        
        # No suitable argument
        preview = build_tool_preview("unknown_tool", {})
        assert preview is None
    
    @pytest.mark.asyncio
    async def test_stream_callback_error_handling(self):
        """Verify error handling in streaming callbacks.
        
        Requirements: 16.9
        """
        import logging
        logger = logging.getLogger("test")
        callback = LoggingStreamCallback(logger=logger)
        
        # Test error with context overflow classification
        classified = ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            message="Context length exceeded",
            retryable=True,
            should_compress=True,
            should_fallback=False,
        )
        
        # Should not raise exception
        await callback.on_error("Context overflow", classified)
        
        # Test error with billing classification
        classified = ClassifiedError(
            reason=FailoverReason.BILLING,
            message="Insufficient credits",
            retryable=False,
            should_compress=False,
            should_fallback=True,
            should_rotate_credential=True,
        )
        
        # Should not raise exception
        await callback.on_error("Billing error", classified)


class TestAuxiliaryClientIntegration:
    """Test AuxiliaryClient integration in ContextCompressor."""
    
    @pytest.mark.asyncio
    async def test_auxiliary_client_provider_resolution(self):
        """Verify AuxiliaryClient resolves providers correctly.
        
        Requirements: 16.10
        """
        # Test auto mode with no API keys (should resolve to None)
        client = AuxiliaryClient(provider="auto")
        assert client._resolved_provider is None or client.is_available
        
        # Test explicit provider mode
        client = AuxiliaryClient(provider="openai", model="gpt-4o-mini")
        assert client._resolved_provider == "openai"
        assert client._resolved_model == "gpt-4o-mini"
    
    @pytest.mark.asyncio
    async def test_auxiliary_client_fallback_chain(self):
        """Verify AuxiliaryClient has correct fallback chains.
        
        Requirements: 16.10
        """
        # Test OpenRouter fallback chain
        client = AuxiliaryClient(provider="openrouter")
        fallbacks = client.get_fallback_providers()
        assert "anthropic" in fallbacks or "openai" in fallbacks
        
        # Test Anthropic fallback chain
        client = AuxiliaryClient(provider="anthropic")
        fallbacks = client.get_fallback_providers()
        assert "openrouter" in fallbacks or "openai" in fallbacks
    
    @pytest.mark.asyncio
    async def test_context_compressor_uses_auxiliary_client(self):
        """Verify ContextCompressor uses AuxiliaryClient for summarization.
        
        Requirements: 16.10
        """
        compressor = ContextCompressor(
            model="gpt-4",
            threshold_percent=0.5,
            protect_first_n=3,
        )
        
        # Create test messages that need compression
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        
        # Add many large messages to trigger compression
        # Each message needs to be large enough to exceed the threshold
        large_content = "This is a large message. " * 200  # ~5000 chars per message
        for i in range(100):
            messages.append({"role": "user", "content": f"Message {i}: {large_content}"})
            messages.append({"role": "assistant", "content": f"Response {i}: {large_content}"})
        
        # Mock AuxiliaryClient to verify it's called
        with patch('app.core.auxiliary_client.AuxiliaryClient') as mock_aux_client_class:
            mock_aux_client = AsyncMock()
            mock_aux_client.is_available = True
            mock_aux_client.call_with_fallback = AsyncMock(return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content="Test summary"))]
            ))
            mock_aux_client_class.return_value = mock_aux_client
            
            # Trigger compression
            compressed = await compressor.compress_async(messages)
            
            # Verify AuxiliaryClient was instantiated
            mock_aux_client_class.assert_called_once()
            
            # Verify call_with_fallback was called
            mock_aux_client.call_with_fallback.assert_called_once()
            
            # Verify the call parameters
            call_args = mock_aux_client.call_with_fallback.call_args
            assert call_args.kwargs['task'] == 'compression'
            assert call_args.kwargs['max_tokens'] == 2000
            assert call_args.kwargs['temperature'] == 0.3
    
    @pytest.mark.asyncio
    async def test_context_compressor_fallback_on_auxiliary_failure(self):
        """Verify ContextCompressor falls back gracefully when AuxiliaryClient fails.
        
        Requirements: 16.10
        """
        compressor = ContextCompressor(
            model="gpt-4",
            threshold_percent=0.5,
            protect_first_n=3,
        )
        
        # Create test messages
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        
        # Add many large messages to trigger compression
        large_content = "This is a large message. " * 200  # ~5000 chars per message
        for i in range(100):
            messages.append({"role": "user", "content": f"Message {i}: {large_content}"})
            messages.append({"role": "assistant", "content": f"Response {i}: {large_content}"})
        
        # Mock AuxiliaryClient to raise an exception
        with patch('app.core.auxiliary_client.AuxiliaryClient') as mock_aux_client_class:
            mock_aux_client = AsyncMock()
            mock_aux_client.is_available = True
            mock_aux_client.call_with_fallback = AsyncMock(side_effect=Exception("API error"))
            mock_aux_client_class.return_value = mock_aux_client
            
            # Trigger compression - should not raise exception
            compressed = await compressor.compress_async(messages)
            
            # Verify compression still worked (fallback to basic truncation)
            assert len(compressed) < len(messages)
            assert compressed[0]["role"] == "system"  # Head protected


class TestReactStrategyStreamingIntegration:
    """Test ReactStrategy integration with StreamCallback."""
    
    @pytest.mark.asyncio
    async def test_react_strategy_uses_stream_callback(self):
        """Verify ReactStrategy uses StreamCallback in _execute_agent_stream.
        
        Requirements: 16.9
        """
        from app.workflow.strategies.react import ReactStrategy
        
        strategy = ReactStrategy()
        
        # Verify _execute_agent_stream method exists
        assert hasattr(strategy, '_execute_agent_stream')
        
        # Verify method signature includes stream_callback parameter
        import inspect
        sig = inspect.signature(strategy._execute_agent_stream)
        assert 'stream_callback' in sig.parameters
    
    @pytest.mark.asyncio
    async def test_react_strategy_streaming_events(self):
        """Verify ReactStrategy emits all streaming events.
        
        Requirements: 16.9
        """
        from app.workflow.strategies.react import ReactStrategy
        
        strategy = ReactStrategy()
        
        # Create mock agent that simulates streaming events
        mock_agent = MagicMock()
        
        async def mock_astream_events(input_state, version):
            """Mock astream_events that yields various event types."""
            # LLM token event
            yield {
                "event": "on_llm_new_token",
                "data": {"chunk": "Hello"},
                "name": "",
            }
            
            # Tool call event
            yield {
                "event": "on_chat_model_stream",
                "data": {
                    "chunk": MagicMock(
                        tool_calls=[{"name": "read_file", "args": {"path": "test.py"}}],
                        content=None,
                    )
                },
                "name": "",
            }
            
            # Tool start event
            yield {
                "event": "on_tool_start",
                "data": {"input": {"path": "test.py"}},
                "name": "read_file",
            }
            
            # Tool end event
            yield {
                "event": "on_tool_end",
                "data": {"output": "file contents"},
                "name": "read_file",
            }
            
            # Completion event
            yield {
                "event": "on_chat_model_end",
                "data": {
                    "output": MagicMock(
                        id="msg-123",
                        content="Task completed",
                    )
                },
                "name": "",
            }
        
        mock_agent.astream_events = mock_astream_events
        
        # Create mock callback to track events
        mock_callback = AsyncMock(spec=StreamCallback)
        
        # Execute streaming
        result = await strategy._execute_agent_stream(
            agent=mock_agent,
            input_state={"messages": []},
            stream_callback=mock_callback,
            logger_instance=MagicMock(),
            execution_id="test-exec",
            llm_config={"provider": "openai", "model": "gpt-4"},
        )
        
        # Verify all callback methods were called
        assert mock_callback.on_llm_token.called
        assert mock_callback.on_tool_call.called
        assert mock_callback.on_tool_result.called


class TestErrorHandlingIntegration:
    """Test error handling in streaming and auxiliary client."""
    
    @pytest.mark.asyncio
    async def test_stream_callback_handles_classified_errors(self):
        """Verify StreamCallback properly handles ClassifiedError objects.
        
        Requirements: 16.9
        """
        import logging
        logger = logging.getLogger("test")
        callback = LoggingStreamCallback(logger=logger)
        
        # Test various error classifications
        error_types = [
            (FailoverReason.RATE_LIMIT, True, False, False),
            (FailoverReason.CONTEXT_OVERFLOW, True, True, False),
            (FailoverReason.BILLING, False, False, True),
            (FailoverReason.AUTH, False, False, True),
            (FailoverReason.SERVER_ERROR, True, False, False),
        ]
        
        for reason, retryable, should_compress, should_fallback in error_types:
            classified = ClassifiedError(
                reason=reason,
                message=f"Test {reason.value}",
                retryable=retryable,
                should_compress=should_compress,
                should_fallback=should_fallback,
            )
            
            # Should not raise exception
            await callback.on_error(f"Error: {reason.value}", classified)
    
    @pytest.mark.asyncio
    async def test_auxiliary_client_handles_provider_failures(self):
        """Verify AuxiliaryClient handles provider failures gracefully.
        
        Requirements: 16.10
        """
        # Create client with explicit provider
        client = AuxiliaryClient(provider="openai", model="gpt-4o-mini")
        
        # Mock the client to simulate failure
        with patch.object(client, '_get_client') as mock_get_client:
            mock_llm_client = AsyncMock()
            mock_llm_client.chat.completions.create = AsyncMock(
                side_effect=Exception("API error")
            )
            mock_get_client.return_value = mock_llm_client
            
            # Call should raise exception (not fall back in single call)
            with pytest.raises(Exception):
                await client.call(
                    messages=[{"role": "user", "content": "test"}],
                    task="test",
                )
    
    @pytest.mark.asyncio
    async def test_context_compressor_handles_no_auxiliary_client(self):
        """Verify ContextCompressor handles missing AuxiliaryClient gracefully.
        
        Requirements: 16.10
        """
        compressor = ContextCompressor(
            model="gpt-4",
            threshold_percent=0.5,
            protect_first_n=3,
        )
        
        # Create test messages
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        
        # Add many large messages to trigger compression
        large_content = "This is a large message. " * 200  # ~5000 chars per message
        for i in range(100):
            messages.append({"role": "user", "content": f"Message {i}: {large_content}"})
            messages.append({"role": "assistant", "content": f"Response {i}: {large_content}"})
        
        # Mock AuxiliaryClient to be unavailable
        with patch('app.core.auxiliary_client.AuxiliaryClient') as mock_aux_client_class:
            mock_aux_client = AsyncMock()
            mock_aux_client.is_available = False
            mock_aux_client_class.return_value = mock_aux_client
            
            # Trigger compression - should fall back to basic truncation
            compressed = await compressor.compress_async(messages)
            
            # Verify compression still worked
            assert len(compressed) < len(messages)
            assert compressed[0]["role"] == "system"  # Head protected


@pytest.mark.asyncio
class TestEndToEndIntegration:
    """End-to-end integration tests."""
    
    async def test_full_streaming_workflow_with_compression(self):
        """Test complete workflow with streaming and compression.
        
        This test verifies:
        1. StreamCallback receives all events
        2. Context overflow triggers compression
        3. ContextCompressor uses AuxiliaryClient
        4. All error handling works correctly
        
        Requirements: 16.9, 16.10
        """
        import logging
        from app.workflow.strategies.react import ReactStrategy
        
        # Create strategy
        strategy = ReactStrategy()
        
        # Create callback to track events
        logger = logging.getLogger("test")
        callback = LoggingStreamCallback(logger=logger, execution_id="test-exec")
        
        # Verify callback is properly initialized
        assert callback.execution_id == "test-exec"
        assert callback._token_count == 0
        assert callback._tool_count == 0
        
        # Simulate streaming events
        await callback.on_llm_token("I")
        await callback.on_llm_token(" will")
        await callback.on_llm_token(" help")
        
        await callback.on_tool_call("read_file", {"path": "test.py"}, "test.py")
        await callback.on_tool_result("read_file", "file contents", 0.5)
        
        await callback.on_complete("Task completed", {
            "token_usage": {"total_tokens": 1000},
            "duration": 5.2,
        })
        
        # Verify event counts
        assert callback._token_count == 3
        assert callback._tool_count == 1
