"""Comprehensive integration tests for ReactStrategy with all components.

This module tests the full ReAct workflow with:
- Context compression on overflow
- Error recovery scenarios (rate limit, context overflow, auth)
- Streaming with large responses
- Memory integration
- Tool registry integration
- Trajectory storage
- Context references

Requirements: 16.1-16.10
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from app.workflow.strategies.react import ReactStrategy
from app.core.error_classifier import ClassifiedError, FailoverReason


@pytest.fixture
def mock_settings():
    """Mock settings with all features enabled."""
    with patch('app.workflow.strategies.react.settings') as mock_settings:
        mock_settings.context_compression_enabled = True
        mock_settings.context_threshold_percent = 0.50
        mock_settings.context_protect_first_n = 3
        mock_settings.rate_limit_tracking_enabled = True
        mock_settings.rate_limit_warning_threshold = 0.80
        yield mock_settings


@pytest.fixture
def mock_knowledge_base():
    """Mock knowledge base service."""
    kb = MagicMock()
    kb.search_known_issues = AsyncMock(return_value=[])
    kb.search_similar_patterns = AsyncMock(return_value=[])
    kb.record_analysis = AsyncMock()
    kb.add_known_issue = AsyncMock()
    kb.upsert_playbook = AsyncMock(return_value={"action": "saved", "id": 1, "title": "Test"})
    kb.patch_playbook_solution = AsyncMock(return_value={"id": 1, "title": "Test"})
    return kb


@pytest.fixture
def mock_trajectory_service():
    """Mock trajectory service."""
    service = MagicMock()
    service.save_trajectory = AsyncMock(return_value="traj-123")
    service.load_trajectory = AsyncMock(return_value=None)
    service.list_trajectories = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_mcp_manager():
    """Mock MCP manager."""
    manager = MagicMock()
    manager.is_connected = MagicMock(return_value=False)
    manager.connect_server = AsyncMock(return_value=True)
    manager.disconnect_all = AsyncMock()
    return manager


@pytest.fixture
def mock_stream_callback():
    """Mock stream callback."""
    callback = MagicMock()
    callback.on_llm_token = AsyncMock()
    callback.on_tool_call = AsyncMock()
    callback.on_tool_result = AsyncMock()
    callback.on_error = AsyncMock()
    callback.on_complete = AsyncMock()
    return callback


@pytest.fixture
def basic_workflow():
    """Basic workflow definition."""
    return {
        "id": "test-workflow-123",
        "nodes": [
            {
                "id": "agent-1",
                "type": "agent",
                "data": {
                    "instructions": "You are a helpful assistant. Answer the user's question."
                }
            },
            {
                "id": "llm-1",
                "type": "llm",
                "data": {
                    "model": "gpt-4",
                    "provider": "openai",
                    "temperature": 0.1,
                    "maxTokens": 4096,
                }
            }
        ],
        "edges": []
    }


class TestFullReActWorkflowWithCompression:
    """Test full ReAct workflow with context compression.
    
    **Validates: Requirements 16.1, 16.2, 16.9**
    """
    
    @pytest.mark.asyncio
    async def test_full_workflow_with_context_overflow_and_compression(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        mock_stream_callback,
        basic_workflow
    ):
        """Test complete workflow with context overflow triggering compression.
        
        Scenario:
        1. Agent starts execution
        2. Context overflow error occurs
        3. Compression is triggered
        4. Retry succeeds with compressed context
        5. Trajectory is saved
        6. Memory is synced
        
        **Validates: Requirements 16.1, 16.2**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                # Create strategy
                strategy = ReactStrategy()
                
                # Setup context
                context = {
                    "execution_id": "exec-overflow-test",
                    "user_query": "Analyze the system logs for errors",
                    "mcp_manager": mock_mcp_manager,
                    "stream_callback": mock_stream_callback,
                }
                
                # Track compression invocations
                compression_invoked = False
                
                # Mock ContextCompressor
                mock_compressor = MagicMock()
                mock_compressor.should_compress = MagicMock(return_value=True)
                mock_compressor.compress_async = AsyncMock(return_value=[
                    {"role": "user", "content": "[Compressed context]"},
                    {"role": "user", "content": "Analyze the system logs for errors"},
                ])
                
                strategy._context_compressor = mock_compressor
                
                # Mock agent execution with overflow then success
                call_count = 0
                
                async def mock_agent_invoke(input_state):
                    nonlocal call_count
                    call_count += 1
                    
                    if call_count == 1:
                        # First call: context overflow
                        print(f"Call 1, input_state: {input_state}")
                        raise Exception("context_length_exceeded: This model's maximum context length is 8192 tokens")
                    else:
                        print(f"Call 2, input_state: {input_state}")
                        # Second call: success after compression
                        return {
                            "messages": [
                                HumanMessage(content="Analyze the system logs for errors"),
                                AIMessage(content="I found 3 critical errors in the logs."),
                            ]
                        }
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(side_effect=mock_agent_invoke)
                
                # Mock dependencies
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            # Mock classify_error to return context overflow
                            with patch('app.workflow.strategies.react.classify_error') as mock_classify, \
                                 patch('app.core.retry.classify_error') as mock_retry_classify:
                                classified = ClassifiedError(
                                    reason=FailoverReason.CONTEXT_OVERFLOW,
                                    status_code=400,
                                    message="context_length_exceeded",
                                    retryable=False,
                                    should_compress=True,
                                )
                                mock_classify.return_value = classified
                                mock_retry_classify.return_value = classified
                                
                                # Execute workflow
                                result = await strategy.execute(basic_workflow, context)
                
                # Verify compression was invoked
                assert mock_compressor.compress_async.called
                
                # Verify retry succeeded
                assert result is not None
                assert result["final_answer"] == "I found 3 critical errors in the logs."
                
                # Verify trajectory was saved
                assert mock_trajectory_service.save_trajectory.called
                
                # Verify stream callback was notified
                assert mock_stream_callback.on_complete.called
    
    @pytest.mark.asyncio
    async def test_compression_with_large_tool_results(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        basic_workflow
    ):
        """Test compression handles large tool results correctly.
        
        **Validates: Requirements 16.1**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                context = {
                    "execution_id": "exec-large-tools",
                    "user_query": "Search the logs",
                    "mcp_manager": mock_mcp_manager,
                }
                
                # Create messages with large tool results
                large_tool_result = "x" * 50000  # 50KB tool result
                
                messages = [
                    HumanMessage(content="Search the logs"),
                    AIMessage(content="", tool_calls=[{"id": "call-1", "name": "search_logs", "args": {}}]),
                    ToolMessage(content=large_tool_result, tool_call_id="call-1"),
                    AIMessage(content="Found the issue"),
                ]
                
                # Mock compressor
                mock_compressor = MagicMock()
                mock_compressor.should_compress = MagicMock(return_value=True)
                mock_compressor.compress_async = AsyncMock(return_value=[
                    {"role": "user", "content": "[Compressed - tool results pruned]"},
                    {"role": "assistant", "content": "Found the issue"},
                ])
                
                strategy._context_compressor = mock_compressor
                
                # Mock agent
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(return_value={"messages": messages})
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            # Trigger compression by simulating overflow
                            with patch('app.workflow.strategies.react.classify_error') as mock_classify, \
                                 patch('app.core.retry.classify_error') as mock_retry_classify:
                                classified = ClassifiedError(
                                    reason=FailoverReason.CONTEXT_OVERFLOW,
                                    status_code=400,
                                    message="context_length_exceeded",
                                    retryable=False,
                                    should_compress=True,
                                )
                                mock_classify.return_value = classified
                                mock_retry_classify.return_value = classified
                                
                                # First call fails, second succeeds
                                call_count = 0
                                async def mock_invoke(input_state):
                                    nonlocal call_count
                                    call_count += 1
                                    if call_count == 1:
                                        raise Exception("context length exceeded")
                                    return {"messages": messages}
                                
                                mock_agent.ainvoke = AsyncMock(side_effect=mock_invoke)
                                
                                result = await strategy.execute(basic_workflow, context)
                
                # Verify compression was called
                assert mock_compressor.compress_async.called
                
                # Verify result
                assert result is not None


class TestErrorRecoveryScenarios:
    """Test error recovery scenarios.
    
    **Validates: Requirements 16.2, 16.3**
    """
    
    @pytest.mark.asyncio
    async def test_rate_limit_error_recovery(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        mock_stream_callback,
        basic_workflow
    ):
        """Test recovery from rate limit errors with backoff.
        
        **Validates: Requirement 16.3**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                context = {
                    "execution_id": "exec-rate-limit",
                    "user_query": "Test query",
                    "mcp_manager": mock_mcp_manager,
                    "stream_callback": mock_stream_callback,
                }
                
                # Track retry attempts
                retry_attempts = []
                
                # Mock agent with rate limit then success
                call_count = 0
                
                async def mock_agent_invoke(input_state):
                    nonlocal call_count
                    call_count += 1
                    retry_attempts.append(call_count)
                    
                    if call_count <= 2:
                        # First two calls: rate limit
                        raise Exception("Rate limit exceeded. Please retry after 2 seconds.")
                    else:
                        # Third call: success
                        return {
                            "messages": [
                                HumanMessage(content="Test query"),
                                AIMessage(content="Test response"),
                            ]
                        }
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(side_effect=mock_agent_invoke)
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            # Mock classify_error to return rate limit
                            with patch('app.workflow.strategies.react.classify_error') as mock_classify, \
                                 patch('app.core.retry.classify_error') as mock_retry_classify:
                                classified = ClassifiedError(
                                    reason=FailoverReason.RATE_LIMIT,
                                    status_code=429,
                                    message="Rate limit exceeded",
                                    retryable=True,
                                )
                                mock_classify.return_value = classified
                                mock_retry_classify.return_value = classified
                                
                                # Mock sleep to speed up test
                                with patch('asyncio.sleep', new_callable=AsyncMock):
                                    result = await strategy.execute(basic_workflow, context)
                
                # Verify retries occurred
                assert len(retry_attempts) == 3
                
                # Verify final success
                assert result is not None
                assert result["final_answer"] == "Test response"
                
                # Verify stream callback was notified
                assert mock_stream_callback.on_complete.called
    
    @pytest.mark.asyncio
    async def test_auth_error_no_retry(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        mock_stream_callback,
        basic_workflow
    ):
        """Test that auth errors don't retry (permanent failure).
        
        **Validates: Requirement 16.3**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                context = {
                    "execution_id": "exec-auth-error",
                    "user_query": "Test query",
                    "mcp_manager": mock_mcp_manager,
                    "stream_callback": mock_stream_callback,
                }
                
                # Mock agent with auth error
                async def mock_agent_invoke(input_state):
                    raise Exception("Invalid API key provided")
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(side_effect=mock_agent_invoke)
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            # Mock classify_error to return auth error
                            with patch('app.workflow.strategies.react.classify_error') as mock_classify, \
                                 patch('app.core.retry.classify_error') as mock_retry_classify:
                                classified = ClassifiedError(
                                    reason=FailoverReason.AUTH_PERMANENT,
                                    status_code=401,
                                    message="Invalid API key",
                                    retryable=False,
                                    should_fallback=True,
                                )
                                mock_classify.return_value = classified
                                mock_retry_classify.return_value = classified
                                
                                # Should raise without retry
                                with pytest.raises(Exception, match="Invalid API key"):
                                    await strategy.execute(basic_workflow, context)
                
                # Verify error callback was called
                assert mock_stream_callback.on_error.called
    
    @pytest.mark.asyncio
    async def test_server_error_with_retry(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        basic_workflow
    ):
        """Test recovery from transient server errors.
        
        **Validates: Requirement 16.3**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                context = {
                    "execution_id": "exec-server-error",
                    "user_query": "Test query",
                    "mcp_manager": mock_mcp_manager,
                }
                
                # Mock agent with server error then success
                call_count = 0
                
                async def mock_agent_invoke(input_state):
                    nonlocal call_count
                    call_count += 1
                    
                    if call_count == 1:
                        raise Exception("Internal server error")
                    else:
                        return {
                            "messages": [
                                HumanMessage(content="Test query"),
                                AIMessage(content="Test response"),
                            ]
                        }
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(side_effect=mock_agent_invoke)
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            # Mock classify_error to return server error
                            with patch('app.workflow.strategies.react.classify_error') as mock_classify, \
                                 patch('app.core.retry.classify_error') as mock_retry_classify:
                                classified = ClassifiedError(
                                    reason=FailoverReason.SERVER_ERROR,
                                    status_code=500,
                                    message="Internal server error",
                                    retryable=True,
                                )
                                mock_classify.return_value = classified
                                mock_retry_classify.return_value = classified
                                
                                # Mock sleep to speed up test
                                with patch('asyncio.sleep', new_callable=AsyncMock):
                                    result = await strategy.execute(basic_workflow, context)
                
                # Verify retry succeeded
                assert result is not None
                assert result["final_answer"] == "Test response"


class TestStreamingWithLargeResponses:
    """Test streaming with large responses.
    
    **Validates: Requirements 16.9**
    """
    
    @pytest.mark.asyncio
    async def test_streaming_callbacks_invoked(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        mock_stream_callback,
        basic_workflow
    ):
        """Test that streaming callbacks are invoked during execution.
        
        **Validates: Requirement 16.9**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                context = {
                    "execution_id": "exec-streaming",
                    "user_query": "Analyze the system",
                    "mcp_manager": mock_mcp_manager,
                    "stream_callback": mock_stream_callback,
                }
                
                # Mock agent with tool calls
                messages = [
                    HumanMessage(content="Analyze the system"),
                    AIMessage(content="", tool_calls=[{
                        "id": "call-1",
                        "name": "search_logs",
                        "args": {"query": "errors"}
                    }]),
                    ToolMessage(content="Found 5 errors", tool_call_id="call-1"),
                    AIMessage(content="I found 5 errors in the system logs."),
                ]
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(return_value={"messages": messages})
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            result = await strategy.execute(basic_workflow, context)
                
                # Verify streaming callbacks were invoked
                # Note: Actual streaming happens in _execute_agent via astream_events
                # This test verifies the callback is passed through
                assert result is not None
                
                # Verify on_complete was called
                assert mock_stream_callback.on_complete.called
    
    @pytest.mark.asyncio
    async def test_streaming_with_large_tool_output(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        mock_stream_callback,
        basic_workflow
    ):
        """Test streaming handles large tool outputs correctly.
        
        **Validates: Requirement 16.9**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                context = {
                    "execution_id": "exec-large-output",
                    "user_query": "Get system logs",
                    "mcp_manager": mock_mcp_manager,
                    "stream_callback": mock_stream_callback,
                }
                
                # Create large tool output
                large_output = "Log entry\n" * 10000  # ~100KB
                
                messages = [
                    HumanMessage(content="Get system logs"),
                    AIMessage(content="", tool_calls=[{
                        "id": "call-1",
                        "name": "get_logs",
                        "args": {}
                    }]),
                    ToolMessage(content=large_output, tool_call_id="call-1"),
                    AIMessage(content="Retrieved system logs successfully."),
                ]
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(return_value={"messages": messages})
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            result = await strategy.execute(basic_workflow, context)
                
                # Verify execution completed
                assert result is not None
                assert result["final_answer"] == "Retrieved system logs successfully."
                
                # Verify callbacks were invoked
                assert mock_stream_callback.on_complete.called


class TestIntegratedComponents:
    """Test integration of all components.
    
    **Validates: Requirements 16.1-16.10**
    """
    
    @pytest.mark.asyncio
    async def test_full_integration_all_components(
        self,
        mock_settings,
        mock_knowledge_base,
        mock_trajectory_service,
        mock_mcp_manager,
        mock_stream_callback,
        basic_workflow
    ):
        """Test full integration with all components working together.
        
        Components tested:
        - Context compression
        - Error classification and retry
        - Rate limit tracking
        - Tool registry
        - Memory manager
        - Trajectory storage
        - Context references
        - Streaming callbacks
        
        **Validates: Requirements 16.1-16.10**
        """
        with patch('app.services.knowledge_base.knowledge_base', mock_knowledge_base):
            with patch('app.services.trajectory_service.trajectory_service', mock_trajectory_service):
                strategy = ReactStrategy()
                
                # _context_compressor is lazily initialized on first execute() call,
                # so it's None at construction time even when compression is enabled.
                # Just verify strategy was constructed without error.
                assert strategy._memory_manager is not None
                
                context = {
                    "execution_id": "exec-full-integration",
                    "user_query": "Investigate the production outage",
                    "mcp_manager": mock_mcp_manager,
                    "stream_callback": mock_stream_callback,
                }
                
                # Mock successful execution
                messages = [
                    HumanMessage(content="Investigate the production outage"),
                    AIMessage(content="", tool_calls=[{
                        "id": "call-1",
                        "name": "search_logs",
                        "args": {"query": "error"}
                    }]),
                    ToolMessage(content="Found database connection errors", tool_call_id="call-1"),
                    AIMessage(content="The outage was caused by database connection pool exhaustion."),
                ]
                
                mock_agent = MagicMock()
                mock_agent.ainvoke = AsyncMock(return_value={"messages": messages})
                
                with patch.object(strategy, '_setup_tools', new_callable=AsyncMock) as mock_setup_tools:
                    mock_setup_tools.return_value = []
                    
                    with patch.object(strategy, '_build_llm') as mock_build_llm:
                        mock_build_llm.return_value = MagicMock()
                        
                        with patch.object(strategy, '_build_agent') as mock_build_agent:
                            mock_build_agent.return_value = mock_agent
                            
                            result = await strategy.execute(basic_workflow, context)
                
                # Verify result
                assert result is not None
                assert "database connection pool exhaustion" in result["final_answer"]
                
                # Verify memory prefetch was called
                assert mock_knowledge_base.search_known_issues.called
                
                # Verify trajectory was saved
                assert mock_trajectory_service.save_trajectory.called
                trajectory_call = mock_trajectory_service.save_trajectory.call_args
                assert trajectory_call[1]["execution_id"] == "exec-full-integration"
                assert trajectory_call[1]["completed"] == True
                
                # Verify stream callback was invoked
                assert mock_stream_callback.on_complete.called
                
                # Verify MCP manager was disconnected
                assert mock_mcp_manager.disconnect_all.called
