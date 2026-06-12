"""Background-job status API (repo indexing today).

Surfaces the durable ``background_jobs`` store so the UI can show live indexing
progress instead of only the coarse ``indexing_status`` flag on the workflow row.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Query

from app.services.background_jobs import background_job_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    active_only: bool = Query(True, description="Only queued/running jobs"),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """List background jobs (active by default; pass active_only=false for history)."""
    jobs = (
        await background_job_store.list_active()
        if active_only
        else await background_job_store.list_recent(limit=limit)
    )
    return {"count": len(jobs), "jobs": jobs}


@router.get("/indexing/status")
async def indexing_status() -> Dict[str, Any]:
    """Convenience: just the active repo-indexing jobs."""
    jobs = [j for j in await background_job_store.list_active() if j["job_type"] == "repo_index"]
    return {"indexing": bool(jobs), "count": len(jobs), "jobs": jobs}
