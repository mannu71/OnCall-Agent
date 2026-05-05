"""Tests for TrajectoryService.

**Validates: Requirements 8.1-8.6**
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any

from app.services.trajectory_service import TrajectoryService, TrajectoryEntry


@pytest_asyncio.fixture
async def trajectory_service(test_session):
    """Create trajectory service instance with test session."""
    return TrajectoryService(session=test_session)


@pytest.fixture
def sample_messages() -> List[Dict[str, Any]]:
    """Sample conversation messages."""
    return [
        {
            "role": "system",
            "content": "You are a helpful assistant."
        },
        {
            "role": "user",
            "content": "What is 2+2?"
        },
        {
            "role": "assistant",
            "content": "The answer is 4."
        }
    ]


@pytest.fixture
def sample_tool_calls() -> List[Dict[str, Any]]:
    """Sample tool calls."""
    return [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "calculator",
                "arguments": '{"operation": "add", "a": 2, "b": 2}'
            }
        }
    ]


@pytest.mark.asyncio
class TestTrajectoryService:
    """Test suite for TrajectoryService."""
    
    async def test_save_trajectory_basic(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test basic trajectory saving.
        
        **Validates: Requirement 8.1** - Save conversation trajectory
        """
        # Save trajectory
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_123",
            messages=sample_messages,
            model="gpt-4",
            completed=True
        )
        
        # Verify trajectory ID is generated
        assert trajectory_id is not None
        assert trajectory_id.startswith("traj_")
        assert len(trajectory_id) > 5
    
    async def test_save_trajectory_with_metadata(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]],
        sample_tool_calls: List[Dict[str, Any]]
    ):
        """Test trajectory saving with metadata and tool calls.
        
        **Validates: Requirement 8.2** - Store metadata including timestamp, model, completion status
        """
        metadata = {
            "user_id": "user_456",
            "session_id": "session_789"
        }
        
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_456",
            messages=sample_messages,
            model="claude-3-opus",
            completed=True,
            metadata=metadata,
            tool_calls=sample_tool_calls
        )
        
        # Load and verify
        loaded = await trajectory_service.load_trajectory(trajectory_id)
        assert loaded is not None
        assert loaded.model == "claude-3-opus"
        assert loaded.completed is True
        assert loaded.metadata["user_id"] == "user_456"
        assert loaded.metadata["session_id"] == "session_789"
        assert "saved_at" in loaded.metadata
        assert len(loaded.tool_calls) == 1
        assert loaded.tool_calls[0]["function"]["name"] == "calculator"
    
    async def test_load_trajectory(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test loading a stored trajectory.
        
        **Validates: Requirement 8.5** - Support loading trajectories
        """
        # Save first
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_load_test",
            messages=sample_messages,
            model="gpt-4-turbo",
            completed=True
        )
        
        # Load
        loaded = await trajectory_service.load_trajectory(trajectory_id)
        
        # Verify
        assert loaded is not None
        assert loaded.trajectory_id == trajectory_id
        assert loaded.execution_id == "exec_load_test"
        assert loaded.model == "gpt-4-turbo"
        assert len(loaded.messages) == 3
        assert loaded.messages[0]["role"] == "system"
        assert loaded.messages[1]["content"] == "What is 2+2?"
    
    async def test_load_nonexistent_trajectory(
        self,
        trajectory_service: TrajectoryService
    ):
        """Test loading a trajectory that doesn't exist.
        
        **Validates: Requirement 8.5** - Handle missing trajectories gracefully
        """
        loaded = await trajectory_service.load_trajectory("traj_nonexistent")
        assert loaded is None
    
    async def test_list_trajectories_all(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test listing all trajectories.
        
        **Validates: Requirement 8.5** - Support listing trajectories
        """
        # Save multiple trajectories
        traj_ids = []
        for i in range(3):
            traj_id = await trajectory_service.save_trajectory(
                execution_id=f"exec_list_{i}",
                messages=sample_messages,
                model=f"model_{i}",
                completed=True
            )
            traj_ids.append(traj_id)
        
        # List all
        trajectories = await trajectory_service.list_trajectories()
        
        # Verify we have at least our 3 trajectories
        assert len(trajectories) >= 3
        
        # Verify our trajectories are in the list
        loaded_ids = [t.trajectory_id for t in trajectories]
        for traj_id in traj_ids:
            assert traj_id in loaded_ids
    
    async def test_list_trajectories_by_execution_id(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test listing trajectories filtered by execution ID.
        
        **Validates: Requirement 8.4** - Link trajectories to execution records
        """
        execution_id = "exec_filter_test"
        
        # Save trajectories with same execution ID
        traj_ids = []
        for i in range(2):
            traj_id = await trajectory_service.save_trajectory(
                execution_id=execution_id,
                messages=sample_messages,
                model="gpt-4",
                completed=True
            )
            traj_ids.append(traj_id)
        
        # Save one with different execution ID
        await trajectory_service.save_trajectory(
            execution_id="exec_other",
            messages=sample_messages,
            model="gpt-4",
            completed=True
        )
        
        # List filtered
        trajectories = await trajectory_service.list_trajectories(
            execution_id=execution_id
        )
        
        # Verify only our execution ID is returned
        assert len(trajectories) >= 2
        for traj in trajectories:
            assert traj.execution_id == execution_id
    
    async def test_list_trajectories_limit(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test listing trajectories with limit.
        
        **Validates: Requirement 8.5** - Support pagination with limit
        """
        # Save multiple trajectories
        for i in range(5):
            await trajectory_service.save_trajectory(
                execution_id=f"exec_limit_{i}",
                messages=sample_messages,
                model="gpt-4",
                completed=True
            )
        
        # List with limit
        trajectories = await trajectory_service.list_trajectories(limit=3)
        
        # Verify limit is respected
        assert len(trajectories) <= 3
    
    async def test_replay_trajectory(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test replaying a trajectory.
        
        **Validates: Requirement 8.5** - Support replaying trajectories
        """
        # Save trajectory
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_replay",
            messages=sample_messages,
            model="gpt-4",
            completed=True
        )
        
        # Replay
        replayed_messages = await trajectory_service.replay_trajectory(trajectory_id)
        
        # Verify
        assert len(replayed_messages) == 3
        assert replayed_messages[0]["role"] == "system"
        assert replayed_messages[1]["content"] == "What is 2+2?"
        assert replayed_messages[2]["content"] == "The answer is 4."
    
    async def test_replay_nonexistent_trajectory(
        self,
        trajectory_service: TrajectoryService
    ):
        """Test replaying a trajectory that doesn't exist.
        
        **Validates: Requirement 8.5** - Handle missing trajectories gracefully
        """
        replayed = await trajectory_service.replay_trajectory("traj_nonexistent")
        assert replayed == []
    
    async def test_scratchpad_tag_conversion_string_content(
        self,
        trajectory_service: TrajectoryService
    ):
        """Test conversion of reasoning scratchpad tags in string content.
        
        **Validates: Requirement 8.3** - Convert reasoning scratchpad tags (thinking → think)
        """
        messages = [
            {
                "role": "assistant",
                "content": "<thinking>Let me think about this</thinking> The answer is 42."
            }
        ]
        
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_scratchpad",
            messages=messages,
            model="claude-3",
            completed=True
        )
        
        # Load and verify conversion
        loaded = await trajectory_service.load_trajectory(trajectory_id)
        assert loaded is not None
        assert "<think>" in loaded.messages[0]["content"]
        assert "</think>" in loaded.messages[0]["content"]
        assert "<thinking>" not in loaded.messages[0]["content"]
        assert "</thinking>" not in loaded.messages[0]["content"]
    
    async def test_scratchpad_tag_conversion_list_content(
        self,
        trajectory_service: TrajectoryService
    ):
        """Test conversion of reasoning scratchpad tags in list content.
        
        **Validates: Requirement 8.3** - Convert reasoning scratchpad tags in multi-part content
        """
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "<thinking>Analyzing the problem</thinking> Here's my response."
                    },
                    {
                        "type": "text",
                        "text": "<thinking>More thoughts</thinking> Additional info."
                    }
                ]
            }
        ]
        
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_scratchpad_list",
            messages=messages,
            model="claude-3",
            completed=True
        )
        
        # Load and verify conversion
        loaded = await trajectory_service.load_trajectory(trajectory_id)
        assert loaded is not None
        
        content = loaded.messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        
        for part in content:
            assert "<think>" in part["text"]
            assert "</think>" in part["text"]
            assert "<thinking>" not in part["text"]
            assert "</thinking>" not in part["text"]
    
    async def test_save_failed_execution_trajectory(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test saving trajectory for failed execution.
        
        **Validates: Requirement 8.6** - Handle both successful and failed execution trajectories
        """
        metadata = {
            "error": "API timeout",
            "error_type": "TimeoutError"
        }
        
        trajectory_id = await trajectory_service.save_trajectory(
            execution_id="exec_failed",
            messages=sample_messages,
            model="gpt-4",
            completed=False,
            metadata=metadata
        )
        
        # Load and verify
        loaded = await trajectory_service.load_trajectory(trajectory_id)
        assert loaded is not None
        assert loaded.completed is False
        assert loaded.metadata["error"] == "API timeout"
        assert loaded.metadata["error_type"] == "TimeoutError"
    
    async def test_trajectory_ordering(
        self,
        trajectory_service: TrajectoryService,
        sample_messages: List[Dict[str, Any]]
    ):
        """Test that trajectories are ordered by creation time (newest first).
        
        **Validates: Requirement 8.5** - List trajectories in chronological order
        """
        # Save trajectories in sequence
        traj_ids = []
        for i in range(3):
            traj_id = await trajectory_service.save_trajectory(
                execution_id=f"exec_order_{i}",
                messages=sample_messages,
                model="gpt-4",
                completed=True
            )
            traj_ids.append(traj_id)
        
        # List trajectories
        trajectories = await trajectory_service.list_trajectories(limit=10)
        
        # Find our trajectories in the list
        our_trajs = [t for t in trajectories if t.trajectory_id in traj_ids]
        
        # Verify they are in reverse chronological order (newest first)
        assert len(our_trajs) == 3
        assert our_trajs[0].trajectory_id == traj_ids[2]  # Most recent
        assert our_trajs[1].trajectory_id == traj_ids[1]
        assert our_trajs[2].trajectory_id == traj_ids[0]  # Oldest
