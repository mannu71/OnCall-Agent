"""Self-improvement (hill-climbing) API.

On-demand analysis of recent execution traces that returns improvement proposals
as DRAFTS for operator review. Read-only: it never modifies prompts, profiles, or
skills — applying a proposal is a deliberate, separate human action.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.core.improvement import analyze_recent

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
