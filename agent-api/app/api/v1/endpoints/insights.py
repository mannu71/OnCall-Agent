"""Agent self-improvement insights endpoint."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.services.insights_service import insights_service

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("", response_model=Dict[str, Any])
async def get_insights(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    log_group: Optional[str] = Query(None, description="Filter anomaly trends to a specific log group"),
) -> Dict[str, Any]:
    """Return a consolidated self-improvement insight report."""
    return await insights_service.get_report(days=days, log_group=log_group)


@router.get("/kb", response_model=Dict[str, Any])
async def get_kb_insights(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100, description="Max recent agent-written entries to return"),
) -> Dict[str, Any]:
    """Return a detailed breakdown of the knowledge base by provenance."""
    return await insights_service.get_kb_breakdown(days=days, limit=limit)


@router.get("/baselines", response_model=Dict[str, Any])
async def get_baseline_insights(
    days: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Return baseline calibration events and current baseline values."""
    return await insights_service.get_baseline_history(days=days)
