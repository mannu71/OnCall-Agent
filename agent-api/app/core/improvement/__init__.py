"""Self-improvement (hill-climbing) loop — Loop-4 from "Art of Loop Engineering".

Closes the compounding loop on top of the data the platform already keeps
(execution traces, skills, semantic memory): an opt-in analyzer samples recent
runs, derives quality signals, and proposes refinements (role/prompt tweaks,
candidate skills, pinned facts) as DRAFTS for operator approval. It never
auto-applies — human judgment stays in the loop (the docs' "human judgment +
token capital compound together").

Off by default (``settings.self_improvement_enabled``).
"""
from app.core.improvement.analyzer import (
    ImprovementReport,
    analyze_recent,
    compute_signals,
)
from app.core.improvement.apply import apply_proposals
from app.core.improvement.guard import run_selftest_guard

__all__ = [
    "ImprovementReport",
    "analyze_recent",
    "apply_proposals",
    "compute_signals",
    "run_selftest_guard",
]
