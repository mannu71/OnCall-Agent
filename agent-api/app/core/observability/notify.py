"""Run-report sink for cadenced (scheduled) loops.

An "L1 report-only" loop — a cron-fired investigation that reports rather than
acts — needs somewhere to send its result. :func:`post_run_report` ships a
compact summary of a finished run to an operator-configured webhook.

Best-effort and fully opt-in: with no webhook configured it is a no-op, and any
delivery failure is swallowed so a report never sinks the run that produced it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

_MAX_ANSWER_CHARS = 2000


def build_report_payload(workflow_name: str, result: Any, *, scheduled: bool) -> Dict[str, Any]:
    """Shape a compact, JSON-serializable report from a run result.

    Defensive: ``result`` may be a harness result dict, a visual-executor
    envelope, or ``None`` — pull out whatever is present, never raise. The
    ``terminal_state`` (app.harness.terminal_state) is surfaced first-class so a
    report reader can tell an exhausted/blocked/unverified run from a real
    success without parsing the prose.
    """
    r = result if isinstance(result, dict) else {}
    answer = str(r.get("final_answer") or r.get("answer") or "").strip()
    if len(answer) > _MAX_ANSWER_CHARS:
        answer = answer[:_MAX_ANSWER_CHARS] + "…"
    return {
        "workflow": workflow_name,
        "scheduled": bool(scheduled),
        "terminal_state": r.get("terminal_state") or r.get("status"),
        "execution_id": r.get("execution_id"),
        "final_answer": answer,
        "ungrounded_ids": list(r.get("ungrounded_ids") or []),
    }


async def post_run_report(workflow_name: str, result: Any, *, scheduled: bool = True) -> bool:
    """POST a compact run report to ``settings.loop_report_webhook_url``.

    Returns True if a report was delivered, False otherwise (disabled, or a
    delivery error — both non-fatal). Never raises.
    """
    try:
        from app.config import settings
        url = (getattr(settings, "loop_report_webhook_url", "") or "").strip()
        if not url:
            return False
        payload = build_report_payload(workflow_name, result, scheduled=scheduled)
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        logger.info(
            "notify: run report for '%s' → %s (status=%s)",
            workflow_name, url, getattr(resp, "status_code", "?"),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — a report must never sink the run
        logger.warning("notify: run report for '%s' skipped (%s)", workflow_name, exc)
        return False


__all__ = ["build_report_payload", "post_run_report"]
