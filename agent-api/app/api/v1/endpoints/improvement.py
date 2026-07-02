"""Self-improvement (hill-climbing) API — Loop 4.

GET /analyze  — sample recent traces and return draft proposals (read-only).
POST /apply   — analyze then apply safe proposals (skill drafts, reliability
                notes) behind the eval guardrail. Default dry_run=true so
                a first call always previews without writing.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.core.improvement import analyze_recent, apply_proposals

router = APIRouter(prefix="/improvement", tags=["improvement"])
logger = logging.getLogger(__name__)


@router.get("/analyze", response_model=Dict[str, Any])
async def analyze(
    profile: Optional[str] = Query(default=None, description="Workflow/profile name to scope to"),
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    use_llm: bool = Query(default=True, description="Use the LLM to draft proposals"),
) -> Dict[str, Any]:
    """Analyze recent runs and return draft improvement proposals."""
    report = await analyze_recent(profile, limit=limit, use_llm=use_llm)
    return {"success": True, "report": report.to_dict()}


@router.post("/apply", response_model=Dict[str, Any])
async def apply(
    profile: Optional[str] = Query(default=None, description="Scope analysis to this workflow/profile"),
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    dry_run: bool = Query(
        default=True,
        description=(
            "When true (default) returns what would be applied without writing. "
            "Set to false to execute — requires the eval guardrail to pass."
        ),
    ),
) -> Dict[str, Any]:
    """Analyze recent traces and apply safe proposals.

    Only 'skill' and 'reliability' proposals are eligible for auto-apply.
    'prompt' and 'policy' proposals remain as human-reviewed drafts.
    When dry_run=false the eval guardrail (DB-free selftest) must pass
    before any change is committed.
    """
    report = await analyze_recent(profile, limit=limit, use_llm=True)
    result = await apply_proposals(report, dry_run=dry_run)
    return {"success": True, "report": report.to_dict(), "apply_result": result}
