"""Recurring tool/run failure fingerprints — failure->governance conversion.

Backs ``app.core.improvement.analyzer.GovernanceConverter`` (migration
``030_governance_and_trajectory.sql``). A fingerprint is the same
regex-normalized error string ``analyzer.compute_signals`` already computes
(``re.sub(r"\\d+", "#", err)[:80]``); recording is idempotent per call —
each call either inserts a new row (count=1) or bumps an existing one.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import FailureLedgerModel

logger = logging.getLogger(__name__)

# Cap on how many sample execution_ids we keep per fingerprint — enough to
# investigate a recurring pattern without the row growing unbounded.
_MAX_SAMPLES = 10


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FailureLedgerRepository(BaseAsyncRepository):
    """Repository for the failure->governance conversion ledger."""

    async def record(self, fingerprint: str, execution_id: Optional[str] = None) -> int:
        """Upsert a failure occurrence; returns the fingerprint's new count."""
        async def _work(session):
            row = (await session.execute(
                select(FailureLedgerModel).where(
                    FailureLedgerModel.fingerprint == fingerprint,
                )
            )).scalar_one_or_none()
            now = _utcnow()
            if row is None:
                samples = [execution_id] if execution_id else []
                row = FailureLedgerModel(
                    fingerprint=fingerprint,
                    first_seen=now, last_seen=now,
                    count=1, sample_execution_ids=samples, status="open",
                )
                session.add(row)
                return 1
            row.count = (row.count or 0) + 1
            row.last_seen = now
            if execution_id:
                samples = list(row.sample_execution_ids or [])
                if execution_id not in samples:
                    samples.append(execution_id)
                row.sample_execution_ids = samples[-_MAX_SAMPLES:]
            return row.count
        return await self._run(_work)

    async def list_open(self, min_count: int = 1) -> List[Dict[str, Any]]:
        """Return open fingerprints with count >= min_count, most-recurring first."""
        rows = await self._all(
            select(FailureLedgerModel)
            .where(FailureLedgerModel.status == "open")
            .order_by(FailureLedgerModel.count.desc())
        )
        return [
            {
                "fingerprint": r.fingerprint,
                "first_seen": r.first_seen,
                "last_seen": r.last_seen,
                "count": r.count,
                "sample_execution_ids": r.sample_execution_ids,
                "status": r.status,
                "control_ref": r.control_ref,
            }
            for r in rows if (r.count or 0) >= min_count
        ]

    async def mark_converted(self, fingerprint: str, control_ref: str) -> None:
        """Flag a fingerprint as converted into a durable control."""
        async def _work(session):
            row = (await session.execute(
                select(FailureLedgerModel).where(
                    FailureLedgerModel.fingerprint == fingerprint,
                )
            )).scalar_one_or_none()
            if row is not None:
                row.status = "converted"
                row.control_ref = control_ref
        await self._run(_work)


failure_ledger_repository = FailureLedgerRepository()
