"""LangGraph InvestigationState — typed shared state for all agent nodes.

Using a TypedDict with Annotated reducers means:
- Type-safe access across all nodes.
- Parallel agent findings accumulate (not overwrite) via merge_findings reducer.
- LangGraph checkpointing serialises/deserialises correctly.

All nodes receive the full state and return only the keys they update.
"""
from __future__ import annotations

from typing import Annotated, List, Optional
from typing_extensions import TypedDict


# ─────────────────────────────────────────────────────────────────────────────
# Reducer — parallel agents each write to their own findings list
# ─────────────────────────────────────────────────────────────────────────────

def merge_findings(a: List[dict], b: List[dict]) -> List[dict]:
    """Accumulate findings from parallel agent branches (Send() dispatch)."""
    return a + b


# ─────────────────────────────────────────────────────────────────────────────
# State schema
# ─────────────────────────────────────────────────────────────────────────────

class InvestigationState(TypedDict):
    # ── Inputs (set by the trigger / supervisor) ────────────────────────────
    trigger: str               # The raw alert / user query that started the investigation
    log_group: str             # Primary CloudWatch log group being investigated
    time_window: str           # e.g. "1h", "30m", "7d"
    service_name: str          # The service being investigated
    investigation_type: str    # "full" | "db_only" | "log_only" | "code_only"

    # ── Context injected by supervisor before agent dispatch ─────────────────
    past_cases: List[dict]     # Similar past investigations from pgvector similarity search

    # ── Agent findings — Annotated reducers allow parallel Send() writes ─────
    log_findings:  Annotated[List[dict], merge_findings]
    db_findings:   Annotated[List[dict], merge_findings]
    code_findings: Annotated[List[dict], merge_findings]

    # ── Synthesis output (populated by synthesis_node) ───────────────────────
    root_cause: Optional[str]
    confidence_score: Optional[float]
    suggestions: List[dict]

    # ── HITL (populated by interrupt() / resume) ─────────────────────────────
    engineer_approved: Optional[bool]
    engineer_notes: Optional[str]

    # ── Cost tracking (accumulated per node) ─────────────────────────────────
    total_cost_usd: Optional[float]

    # ── Metadata ─────────────────────────────────────────────────────────────
    investigation_id: Optional[str]
    execution_id: Optional[str]
    user_query: Optional[str]  # Free-form query (alternative entry point to trigger)
