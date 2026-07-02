"""Loop 4 eval guardrail — gates hill-climbing auto-apply.

run_selftest_guard() invokes the DB-free selftest suite and returns
True (safe to apply) / False (regression detected). Heavy-weight
container evals are not run here; the guard validates structural
correctness so proposal application never breaks the harness.

When the selftest suite is unavailable (import error, test collection
failure) the guard returns False and logs a warning — fail-safe.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def run_selftest_guard() -> Dict[str, Any]:
    """Run the DB-free harness selftest and return {passed, detail}.

    Returns:
        ``{"passed": bool, "detail": str}``
    """
    try:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "evals.harness_selftest"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        passed = proc.returncode == 0
        detail = (proc.stdout or "") + (proc.stderr or "")
        if not passed:
            logger.warning(
                "guard: selftest FAILED (rc=%d) — blocking auto-apply\n%s",
                proc.returncode,
                detail[:2000],
            )
        return {"passed": passed, "detail": detail[:2000]}
    except FileNotFoundError:
        # pytest not on PATH — fall back to a pure-import smoke check
        return await _import_smoke_guard()
    except Exception as exc:  # noqa: BLE001 — guard must be defensive
        logger.warning("guard: selftest run error (%s) — blocking auto-apply", exc)
        return {"passed": False, "detail": str(exc)}


async def _import_smoke_guard() -> Dict[str, Any]:
    """Minimal import-level smoke check when pytest is unavailable."""
    try:
        from app.core.supervisor import InvestigationSupervisor, SupervisorConfig
        from app.core.improvement.analyzer import compute_signals

        sup = InvestigationSupervisor(SupervisorConfig())
        # Sanity: heuristic path still returns a verdict (sync fallback check)
        import asyncio
        verdict = await sup.evaluate(
            final_answer="root cause identified: the service restarted.",
            tool_calls=[],
            confidence=0.9,
        )
        assert verdict is not None, "evaluate() returned None"

        signals = compute_signals([])
        assert signals.get("count") == 0
        return {"passed": True, "detail": "import smoke check passed"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("guard: import smoke check failed (%s)", exc)
        return {"passed": False, "detail": str(exc)}
