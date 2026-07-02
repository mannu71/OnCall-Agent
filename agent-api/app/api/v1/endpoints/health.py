"""Health check and status routes."""
from datetime import datetime, timezone
from fastapi import APIRouter, Query

from app.models.workflow import HealthResponse
from app.core.scheduler import workflow_scheduler
from app.services.execution_state import execution_state
from app.infrastructure.persistence.workflow_repository import WorkflowRepository


router = APIRouter(tags=["health"])

# Internal APScheduler jobs — not user-facing workflow schedules.
_SYSTEM_JOB_IDS = frozenset({"self_improvement_curator", "hillclimb_loop"})


async def _list_armed_workflow_schedules() -> list[str]:
    """Enabled, non-agent workflows with an active schedule node — matches the Scheduler UI."""
    workflows = await WorkflowRepository().list_all()
    names: list[str] = []
    for wf in workflows:
        if not isinstance(wf, dict):
            continue
        if wf.get("type") == "agent":
            continue
        if not wf.get("enabled", True):
            continue
        nodes = wf.get("nodes") or []
        sched = next((n for n in nodes if n.get("type") in ("scheduler", "schedule")), None)
        if sched:
            params = sched.get("params") or {}
            data = sched.get("data") or {}
            if params.get("enabled", data.get("enabled", True)) is False:
                continue
        schedule = wf.get("schedule")
        if schedule and str(schedule).strip():
            names.append(str(wf.get("name") or ""))
    return [n for n in names if n]


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
    workflow_jobs = [job for job in scheduled_jobs if job.id not in _SYSTEM_JOB_IDS]
    armed_workflow_schedules = await _list_armed_workflow_schedules()

    return {
        "scheduler_running": workflow_scheduler.is_running(),
        "active_executions": len(active_records),
        "scheduled_jobs": len(scheduled_jobs),
        "armed_workflow_schedules": len(armed_workflow_schedules),
        "armed_workflow_names": armed_workflow_schedules,
        "workflow_scheduler_jobs": len(workflow_jobs),
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
