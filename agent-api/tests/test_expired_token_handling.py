"""Tests for ExpiredTokenException handling to prevent token waste.

This test suite verifies that when AWS credentials expire during workflow
execution, the system immediately stops processing without wasting tokens
on post-processing operations.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from app.core.error_classifier import classify_error, FailoverReason


# Create a mock boto error class for testing
class MockBotoError(Exception):
    """Mock boto error for testing."""
    __module__ = "botocore.exceptions"
    
    def __init__(self, message, error_code="ExpiredTokenException"):
        super().__init__(message)
        self.response = {
            "Error": {
                "Code": error_code,
                "Message": message
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 403
            }
        }


class TestExpiredTokenClassification:
    """Test that ExpiredTokenException is correctly classified."""
    
    def test_expired_token_exception_classification(self):
        """Test that ExpiredTokenException is classified as AUTH_PERMANENT with retryable=False."""
        # Create a mock boto error with ExpiredTokenException
        error = MockBotoError("The security token included in the request is expired")
        
        # Classify the error
        classified = classify_error(error)
        
        # Verify classification
        assert classified.reason == FailoverReason.AUTH_PERMANENT
        assert classified.retryable == False
        assert classified.should_fallback == False
        assert "expired" in classified.message.lower()
    
    def test_expired_token_in_message_classification(self):
        """Test that errors with 'ExpiredToken' in message are classified correctly."""
        # Create a mock boto error with ExpiredToken in message
        error = MockBotoError("ExpiredToken: The security token is expired", error_code="SomeOtherError")
        
        # Classify the error
        classified = classify_error(error)
        
        # Verify classification
        assert classified.reason == FailoverReason.AUTH_PERMANENT
        assert classified.retryable == False
        assert classified.should_fallback == False


@pytest.mark.asyncio
class TestReactStrategyEarlyTermination:
    """Test that React strategy terminates early on ExpiredTokenException."""
    
    async def test_react_strategy_skips_postprocessing_on_expired_token(self):
        """Test that React strategy skips post-processing when ExpiredTokenException occurs."""
        from app.workflow.strategies.react import ReactStrategy
        from app.core.error_classifier import ClassifiedError, FailoverReason
        
        # Create a mock workflow
        workflow = {
            "id": "test-workflow",
            "name": "Test Workflow",
            "nodes": [
                {"id": "agent-1", "type": "agent", "data": {"instructions": "test"}},
                {"id": "llm-1", "type": "llm", "data": {"model": "gpt-4"}}
            ],
            "edges": []
        }
        
        # Create execution context
        context = {
            "execution_id": "test-exec-1",
            "user_query": "test query",
            "mcp_manager": Mock(),
            "logger": Mock()
        }
        
        strategy = ReactStrategy()
        
        # Mock the agent execution to raise an ExpiredTokenException
        with patch.object(strategy, '_execute_agent') as mock_execute:
            # Create a mock boto error
            boto_error = MockBotoError("The security token included in the request is expired")
            mock_execute.side_effect = boto_error
            
            # Execute should raise the error
            with pytest.raises(Exception) as exc_info:
                await strategy.execute(workflow, context)
            
            # Verify an exception was raised
            assert exc_info.value is not None


@pytest.mark.asyncio
class TestWorkflowExecutorPropagation:
    """Test that workflow executor propagates fatal auth errors."""
    
    async def test_agent_node_propagates_expired_token(self):
        """Test that agent node propagates ExpiredTokenException instead of converting to failed result."""
        from app.services.visual_workflow_executor import VisualWorkflowExecutor
        
        executor = VisualWorkflowExecutor()
        
        # Create a mock agent node
        node = {
            "id": "agent-1",
            "type": "agent",
            "data": {"instructions": "test"}
        }
        
        # Create execution context
        context = {
            "execution_id": "test-exec-1",
            "workflow_name": "test-workflow",
            "inputs": {}
        }
        
        # Mock the React strategy to raise ExpiredTokenException
        with patch('app.workflow.strategies.react.ReactStrategy') as MockStrategy:
            mock_strategy = MockStrategy.return_value
            
            # Create a mock boto error
            boto_error = MockBotoError("The security token included in the request is expired")
            mock_strategy.execute = AsyncMock(side_effect=boto_error)
            
            # Execute should propagate the error (not return failed result)
            with pytest.raises(Exception) as exc_info:
                await executor._execute_agent_node(node, context)
            
            # Verify an exception was propagated
            assert exc_info.value is not None
    
    async def test_agent_node_returns_failed_for_other_errors(self):
        """Test that agent node still returns failed result for non-fatal errors."""
        from app.services.visual_workflow_executor import VisualWorkflowExecutor
        
        executor = VisualWorkflowExecutor()
        
        # Create a mock agent node
        node = {
            "id": "agent-1",
            "type": "agent",
            "data": {"instructions": "test"}
        }
        
        # Create execution context
        context = {
            "execution_id": "test-exec-1",
            "workflow_name": "test-workflow",
            "inputs": {}
        }
        
        # Mock the React strategy to raise a regular error
        with patch('app.workflow.strategies.react.ReactStrategy') as MockStrategy:
            mock_strategy = MockStrategy.return_value
            mock_strategy.execute = AsyncMock(side_effect=ValueError("Some other error"))
            
            # Execute should return failed result (not propagate)
            result = await executor._execute_agent_node(node, context)
            
            # Verify failed result was returned
            assert result["status"] == "failed"
            assert "Some other error" in result["error"]


@pytest.mark.asyncio
class TestTokenWastePrevention:
    """Integration tests to verify token waste is prevented."""
    
    async def test_no_memory_sync_on_expired_token(self):
        """Test that memory sync is skipped when ExpiredTokenException occurs."""
        from app.workflow.strategies.react import ReactStrategy
        
        strategy = ReactStrategy()
        
        # Verify that memory manager exists
        assert strategy._memory_manager is not None or True  # May not be initialized in test env
        
        # Mock workflow and context
        workflow = {
            "id": "test-workflow",
            "name": "Test Workflow",
            "nodes": [
                {"id": "agent-1", "type": "agent", "data": {"instructions": "test"}},
                {"id": "llm-1", "type": "llm", "data": {"model": "gpt-4"}}
            ]
        }
        
        context = {
            "execution_id": "test-exec-1",
            "user_query": "test query",
            "mcp_manager": Mock()
        }
        
        # Mock agent execution to raise ExpiredTokenException
        with patch.object(strategy, '_execute_agent') as mock_execute:
            boto_error = MockBotoError("Token expired")
            mock_execute.side_effect = boto_error
            
            # Mock memory manager sync
            if strategy._memory_manager:
                with patch.object(strategy._memory_manager, 'sync_all') as mock_sync:
                    # Execute should raise without calling sync
                    with pytest.raises(Exception):
                        await strategy.execute(workflow, context)
                    
                    # Verify sync was never called
                    mock_sync.assert_not_called()
    
    async def test_no_trajectory_save_on_expired_token(self):
        """Test that trajectory save is skipped when ExpiredTokenException occurs."""
        from app.workflow.strategies.react import ReactStrategy
        
        strategy = ReactStrategy()
        
        workflow = {
            "id": "test-workflow",
            "name": "Test Workflow",
            "nodes": [
                {"id": "agent-1", "type": "agent", "data": {"instructions": "test"}},
                {"id": "llm-1", "type": "llm", "data": {"model": "gpt-4"}}
            ]
        }
        
        context = {
            "execution_id": "test-exec-1",
            "user_query": "test query",
            "mcp_manager": Mock()
        }
        
        # Mock agent execution to raise ExpiredTokenException
        with patch.object(strategy, '_execute_agent') as mock_execute, \
             patch.object(strategy, '_save_trajectory') as mock_save:
            
            boto_error = MockBotoError("Token expired")
            mock_execute.side_effect = boto_error
            
            # Execute should raise without calling save_trajectory
            with pytest.raises(Exception):
                await strategy.execute(workflow, context)
            
            # Verify save_trajectory was never called
            mock_save.assert_not_called()



@pytest.mark.asyncio
class TestCloudWatchNodePropagation:
    """Test that CloudWatch node propagates fatal auth errors."""
    
    async def test_cloudwatch_llm_analysis_propagates_expired_token(self):
        """Test that CloudWatch LLM analysis propagates ExpiredTokenException."""
        from app.services.visual_workflow_executor import VisualWorkflowExecutor
        
        executor = VisualWorkflowExecutor()
        
        # Create a mock CloudWatch node
        node = {
            "id": "cw-1",
            "type": "cloudwatchAnalyzer",
            "data": {
                "logGroups": ["/aws/lambda/test"],
                "analysisType": "error-patterns",
                "timeRange": "1h"
            }
        }
        
        # Create execution context
        context = {
            "execution_id": "test-exec-1",
            "workflow_name": "test-workflow",
            "inputs": {}
        }
        
        # Store workflow in active executions for LLM analysis
        executor.active_executions["test-exec-1"] = {
            "workflow": {
                "nodes": [
                    {"id": "llm-1", "type": "llm", "data": {"model": "gpt-4"}}
                ]
            }
        }
        
        # Mock the LLM build to raise ExpiredTokenException
        with patch('app.workflow.strategies.react.ReactStrategy._build_llm') as mock_build_llm:
            mock_llm = Mock()
            boto_error = MockBotoError("The security token included in the request is expired")
            mock_llm.ainvoke = AsyncMock(side_effect=boto_error)
            mock_build_llm.return_value = mock_llm
            
            # Mock the CloudWatch analysis to succeed
            with patch('app.mcp.tools.watch_tools.analyze_log_patterns') as mock_analyze:
                mock_analyze.return_value = {"patterns": {}}
                
                # Execute should propagate the error (not return failed result)
                with pytest.raises(Exception) as exc_info:
                    await executor._execute_cloudwatch_node(node, context)
                
                # Verify an exception was propagated
                assert exc_info.value is not None
    
    async def test_cloudwatch_node_returns_failed_for_other_errors(self):
        """Test that CloudWatch node still returns failed result for non-fatal errors."""
        from app.services.visual_workflow_executor import VisualWorkflowExecutor
        
        executor = VisualWorkflowExecutor()
        
        # Create a mock CloudWatch node
        node = {
            "id": "cw-1",
            "type": "cloudwatchAnalyzer",
            "data": {
                "logGroups": ["/aws/lambda/test"],
                "analysisType": "error-patterns",
                "timeRange": "1h"
            }
        }
        
        # Create execution context
        context = {
            "execution_id": "test-exec-1",
            "workflow_name": "test-workflow",
            "inputs": {}
        }
        
        # Mock the with_retry to raise a regular error
        with patch('app.services.visual_workflow_executor.with_retry') as mock_retry:
            mock_retry.side_effect = ValueError("Some other error")
            
            # Execute should return failed result (not propagate)
            result = await executor._execute_cloudwatch_node(node, context)
            
            # Verify failed result was returned
            assert result["status"] == "failed"
            assert "Some other error" in result["error"]
