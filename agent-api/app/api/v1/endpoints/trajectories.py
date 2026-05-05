"""Trajectories API endpoints for managing conversation trajectories."""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.trajectory_service import trajectory_service, TrajectoryEntry


router = APIRouter(prefix="/trajectories", tags=["trajectories"])


# Response models
class TrajectoryMetadata(BaseModel):
    """Trajectory metadata response."""
    trajectory_id: str = Field(..., description="Unique trajectory identifier")
    execution_id: str = Field(..., description="Associated execution ID")
    model: str = Field(..., description="Model used for this trajectory")
    timestamp: datetime = Field(..., description="Trajectory creation timestamp")
    completed: bool = Field(..., description="Whether execution completed successfully")
    message_count: int = Field(..., description="Number of messages in trajectory")
    tool_call_count: int = Field(..., description="Number of tool calls made")


class TrajectoryDetail(BaseModel):
    """Detailed trajectory response."""
    trajectory_id: str = Field(..., description="Unique trajectory identifier")
    execution_id: str = Field(..., description="Associated execution ID")
    model: str = Field(..., description="Model used for this trajectory")
    timestamp: datetime = Field(..., description="Trajectory creation timestamp")
    completed: bool = Field(..., description="Whether execution completed successfully")
    messages: List[dict] = Field(..., description="Conversation messages")
    tool_calls: List[dict] = Field(..., description="Tool calls made during execution")
    metadata: dict = Field(..., description="Additional metadata")


class ReplayResponse(BaseModel):
    """Response from trajectory replay."""
    trajectory_id: str = Field(..., description="Replayed trajectory ID")
    messages: List[dict] = Field(..., description="Messages from the trajectory")
    message_count: int = Field(..., description="Number of messages replayed")


def _trajectory_entry_to_metadata(entry: TrajectoryEntry) -> TrajectoryMetadata:
    """Convert TrajectoryEntry to TrajectoryMetadata response."""
    return TrajectoryMetadata(
        trajectory_id=entry.trajectory_id,
        execution_id=entry.execution_id,
        model=entry.model,
        timestamp=entry.timestamp,
        completed=entry.completed,
        message_count=len(entry.messages),
        tool_call_count=len(entry.tool_calls),
    )


def _trajectory_entry_to_detail(entry: TrajectoryEntry) -> TrajectoryDetail:
    """Convert TrajectoryEntry to TrajectoryDetail response."""
    return TrajectoryDetail(
        trajectory_id=entry.trajectory_id,
        execution_id=entry.execution_id,
        model=entry.model,
        timestamp=entry.timestamp,
        completed=entry.completed,
        messages=entry.messages,
        tool_calls=entry.tool_calls,
        metadata=entry.metadata,
    )


@router.get("", response_model=List[TrajectoryMetadata])
async def list_trajectories(
    execution_id: Optional[str] = Query(
        None,
        description="Filter trajectories by execution ID"
    ),
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="Maximum number of trajectories to return"
    )
) -> List[TrajectoryMetadata]:
    """List stored trajectories with optional filtering.
    
    Returns a list of trajectory metadata, optionally filtered by execution ID.
    Results are ordered by creation time (newest first).
    
    Args:
        execution_id: Optional filter by execution ID
        limit: Maximum number of results (1-1000, default 100)
    
    Returns:
        List of trajectory metadata objects
    """
    trajectories = await trajectory_service.list_trajectories(
        execution_id=execution_id,
        limit=limit
    )
    
    return [_trajectory_entry_to_metadata(t) for t in trajectories]


@router.get("/{trajectory_id}", response_model=TrajectoryDetail)
async def get_trajectory(trajectory_id: str) -> TrajectoryDetail:
    """Get detailed information about a specific trajectory.
    
    Returns the complete trajectory including all messages, tool calls,
    and metadata.
    
    Args:
        trajectory_id: Unique trajectory identifier
    
    Returns:
        Detailed trajectory information
    
    Raises:
        HTTPException: 404 if trajectory not found
    """
    trajectory = await trajectory_service.load_trajectory(trajectory_id)
    
    if not trajectory:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory '{trajectory_id}' not found"
        )
    
    return _trajectory_entry_to_detail(trajectory)


@router.post("/{trajectory_id}/replay", response_model=ReplayResponse)
async def replay_trajectory(trajectory_id: str) -> ReplayResponse:
    """Replay a trajectory and return its messages.
    
    This endpoint loads a trajectory and returns its messages for replay.
    The messages can be used to reconstruct the conversation state.
    
    Args:
        trajectory_id: Unique trajectory identifier
    
    Returns:
        Replay response with messages
    
    Raises:
        HTTPException: 404 if trajectory not found
    """
    messages = await trajectory_service.replay_trajectory(trajectory_id)
    
    if not messages:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory '{trajectory_id}' not found or has no messages"
        )
    
    return ReplayResponse(
        trajectory_id=trajectory_id,
        messages=messages,
        message_count=len(messages),
    )
