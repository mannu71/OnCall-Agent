"""Live failure → governance hook.

Records a fingerprint of each failed tool call into ``failure_ledger`` as it
happens, so the improvement loop can spot a failure mode that keeps recurring
and propose a fix for it (see :mod:`app.core.improvement.analyzer`, which
fingerprints the SAME way so live and post-hoc records collate).
"""
from __future__ import annotations

from typing import Optional


async def record_failure_fingerprint(
    tool_name: str, output_str: str, execution_id: Optional[str],
) -> None:
    """Upsert this failure's fingerprint into failure_ledger in real time.

    Gated by ``settings.governance_conversion_enabled`` (default False) — the
    flag check is the first thing done so the common (flag-off) path costs one
    attribute read and no DB round-trip. Best-effort — never breaks a run.
    """
    try:
        from app.config import settings
        if not getattr(settings, "governance_conversion_enabled", False):
            return
        from app.core.improvement.analyzer import fingerprint_error
        from app.infrastructure.persistence import failure_ledger_repository
        fp = fingerprint_error(f"{tool_name}: {output_str}")
        await failure_ledger_repository.record(fp, execution_id)
    except Exception:  # noqa: BLE001 — governance must never break a run
        pass


__all__ = ["record_failure_fingerprint"]
