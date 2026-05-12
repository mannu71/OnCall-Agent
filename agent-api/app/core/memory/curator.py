"""Autonomous Curator — background job for pattern_memory health maintenance.

Runs weekly (wired into app/core/scheduler.py).  Marks stale and low-confidence
patterns so they stop polluting similarity searches and RAG recall.

Rules (from the build plan):
- Not seen in 30+ days AND curator_status == 'active'  → set 'stale'
- confidence_score < 0.3 AND occurrence_count < 3       → set 'archive'
- A curator_runs row is written after each run

The curator uses cheap queries — it never calls an LLM.
Reference: hermes-agent curator background job pattern.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────────────────────
_STALE_DAYS           = 30
_ARCHIVE_CONFIDENCE   = 0.3
_ARCHIVE_OCCURRENCES  = 3


async def run_curator() -> Dict[str, Any]:
    """Main curator coroutine — call from the scheduler weekly.

    Returns:
        Dict with counts of patterns staled/archived and the run timestamp.
    """
    logger.info("Curator: starting pattern_memory health pass")
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=_STALE_DAYS)
    staled = 0
    archived = 0

    async with AsyncSessionLocal() as session:
        # Mark stale: active patterns not seen recently
        stale_result = await session.execute(
            text("""
                UPDATE pattern_memory
                   SET curator_status = 'stale',
                       updated_at     = NOW()
                 WHERE curator_status = 'active'
                   AND last_seen < :threshold
            """),
            {"threshold": stale_threshold},
        )
        staled = stale_result.rowcount  # type: ignore[attr-defined]

        # Mark archive: low-confidence patterns seen rarely
        archive_result = await session.execute(
            text("""
                UPDATE pattern_memory
                   SET curator_status = 'archive',
                       updated_at     = NOW()
                 WHERE curator_status IN ('active', 'stale')
                   AND confidence_score < :min_confidence
                   AND occurrence_count < :min_occurrences
            """),
            {
                "min_confidence":  _ARCHIVE_CONFIDENCE,
                "min_occurrences": _ARCHIVE_OCCURRENCES,
            },
        )
        archived = archive_result.rowcount  # type: ignore[attr-defined]

        # Write curator run record (table created inline if missing)
        await session.execute(
            text("""
                CREATE TABLE IF NOT EXISTS curator_runs (
                    id         SERIAL PRIMARY KEY,
                    ran_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    staled     INTEGER NOT NULL DEFAULT 0,
                    archived   INTEGER NOT NULL DEFAULT 0,
                    details    JSONB
                )
            """)
        )
        await session.execute(
            text("""
                INSERT INTO curator_runs (ran_at, staled, archived, details)
                VALUES (:ran_at, :staled, :archived, :details::jsonb)
            """),
            {
                "ran_at":   now,
                "staled":   staled,
                "archived": archived,
                "details":  f'{{"stale_threshold_days": {_STALE_DAYS}, "archive_confidence": {_ARCHIVE_CONFIDENCE}}}',
            },
        )
        await session.commit()

    logger.info(
        "Curator: complete — staled=%d archived=%d",
        staled, archived,
    )
    return {
        "ran_at":   now.isoformat(),
        "staled":   staled,
        "archived": archived,
    }


async def get_last_run() -> Dict[str, Any] | None:
    """Return the most recent curator_runs row, or None if no run yet."""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                text("""
                    SELECT ran_at, staled, archived, details
                      FROM curator_runs
                     ORDER BY ran_at DESC
                     LIMIT 1
                """)
            )
            row = result.one_or_none()
        except Exception:
            return None

    if row is None:
        return None

    return {
        "ran_at":   row.ran_at.isoformat() if row.ran_at else None,
        "staled":   row.staled,
        "archived": row.archived,
        "details":  row.details,
    }
