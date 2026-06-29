"""Repository for human-in-the-loop tool-approval audit records.

All writes are best-effort: a failure here (e.g. the ``tool_approvals`` table
not yet migrated) must never break the live HITL approval flow, so every method
swallows and logs exceptions rather than propagating them.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import ToolApprovalModel

logger = logging.getLogger(__name__)


class ToolApprovalRepository(BaseAsyncRepository):
    """Durable audit trail for ``ask`` tool permission gates."""

    async def record_pending(
        self,
        execution_id: str,
        request_id: str,
        tool_name: str,
        args_summary: Optional[str] = None,
    ) -> None:
        """Insert a ``pending`` row before the agent blocks on approval."""
        try:
            async def _work(session):
                session.add(ToolApprovalModel(
                    execution_id=str(execution_id),
                    request_id=str(request_id),
                    tool_name=tool_name,
                    args_summary=(args_summary or "")[:4000],
                    decision="pending",
                    created_at=datetime.now(timezone.utc),
                ))
            await self._run(_work)
        except Exception as exc:  # noqa: BLE001 — audit must never break HITL
            logger.warning(
                "ToolApprovalRepository.record_pending failed (exec=%s req=%s): %s",
                execution_id, request_id, exc,
            )

    async def record_decision(
        self,
        execution_id: str,
        request_id: str,
        decision: str,
        *,
        reason: Optional[str] = None,
        decided_by: Optional[str] = None,
    ) -> None:
        """Update the row with the resolved decision (approved|denied|timeout)."""
        try:
            async def _work(session):
                await session.execute(
                    update(ToolApprovalModel)
                    .where(
                        ToolApprovalModel.execution_id == str(execution_id),
                        ToolApprovalModel.request_id == str(request_id),
                    )
                    .values(
                        decision=decision,
                        reason=reason,
                        decided_by=decided_by,
                        decided_at=datetime.now(timezone.utc),
                    )
                )
            await self._run(_work)
        except Exception as exc:  # noqa: BLE001 — audit must never break HITL
            logger.warning(
                "ToolApprovalRepository.record_decision failed (exec=%s req=%s): %s",
                execution_id, request_id, exc,
            )

    async def list_for_execution(self, execution_id: str) -> List[Dict[str, Any]]:
        """Return all approval records for an execution (newest first)."""
        try:
            rows = await self._all(
                select(ToolApprovalModel)
                .where(ToolApprovalModel.execution_id == str(execution_id))
                .order_by(ToolApprovalModel.created_at.desc())
            )
            return [self._to_dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ToolApprovalRepository.list_for_execution failed (exec=%s): %s",
                execution_id, exc,
            )
            return []

    @staticmethod
    def _to_dict(row: ToolApprovalModel) -> Dict[str, Any]:
        return {
            "id": row.id,
            "execution_id": row.execution_id,
            "request_id": row.request_id,
            "tool_name": row.tool_name,
            "args_summary": row.args_summary,
            "decision": row.decision,
            "decided_by": row.decided_by,
            "reason": row.reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        }
