"""
Unit tests for ReactStrategy trajectory integration.

Tests verify that execution trajectories are saved correctly after agent
execution completes, including messages, tool calls, and metadata.

Requirements: 16.6
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.workflow.strategies.react import ReactStrategy


@pytest.fixture
def mock_trajectory_service():
    """Mock trajectory service for testing."""
    with patch("app.services.trajectory_service.trajectory_service") as mock:
        mock.save_trajectory = AsyncMock(return_value="traj_abc123")
        yield mock


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
def mock_agent():
    """Mock LangGraph agent."""
    agent = MagicMock()
    agent.ainvoke = AsyncMock(return_value={
        "messages": [
            {"role": "user", "content": "test query"},
            {"role": "assistant", "content": "test response"},
        ]
    })
    return agent


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
async def test_trajectory_saved_on_successful_execution(
    mock_trajectory_service,
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that trajectory is saved after successful execution.
    
    **Validates: Requirements 16.6**
    """
    strategy = ReactStrategy()
    
    # Mock the agent execution
    with patch.object(strategy, "_setup_tools", return_value=[]):
        with patch.object(strategy, "_build_llm") as mock_build_llm:
            with patch.object(strategy, "_build_agent") as mock_build_agent:
                with patch.object(strategy, "_execute_agent") as mock_execute:
                    with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                        # Setup mocks
                        mock_build_llm.return_value = MagicMock()
                        mock_build_agent.return_value = MagicMock()
                        mock_execute.return_value = {
                            "final_answer": "Test answer",
                            "messages": [
                                {"role": "user", "content": "test query"},
                                {"role": "assistant", "content": "test response"},
                            ],
                            "tool_calls": [
                                {"tool": "test_tool", "args_keys": ["arg1"]},
                            ],
                        }
                        
                        # Execute workflow
                        context = {
                            "execution_id": "exec-123",
                            "user_query": "test query",
                            "mcp_manager": mock_mcp_manager,
                            "logger": MagicMock(),
                        }
                        
                        result = await strategy.execute(sample_workflow, context)
                        
                        # Verify trajectory was saved
                        mock_trajectory_service.save_trajectory.assert_called_once()
                        call_args = mock_trajectory_service.save_trajectory.call_args
                        
                        assert call_args.kwargs["execution_id"] == "exec-123"
                        assert call_args.kwargs["model"] == "gpt-4"
                        assert call_args.kwargs["completed"] is True
                        assert len(call_args.kwargs["messages"]) == 2
                        assert len(call_args.kwargs["tool_calls"]) == 1
                        assert call_args.kwargs["metadata"]["provider"] == "openai"


@pytest.mark.asyncio
async def test_trajectory_includes_all_messages(
    mock_trajectory_service,
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that trajectory includes all conversation messages.
    
    **Validates: Requirements 16.6**
    """
    strategy = ReactStrategy()
    
    messages = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "first response"},
        {"role": "user", "content": "second message"},
        {"role": "assistant", "content": "second response"},
    ]
    
    with patch.object(strategy, "_setup_tools", return_value=[]):
        with patch.object(strategy, "_build_llm") as mock_build_llm:
            with patch.object(strategy, "_build_agent") as mock_build_agent:
                with patch.object(strategy, "_execute_agent") as mock_execute:
                    with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                        mock_build_llm.return_value = MagicMock()
                        mock_build_agent.return_value = MagicMock()
                        mock_execute.return_value = {
                            "final_answer": "Final answer",
                            "messages": messages,
                            "tool_calls": [],
                        }
                        
                        context = {
                            "execution_id": "exec-456",
                            "user_query": "test query",
                            "mcp_manager": mock_mcp_manager,
                            "logger": MagicMock(),
                        }
                        
                        await strategy.execute(sample_workflow, context)
                        
                        # Verify all messages were saved
                        call_args = mock_trajectory_service.save_trajectory.call_args
                        saved_messages = call_args.kwargs["messages"]
                        
                        assert len(saved_messages) == 4
                        assert saved_messages[0]["content"] == "first message"
                        assert saved_messages[3]["content"] == "second response"


@pytest.mark.asyncio
async def test_trajectory_includes_tool_calls(
    mock_trajectory_service,
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that trajectory includes tool call information.
    
    **Validates: Requirements 16.6**
    """
    strategy = ReactStrategy()
    
    tool_calls = [
        {"tool": "read_file", "args_keys": ["path"]},
        {"tool": "execute_command", "args_keys": ["command"]},
    ]
    
    with patch.object(strategy, "_setup_tools", return_value=[]):
        with patch.object(strategy, "_build_llm") as mock_build_llm:
            with patch.object(strategy, "_build_agent") as mock_build_agent:
                with patch.object(strategy, "_execute_agent") as mock_execute:
                    with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                        mock_build_llm.return_value = MagicMock()
                        mock_build_agent.return_value = MagicMock()
                        mock_execute.return_value = {
                            "final_answer": "Done",
                            "messages": [{"role": "assistant", "content": "done"}],
                            "tool_calls": tool_calls,
                        }
                        
                        context = {
                            "execution_id": "exec-789",
                            "user_query": "test query",
                            "mcp_manager": mock_mcp_manager,
                            "logger": MagicMock(),
                        }
                        
                        await strategy.execute(sample_workflow, context)
                        
                        # Verify tool calls were saved
                        call_args = mock_trajectory_service.save_trajectory.call_args
                        saved_tool_calls = call_args.kwargs["tool_calls"]
                        
                        assert len(saved_tool_calls) == 2
                        assert saved_tool_calls[0]["tool"] == "read_file"
                        assert saved_tool_calls[1]["tool"] == "execute_command"


@pytest.mark.asyncio
async def test_trajectory_includes_metadata(
    mock_trajectory_service,
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that trajectory includes execution metadata.
    
    **Validates: Requirements 16.6**
    """
    strategy = ReactStrategy()
    
    with patch.object(strategy, "_setup_tools", return_value=[]):
        with patch.object(strategy, "_build_llm") as mock_build_llm:
            with patch.object(strategy, "_build_agent") as mock_build_agent:
                with patch.object(strategy, "_execute_agent") as mock_execute:
                    with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                        mock_build_llm.return_value = MagicMock()
                        mock_build_agent.return_value = MagicMock()
                        mock_execute.return_value = {
                            "final_answer": "Answer",
                            "messages": [],
                            "tool_calls": [],
                        }
                        
                        context = {
                            "execution_id": "exec-metadata",
                            "user_query": "test query",
                            "mcp_manager": mock_mcp_manager,
                            "logger": MagicMock(),
                        }
                        
                        await strategy.execute(sample_workflow, context)
                        
                        # Verify metadata was saved
                        call_args = mock_trajectory_service.save_trajectory.call_args
                        metadata = call_args.kwargs["metadata"]
                        
                        assert "provider" in metadata
                        assert "execution_id" in metadata
                        assert metadata["provider"] == "openai"
                        assert metadata["execution_id"] == "exec-metadata"


@pytest.mark.asyncio
async def test_trajectory_failure_does_not_break_execution(
    mock_trajectory_service,
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that trajectory save failure doesn't break execution.
    
    **Validates: Requirements 16.6**
    """
    strategy = ReactStrategy()
    
    # Make trajectory service fail
    mock_trajectory_service.save_trajectory.side_effect = Exception("DB error")
    
    with patch.object(strategy, "_setup_tools", return_value=[]):
        with patch.object(strategy, "_build_llm") as mock_build_llm:
            with patch.object(strategy, "_build_agent") as mock_build_agent:
                with patch.object(strategy, "_execute_agent") as mock_execute:
                    with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                        mock_build_llm.return_value = MagicMock()
                        mock_build_agent.return_value = MagicMock()
                        mock_execute.return_value = {
                            "final_answer": "Answer",
                            "messages": [],
                            "tool_calls": [],
                        }
                        
                        context = {
                            "execution_id": "exec-fail",
                            "user_query": "test query",
                            "mcp_manager": mock_mcp_manager,
                            "logger": MagicMock(),
                        }
                        
                        # Should not raise exception
                        result = await strategy.execute(sample_workflow, context)
                        
                        # Verify execution completed successfully
                        assert result["final_answer"] == "Answer"
                        assert result["type"] == "react"


@pytest.mark.asyncio
async def test_trajectory_links_to_execution_record(
    mock_trajectory_service,
    mock_db_repository,
    mock_mcp_manager,
    sample_workflow,
):
    """Test that trajectory is linked to execution record via execution_id.
    
    **Validates: Requirements 16.6**
    """
    strategy = ReactStrategy()
    
    with patch.object(strategy, "_setup_tools", return_value=[]):
        with patch.object(strategy, "_build_llm") as mock_build_llm:
            with patch.object(strategy, "_build_agent") as mock_build_agent:
                with patch.object(strategy, "_execute_agent") as mock_execute:
                    with patch.object(strategy, "_auto_learn", new_callable=AsyncMock):
                        mock_build_llm.return_value = MagicMock()
                        mock_build_agent.return_value = MagicMock()
                        mock_execute.return_value = {
                            "final_answer": "Answer",
                            "messages": [],
                            "tool_calls": [],
                        }
                        
                        execution_id = "exec-link-test"
                        context = {
                            "execution_id": execution_id,
                            "user_query": "test query",
                            "mcp_manager": mock_mcp_manager,
                            "logger": MagicMock(),
                        }
                        
                        await strategy.execute(sample_workflow, context)
                        
                        # Verify execution_id was passed to trajectory service
                        call_args = mock_trajectory_service.save_trajectory.call_args
                        assert call_args.kwargs["execution_id"] == execution_id
