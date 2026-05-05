"""
Unit tests for ReactStrategy context references integration.

Tests verify that @file, @folder, @url, @diff, @staged, @git references
are preprocessed correctly and integrated into the agent execution flow.

Requirements: 16.8
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from app.workflow.strategies.react import ReactStrategy
from app.core.context_references import ContextReferenceResult, ContextReference


@pytest.fixture
def mock_db_repository():
    """Mock database repository."""
    with patch("app.workflow.strategies.react.db_repository") as mock:
        mock.get_llm_config = AsyncMock(return_value={
            "provider": "openai",
            "model": "gpt-4",
            "temperature": 0.1,
            "max_tokens": 4096,
        })
        mock.list_llm_configs = AsyncMock(return_value={})
        mock.get_model_key = AsyncMock(return_value={
            "api_key": "test-key",
        })
        yield mock


@pytest.fixture
def mock_mcp_manager():
    """Mock MCP manager."""
    manager = MagicMock()
    manager.disconnect_all = AsyncMock()
    return manager


@pytest.fixture
def sample_workflow():
    """Sample workflow definition."""
    return {
        "id": "test-workflow",
        "nodes": [
            {
                "id": "agent-1",
                "type": "agent",
                "data": {
                    "instructions": "Test agent",
                }
            },
            {
                "id": "llm-1",
                "type": "llm",
                "data": {
                    "provider": "openai",
                    "model": "gpt-4",
                    "temperature": 0.1,
                }
            }
        ],
        "edges": []
    }


@pytest.mark.asyncio
async def test_context_references_expanded_successfully(
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that context references are expanded and integrated into query.
    
    **Validates: Requirements 16.8**
    """
    strategy = ReactStrategy()
    
    # Mock context reference preprocessing
    mock_ref_result = ContextReferenceResult(
        message="Expanded query with file content",
        original_message="Check @file:test.py",
        references=[
            ContextReference(
                raw="@file:test.py",
                kind="file",
                target="test.py",
                start=6,
                end=20,
            )
        ],
        warnings=[],
        injected_tokens=100,
        expanded=True,
        blocked=False,
    )
    
    with patch("app.core.context_references.preprocess_context_references_async", new_callable=AsyncMock) as mock_preprocess:
        with patch("app.core.model_metadata.get_model_context_length", new_callable=AsyncMock) as mock_context_length:
            with patch.object(strategy, "_setup_tools", return_value=[]):
                with patch.object(strategy, "_build_llm") as mock_build_llm:
                    with patch.object(strategy, "_build_agent") as mock_build_agent:
                        with patch.object(strategy, "_execute_agent") as mock_execute:
                            with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                                with patch.object(strategy, "_save_trajectory", new_callable=AsyncMock):
                                    # Setup mocks
                                    mock_preprocess.return_value = mock_ref_result
                                    mock_context_length.return_value = 128000
                                    mock_build_llm.return_value = MagicMock()
                                    mock_build_agent.return_value = MagicMock()
                                    mock_execute.return_value = {
                                        "final_answer": "Test answer",
                                        "messages": [],
                                        "tool_calls": [],
                                    }
                                    
                                    # Execute workflow
                                    context = {
                                        "execution_id": "exec-123",
                                        "user_query": "Check @file:test.py",
                                        "mcp_manager": mock_mcp_manager,
                                        "logger": MagicMock(),
                                        "cwd": "/test/workspace",
                                    }
                                    
                                    result = await strategy.execute(sample_workflow, context)
                                    
                                    # Verify preprocessing was called
                                    mock_preprocess.assert_called_once()
                                    call_args = mock_preprocess.call_args
                                    
                                    # Verify correct parameters
                                    assert call_args.kwargs["cwd"] == "/test/workspace"
                                    assert call_args.kwargs["context_length"] == 128000
                                    
                                    # Verify agent was called with expanded query
                                    mock_execute.assert_called_once()
                                    agent_call_args = mock_execute.call_args
                                    assert agent_call_args[0][1] == "Expanded query with file content"


@pytest.mark.asyncio
async def test_context_references_with_warnings(
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that context reference warnings are logged but don't block execution.
    
    **Validates: Requirements 16.8**
    """
    strategy = ReactStrategy()
    
    # Mock context reference preprocessing with warnings
    mock_ref_result = ContextReferenceResult(
        message="Expanded query",
        original_message="Check @file:test.py",
        references=[
            ContextReference(
                raw="@file:test.py",
                kind="file",
                target="test.py",
                start=6,
                end=20,
            )
        ],
        warnings=["@ context injection warning: 30000 tokens exceeds the 25% soft limit (25000)."],
        injected_tokens=30000,
        expanded=True,
        blocked=False,
    )
    
    mock_logger = MagicMock()
    
    with patch("app.core.context_references.preprocess_context_references_async", new_callable=AsyncMock) as mock_preprocess:
        with patch("app.core.model_metadata.get_model_context_length", new_callable=AsyncMock) as mock_context_length:
            with patch.object(strategy, "_setup_tools", return_value=[]):
                with patch.object(strategy, "_build_llm") as mock_build_llm:
                    with patch.object(strategy, "_build_agent") as mock_build_agent:
                        with patch.object(strategy, "_execute_agent") as mock_execute:
                            with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                                with patch.object(strategy, "_save_trajectory", new_callable=AsyncMock):
                                    mock_preprocess.return_value = mock_ref_result
                                    mock_context_length.return_value = 100000
                                    mock_build_llm.return_value = MagicMock()
                                    mock_build_agent.return_value = MagicMock()
                                    mock_execute.return_value = {
                                        "final_answer": "Test answer",
                                        "messages": [],
                                        "tool_calls": [],
                                    }
                                    
                                    context = {
                                        "execution_id": "exec-456",
                                        "user_query": "Check @file:test.py",
                                        "mcp_manager": mock_mcp_manager,
                                        "logger": mock_logger,
                                    }
                                    
                                    result = await strategy.execute(sample_workflow, context)
                                    
                                    # Verify warning was logged
                                    assert mock_logger.warning.called
                                    warning_calls = [call for call in mock_logger.warning.call_args_list 
                                                   if "Context reference warning" in str(call)]
                                    assert len(warning_calls) > 0
                                    
                                    # Verify execution completed successfully
                                    assert result["final_answer"] == "Test answer"


@pytest.mark.asyncio
async def test_context_references_blocked_by_hard_limit(
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that context references blocked by hard limit don't break execution.
    
    **Validates: Requirements 16.8**
    """
    strategy = ReactStrategy()
    
    # Mock context reference preprocessing blocked by hard limit
    mock_ref_result = ContextReferenceResult(
        message="Check @file:test.py",  # Original message unchanged
        original_message="Check @file:test.py",
        references=[
            ContextReference(
                raw="@file:test.py",
                kind="file",
                target="test.py",
                start=6,
                end=20,
            )
        ],
        warnings=["@ context injection refused: 60000 tokens exceeds the 50% hard limit (50000)."],
        injected_tokens=60000,
        expanded=False,
        blocked=True,
    )
    
    mock_logger = MagicMock()
    
    with patch("app.core.context_references.preprocess_context_references_async", new_callable=AsyncMock) as mock_preprocess:
        with patch("app.core.model_metadata.get_model_context_length", new_callable=AsyncMock) as mock_context_length:
            with patch.object(strategy, "_setup_tools", return_value=[]):
                with patch.object(strategy, "_build_llm") as mock_build_llm:
                    with patch.object(strategy, "_build_agent") as mock_build_agent:
                        with patch.object(strategy, "_execute_agent") as mock_execute:
                            with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                                with patch.object(strategy, "_save_trajectory", new_callable=AsyncMock):
                                    mock_preprocess.return_value = mock_ref_result
                                    mock_context_length.return_value = 100000
                                    mock_build_llm.return_value = MagicMock()
                                    mock_build_agent.return_value = MagicMock()
                                    mock_execute.return_value = {
                                        "final_answer": "Test answer",
                                        "messages": [],
                                        "tool_calls": [],
                                    }
                                    
                                    context = {
                                        "execution_id": "exec-789",
                                        "user_query": "Check @file:test.py",
                                        "mcp_manager": mock_mcp_manager,
                                        "logger": mock_logger,
                                    }
                                    
                                    result = await strategy.execute(sample_workflow, context)
                                    
                                    # Verify blocked warning was logged
                                    assert mock_logger.warning.called
                                    blocked_calls = [call for call in mock_logger.warning.call_args_list 
                                                   if "hard limit exceeded" in str(call)]
                                    assert len(blocked_calls) > 0
                                    
                                    # Verify execution completed with original query
                                    assert result["final_answer"] == "Test answer"


@pytest.mark.asyncio
async def test_context_references_failure_does_not_break_execution(
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that context reference preprocessing failure doesn't break execution.
    
    **Validates: Requirements 16.8**
    """
    strategy = ReactStrategy()
    
    mock_logger = MagicMock()
    
    with patch("app.core.context_references.preprocess_context_references_async", new_callable=AsyncMock) as mock_preprocess:
        with patch("app.core.model_metadata.get_model_context_length", new_callable=AsyncMock) as mock_context_length:
            with patch.object(strategy, "_setup_tools", return_value=[]):
                with patch.object(strategy, "_build_llm") as mock_build_llm:
                    with patch.object(strategy, "_build_agent") as mock_build_agent:
                        with patch.object(strategy, "_execute_agent") as mock_execute:
                            with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                                with patch.object(strategy, "_save_trajectory", new_callable=AsyncMock):
                                    # Make preprocessing fail
                                    mock_preprocess.side_effect = Exception("File not found")
                                    mock_context_length.return_value = 128000
                                    mock_build_llm.return_value = MagicMock()
                                    mock_build_agent.return_value = MagicMock()
                                    mock_execute.return_value = {
                                        "final_answer": "Test answer",
                                        "messages": [],
                                        "tool_calls": [],
                                    }
                                    
                                    context = {
                                        "execution_id": "exec-fail",
                                        "user_query": "Check @file:test.py",
                                        "mcp_manager": mock_mcp_manager,
                                        "logger": mock_logger,
                                    }
                                    
                                    # Should not raise exception
                                    result = await strategy.execute(sample_workflow, context)
                                    
                                    # Verify failure was logged
                                    assert mock_logger.warning.called
                                    failure_calls = [call for call in mock_logger.warning.call_args_list 
                                                   if "Context reference preprocessing failed" in str(call)]
                                    assert len(failure_calls) > 0
                                    
                                    # Verify execution completed successfully
                                    assert result["final_answer"] == "Test answer"


@pytest.mark.asyncio
async def test_context_references_with_multiple_references(
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that multiple context references are all expanded.
    
    **Validates: Requirements 16.8**
    """
    strategy = ReactStrategy()
    
    # Mock context reference preprocessing with multiple references
    mock_ref_result = ContextReferenceResult(
        message="Expanded query with multiple files",
        original_message="Check @file:test.py and @file:config.json",
        references=[
            ContextReference(
                raw="@file:test.py",
                kind="file",
                target="test.py",
                start=6,
                end=20,
            ),
            ContextReference(
                raw="@file:config.json",
                kind="file",
                target="config.json",
                start=25,
                end=44,
            )
        ],
        warnings=[],
        injected_tokens=200,
        expanded=True,
        blocked=False,
    )
    
    with patch("app.core.context_references.preprocess_context_references_async", new_callable=AsyncMock) as mock_preprocess:
        with patch("app.core.model_metadata.get_model_context_length", new_callable=AsyncMock) as mock_context_length:
            with patch.object(strategy, "_setup_tools", return_value=[]):
                with patch.object(strategy, "_build_llm") as mock_build_llm:
                    with patch.object(strategy, "_build_agent") as mock_build_agent:
                        with patch.object(strategy, "_execute_agent") as mock_execute:
                            with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                                with patch.object(strategy, "_save_trajectory", new_callable=AsyncMock):
                                    mock_preprocess.return_value = mock_ref_result
                                    mock_context_length.return_value = 128000
                                    mock_build_llm.return_value = MagicMock()
                                    mock_build_agent.return_value = MagicMock()
                                    mock_execute.return_value = {
                                        "final_answer": "Test answer",
                                        "messages": [],
                                        "tool_calls": [],
                                    }
                                    
                                    context = {
                                        "execution_id": "exec-multi",
                                        "user_query": "Check @file:test.py and @file:config.json",
                                        "mcp_manager": mock_mcp_manager,
                                        "logger": MagicMock(),
                                    }
                                    
                                    result = await strategy.execute(sample_workflow, context)
                                    
                                    # Verify preprocessing was called
                                    mock_preprocess.assert_called_once()
                                    
                                    # Verify execution completed successfully
                                    assert result["final_answer"] == "Test answer"


@pytest.mark.asyncio
async def test_context_references_with_git_references(
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that git references (@diff, @staged, @git:N) are handled.
    
    **Validates: Requirements 16.8**
    """
    strategy = ReactStrategy()
    
    # Mock context reference preprocessing with git references
    mock_ref_result = ContextReferenceResult(
        message="Expanded query with git diff",
        original_message="Check @diff",
        references=[
            ContextReference(
                raw="@diff",
                kind="diff",
                target="",
                start=6,
                end=11,
            )
        ],
        warnings=[],
        injected_tokens=500,
        expanded=True,
        blocked=False,
    )
    
    with patch("app.core.context_references.preprocess_context_references_async", new_callable=AsyncMock) as mock_preprocess:
        with patch("app.core.model_metadata.get_model_context_length", new_callable=AsyncMock) as mock_context_length:
            with patch.object(strategy, "_setup_tools", return_value=[]):
                with patch.object(strategy, "_build_llm") as mock_build_llm:
                    with patch.object(strategy, "_build_agent") as mock_build_agent:
                        with patch.object(strategy, "_execute_agent") as mock_execute:
                            with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                                with patch.object(strategy, "_save_trajectory", new_callable=AsyncMock):
                                    mock_preprocess.return_value = mock_ref_result
                                    mock_context_length.return_value = 128000
                                    mock_build_llm.return_value = MagicMock()
                                    mock_build_agent.return_value = MagicMock()
                                    mock_execute.return_value = {
                                        "final_answer": "Test answer",
                                        "messages": [],
                                        "tool_calls": [],
                                    }
                                    
                                    context = {
                                        "execution_id": "exec-git",
                                        "user_query": "Check @diff",
                                        "mcp_manager": mock_mcp_manager,
                                        "logger": MagicMock(),
                                    }
                                    
                                    result = await strategy.execute(sample_workflow, context)
                                    
                                    # Verify preprocessing was called
                                    mock_preprocess.assert_called_once()
                                    
                                    # Verify execution completed successfully
                                    assert result["final_answer"] == "Test answer"
