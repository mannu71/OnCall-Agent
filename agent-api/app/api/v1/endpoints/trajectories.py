"""Trajectories API — read agent message traces persisted on ExecutionModel.

Backs the TrajectoryReplay.jsx dashboard view. Trajectories are keyed by
execution_id (not a separate trajectory_id) because they live on the
ExecutionModel.trajectory JSON column written by ReactStrategy.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.trajectory_service import trajectory_service

router = APIRouter(prefix="/trajectories", tags=["trajectories"])


class TrajectorySummary(BaseModel):
    execution_id: str
    workflow_name: Optional[str] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    message_count: int = 0


class TrajectoryDetail(BaseModel):
    execution_id: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    message_count: int = 0


class ReplayResponse(BaseModel):
    execution_id: str
    messages: List[Dict[str, Any]]
    message_count: int


@router.get("", response_model=List[TrajectorySummary])
async def list_trajectories(
    workflow_name: Optional[str] = Query(None, description="Filter by workflow name"),
    limit: int = Query(20, ge=1, le=1000),
) -> List[TrajectorySummary]:
    rows = await trajectory_service.list_trajectories(
        workflow_name=workflow_name, limit=limit
    )
    return [TrajectorySummary(**r) for r in rows]


@router.get("/{execution_id}", response_model=TrajectoryDetail)
async def get_trajectory(execution_id: str) -> TrajectoryDetail:
    trajectory = await trajectory_service.get_trajectory(execution_id)
    if trajectory is None:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory for execution '{execution_id}' not found",
        )
    return TrajectoryDetail(
        execution_id=execution_id,
        messages=trajectory,
        message_count=len(trajectory),
    )


@router.post("/{execution_id}/replay", response_model=ReplayResponse)
async def replay_trajectory(execution_id: str) -> ReplayResponse:
    trajectory = await trajectory_service.get_trajectory(execution_id)
    if not trajectory:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory for execution '{execution_id}' not found or empty",
        )
    return ReplayResponse(
        execution_id=execution_id,
        messages=trajectory,
        message_count=len(trajectory),
    )


@router.get("/{execution_id}/atropos")
async def get_atropos(execution_id: str) -> Dict[str, Any]:
    """Trajectory in Atropos format for RL fine-tuning pipelines."""
    payload = await trajectory_service.get_atropos_format(execution_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory for execution '{execution_id}' not found",
        )
    return payload
