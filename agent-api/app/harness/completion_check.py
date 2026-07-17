"""Verified-completion check — did the agent actually finish its own plan?

Pure derivation over the planning-tool todo list
(``app.harness.planning_tools``) captured at run end. Feeds
``app.harness.terminal_state`` so a run that left plan items unfinished — or
marked them completed without any evidence — cannot silently self-report
``success``. The loop-engineering discipline this maps to: a plan with open
items is not a finished job.

Pure (no I/O): the caller supplies the already-fetched todo list.
"""
from __future__ import annotations

from typing import Any, Dict, List

_DONE = "completed"
# "blocked" is an honest, deliberate non-completion — it does not count as an
# unfinished/dangling item the way a still-pending/in_progress step does.
_TERMINAL_STATUSES = {"completed", "blocked"}


def check_completion(todos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize plan completion for a finished run.

    Returns:
      - ``plan_incomplete``: the agent wrote a plan but left at least one item
        neither completed nor blocked (a pending/in_progress step dangling at
        run end). False when there was no plan — runs that never planned are
        not penalized.
      - ``open_count``: how many items are still open.
      - ``unevidenced_completions``: indices of items marked completed with no
        cited evidence (telemetry; only enforced when verified completion is on).
    """
    items = todos or []
    open_items = [
        t for t in items
        if str(t.get("status", "")).strip().lower() not in _TERMINAL_STATUSES
    ]
    unevidenced = [
        t.get("index", i)
        for i, t in enumerate(items)
        if str(t.get("status", "")).strip().lower() == _DONE
        and not str(t.get("evidence", "")).strip()
    ]
    return {
        "plan_incomplete": bool(items) and bool(open_items),
        "open_count": len(open_items),
        "unevidenced_completions": unevidenced,
    }


__all__ = ["check_completion"]
