"""Health check and status routes."""
from datetime import datetime, timezone
from fastapi import APIRouter, Query

from app.models.workflow import HealthResponse
from app.core.scheduler import workflow_scheduler
from app.services.execution_state import execution_state


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    active_records = await execution_state.list_active_records()

    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc),
        scheduler_running=workflow_scheduler.is_running(),
        active_workflows=len(active_records),
    )


@router.get("/status", response_model=dict)
async def get_status():
    """Get detailed system status."""
    active_records = await execution_state.list_active_records()
    scheduled_jobs = workflow_scheduler.get_scheduled_jobs()

    return {
        "scheduler_running": workflow_scheduler.is_running(),
        "active_executions": len(active_records),
        "scheduled_jobs": len(scheduled_jobs),
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None
            }
            for job in scheduled_jobs
        ],
        "executions": [
            {
                "execution_id": record.get("execution_id"),
                "workflow_name": record.get("workflow_name"),
                "status": record.get("status"),
                "start_time": record.get("started_at") or record.get("start_time"),
            }
            for record in active_records
        ],
    }


@router.post("/clear", response_model=dict)
async def clear_data(clear_jobs: bool = Query(False, description="Also clear scheduled jobs")):
    """Clear in-memory data (active executions, event queues, optionally scheduled jobs)."""
    result = workflow_scheduler.clear_data(clear_jobs=clear_jobs)
    
    return {
        "status": "cleared",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **result
    }
