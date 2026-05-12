"""Curator API endpoints.

GET  /api/v1/curator/last-run  — return the most recent curator run summary
POST /api/v1/curator/run       — trigger a curator run immediately (admin use)
"""
from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/curator", tags=["curator"])


@router.get("/last-run")
async def get_last_curator_run():
    """Return the most recent pattern_memory curator run summary."""
    from app.core.memory.curator import get_last_run

    result = await get_last_run()
    if result is None:
        return {"message": "No curator runs found — run POST /curator/run to trigger one."}
    return result


@router.post("/run", status_code=status.HTTP_200_OK)
async def trigger_curator_run():
    """Trigger an immediate curator pass on pattern_memory.

    Normally runs weekly via the scheduler.  Use this endpoint to force a run
    (e.g., after bulk-importing patterns or during testing).
    """
    from app.core.memory.curator import run_curator

    try:
        result = await run_curator()
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curator run failed: {exc}",
        )
