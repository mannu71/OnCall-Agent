"""Self-improvement (hill-climbing) loop — Loop-4 from "Art of Loop Engineering".

Closes the compounding loop on top of the data the platform already keeps
(execution traces, skills, semantic memory): an opt-in analyzer samples recent
runs, derives quality signals, and proposes refinements (role/prompt tweaks,
candidate skills, reliability notes).

By default (``dry_run=True``) nothing is written — proposals are returned for
operator review only. When both ``settings.self_improvement_enabled`` AND
``settings.hillclimb_apply_enabled`` are true, the scheduler's periodic hill-climb
job calls ``apply_proposals(report, dry_run=False)``, which — after an eval-guard
selftest check — auto-applies the two "safe, reversible" proposal kinds:
  * ``skill``       → drafts a confidence-gated skill (status='draft', confidence=0.0;
                       the curator promotes it later, not this loop)
  * ``reliability`` → recorded as a structured audit log entry only, no state change
``prompt`` and ``policy`` proposals are NEVER auto-applied regardless of this flag —
the system-prompt CACHE CONTRACT makes auto-editing prompts off-limits, and policy
changes always require explicit operator sign-off. See ``app/core/improvement/apply.py``
for the exact eligibility/guard logic.

Off by default (``settings.self_improvement_enabled``); auto-apply is a second,
separate opt-in (``settings.hillclimb_apply_enabled``).
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
