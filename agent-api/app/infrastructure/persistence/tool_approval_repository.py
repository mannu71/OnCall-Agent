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
        *,
        risk_tier: Optional[str] = None,
        supervisor_verdict: Optional[str] = None,
        supervisor_reasoning: Optional[str] = None,
    ) -> None:
        """Insert a ``pending`` row before the agent blocks on approval.

        The Action Supervisor's classification/verdict (when it ran) are stored
        at insert time so they're visible on the approval card and in the audit
        trail even while the human decision is still outstanding.
        """
        try:
            async def _work(session):
                session.add(ToolApprovalModel(
                    execution_id=str(execution_id),
                    request_id=str(request_id),
                    tool_name=tool_name,
                    args_summary=(args_summary or "")[:4000],
                    decision="pending",
                    risk_tier=risk_tier,
                    supervisor_verdict=supervisor_verdict,
                    supervisor_reasoning=(supervisor_reasoning or None),
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

    async def record_supervisor_verdict(
        self,
        execution_id: str,
        request_id: str,
        *,
        risk_tier: Optional[str] = None,
        supervisor_verdict: Optional[str] = None,
        supervisor_reasoning: Optional[str] = None,
    ) -> None:
        """Attach/refresh the Action Supervisor's verdict on an existing row.

        Used when the pending row was written before the review completed, or to
        record a shadow-mode verdict that doesn't itself decide the gate.
        """
        try:
            async def _work(session):
                await session.execute(
                    update(ToolApprovalModel)
                    .where(
                        ToolApprovalModel.execution_id == str(execution_id),
                        ToolApprovalModel.request_id == str(request_id),
                    )
                    .values(
                        risk_tier=risk_tier,
                        supervisor_verdict=supervisor_verdict,
                        supervisor_reasoning=(supervisor_reasoning or None),
                    )
                )
            await self._run(_work)
        except Exception as exc:  # noqa: BLE001 — audit must never break HITL
            logger.warning(
                "ToolApprovalRepository.record_supervisor_verdict failed (exec=%s req=%s): %s",
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

    async def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent approval records across all executions.

        Powers the cross-execution supervision audit view. Best-effort → [] on
        error so the endpoint degrades gracefully on an un-migrated DB."""
        try:
            limit = max(1, min(int(limit), 500))
            rows = await self._all(
                select(ToolApprovalModel)
                .order_by(ToolApprovalModel.created_at.desc())
                .limit(limit)
            )
            return [self._to_dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("ToolApprovalRepository.list_recent failed: %s", exc)
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
            "risk_tier": row.risk_tier,
            "supervisor_verdict": row.supervisor_verdict,
            "supervisor_reasoning": row.supervisor_reasoning,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        }
