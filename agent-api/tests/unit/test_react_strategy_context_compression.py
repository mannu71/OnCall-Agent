"""
Unit tests for ReactStrategy ContextCompressor integration.

Tests the initialization and integration of ContextCompressor into ReactStrategy
for handling context overflow errors.

Requirements: 16.1
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, List

from app.workflow.strategies.react import ReactStrategy
from app.config import Settings


class TestReactStrategyContextCompression:
    """Test suite for ReactStrategy ContextCompressor integration."""
    
    def test_init_with_compression_enabled(self):
        """Test ReactStrategy initialization when context compression is enabled.
        
        **Validates: Requirements 16.1**
        """
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = True
            mock_settings.context_threshold_percent = 0.50
            mock_settings.context_protect_first_n = 3
            
            strategy = ReactStrategy()
            
            # Should initialize with compression support
            # Note: _context_compressor is None initially and will be created on first use
            assert hasattr(strategy, '_context_compressor')
    
    def test_init_with_compression_disabled(self):
        """Test ReactStrategy initialization when context compression is disabled."""
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = False
            
            strategy = ReactStrategy()
            
            # Should not initialize compressor
            assert strategy._context_compressor is None
    
    @pytest.mark.asyncio
    async def test_on_context_overflow_with_compressor(self):
        """Test _on_context_overflow callback uses ContextCompressor when available.
        
        **Validates: Requirements 16.1**
        """
        from langchain_core.messages import HumanMessage, AIMessage
        
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = True
            mock_settings.context_threshold_percent = 0.50
            mock_settings.context_protect_first_n = 3
            
            strategy = ReactStrategy()
            
            # Mock the ContextCompressor
            mock_compressor = AsyncMock()
            mock_compressor.compress_async = AsyncMock(return_value=[
                {"role": "user", "content": "test query"},
                {"role": "assistant", "content": "[Context Summary]\n\nCompressed content"},
            ])
            
            # Patch where it's imported (inside the method)
            with patch('app.core.context_compression.ContextCompressor', return_value=mock_compressor):
                with patch('app.core.model_metadata.estimate_messages_tokens_rough', return_value=10000):
                    input_state = {
                        "messages": [
                            HumanMessage(content="test query"),
                            AIMessage(content="test response"),
                        ]
                    }
                    
                    llm_config = {
                        "model": "gpt-4",
                        "provider": "openai",
                        "base_url": "",
                    }
                    
                    logger_mock = Mock()
                    
                    result = await strategy._on_context_overflow(
                        input_state, llm_config, logger_mock, "test-exec-id"
                    )
                    
                    # Should return compressed messages
                    assert "messages" in result
                    assert len(result["messages"]) == 2
                    
                    # Should have called compress_async
                    mock_compressor.compress_async.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_on_context_overflow_fallback_to_basic(self):
        """Test _on_context_overflow falls back to basic compaction on error."""
        from langchain_core.messages import HumanMessage, AIMessage
        
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = True
            
            strategy = ReactStrategy()
            
            # Mock ContextCompressor to raise an error
            with patch('app.core.context_compression.ContextCompressor', side_effect=Exception("Test error")):
                with patch('app.workflow.strategies.react._compact_input_state') as mock_compact:
                    mock_compact.return_value = {"messages": [HumanMessage(content="compacted")]}
                    
                    input_state = {
                        "messages": [
                            HumanMessage(content="test query"),
                            AIMessage(content="test response"),
                        ]
                    }
                    
                    llm_config = {"model": "gpt-4"}
                    logger_mock = Mock()
                    
                    result = await strategy._on_context_overflow(
                        input_state, llm_config, logger_mock, "test-exec-id"
                    )
                    
                    # Should fall back to basic compaction
                    mock_compact.assert_called_once_with(input_state)
                    assert result == {"messages": [HumanMessage(content="compacted")]}
    
    @pytest.mark.asyncio
    async def test_on_context_overflow_without_compressor(self):
        """Test _on_context_overflow uses basic compaction when compressor disabled."""
        from langchain_core.messages import HumanMessage, AIMessage
        
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = False
            
            strategy = ReactStrategy()
            
            with patch('app.workflow.strategies.react._compact_input_state') as mock_compact:
                mock_compact.return_value = {"messages": [HumanMessage(content="compacted")]}
                
                input_state = {
                    "messages": [
                        HumanMessage(content="test query"),
                        AIMessage(content="test response"),
                    ]
                }
                
                llm_config = {"model": "gpt-4"}
                logger_mock = Mock()
                
                result = await strategy._on_context_overflow(
                    input_state, llm_config, logger_mock, "test-exec-id"
                )
                
                # Should use basic compaction
                mock_compact.assert_called_once_with(input_state)
                assert result == {"messages": [HumanMessage(content="compacted")]}
    
    @pytest.mark.asyncio
    async def test_execute_agent_passes_llm_config(self):
        """Test that _execute_agent receives llm_config for compression callback."""
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = True
            
            strategy = ReactStrategy()
            
            # Mock the agent and dependencies
            mock_agent = AsyncMock()
            mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
            
            with patch('app.workflow.strategies.react.with_retry') as mock_retry:
                mock_retry.return_value = {"messages": []}
                
                logger_mock = Mock()
                llm_config = {"model": "gpt-4", "provider": "openai"}
                
                result = await strategy._execute_agent(
                    mock_agent,
                    "test query",
                    logger_mock,
                    "test-exec-id",
                    None,
                    llm_config
                )
                
                # Should complete without error
                assert "messages" in result
                assert "final_answer" in result
                assert "tool_calls" in result
    
    def test_context_compressor_lazy_initialization(self):
        """Test that ContextCompressor is lazily initialized on first use."""
        with patch('app.workflow.strategies.react.settings') as mock_settings:
            mock_settings.context_compression_enabled = True
            
            strategy = ReactStrategy()
            
            # Should not be initialized yet (lazy initialization)
            # The compressor will be created when _on_context_overflow is called
            assert hasattr(strategy, '_context_compressor')
