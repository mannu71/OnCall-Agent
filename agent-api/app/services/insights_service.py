"""Insights service — agent self-improvement analytics."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    AnalysisHistoryModel,
    BaselineMetricModel,
    ExecutionModel,
    KnowledgeEntryModel,
    LogPatternModel,
)

logger = logging.getLogger(__name__)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


class InsightsService:
    """Aggregates execution, KB, and trajectory data for operator dashboards."""

    async def get_report(
        self,
        *,
        days: int = 30,
        log_group: Optional[str] = None,
    ) -> Dict[str, Any]:
        since = _cutoff(days)

        async def _anomaly_trends() -> List[Dict[str, Any]]:
            async with AsyncSessionLocal() as session:
                return await self._anomaly_trends(session, since, log_group)

        async def _resolution_rate() -> Dict[str, Any]:
            async with AsyncSessionLocal() as session:
                return await self._resolution_rate(session, since)

        async def _kb_growth() -> Dict[str, Any]:
            async with AsyncSessionLocal() as session:
                return await self._kb_growth(session, since)

        async def _top_tools() -> List[Dict[str, Any]]:
            async with AsyncSessionLocal() as session:
                return await self._top_tools(session, since)

        async def _calibration_events() -> List[Dict[str, Any]]:
            async with AsyncSessionLocal() as session:
                return await self._calibration_events(session, since)

        (
            anomaly_trends,
            resolution_rate,
            kb_growth,
            top_tools,
            calibration_events,
        ) = await asyncio.gather(
            _anomaly_trends(),
            _resolution_rate(),
            _kb_growth(),
            _top_tools(),
            _calibration_events(),
        )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "log_group_filter": log_group,
            "anomaly_trends": anomaly_trends,
            "resolution_rate": resolution_rate,
            "kb_growth": kb_growth,
            "top_tools": top_tools,
            "calibration_events": calibration_events,
        }

    async def get_kb_breakdown(
        self, *, days: int = 30, limit: int = 20
    ) -> Dict[str, Any]:
        since = _cutoff(days)

        async with AsyncSessionLocal() as session:
            source_counts_result = await session.execute(
                select(KnowledgeEntryModel.source, func.count(KnowledgeEntryModel.id))
                .group_by(KnowledgeEntryModel.source)
            )
            issue_by_source = {row[0] or "manual": row[1] for row in source_counts_result}

            pattern_count_result = await session.execute(
                select(func.count(LogPatternModel.id))
            )
            total_patterns = pattern_count_result.scalar() or 0

            category_result = await session.execute(
                select(KnowledgeEntryModel.category, func.count(KnowledgeEntryModel.id))
                .group_by(KnowledgeEntryModel.category)
                .order_by(func.count(KnowledgeEntryModel.id).desc())
                .limit(10)
            )
            top_categories = [
                {"category": row[0] or "uncategorized", "count": row[1]}
                for row in category_result
            ]

            recent_agent_result = await session.execute(
                select(KnowledgeEntryModel)
                .where(
                    KnowledgeEntryModel.source == "agent",
                    KnowledgeEntryModel.created_at >= since,
                )
                .order_by(KnowledgeEntryModel.created_at.desc())
                .limit(limit)
            )
            recent_agent = [
                {
                    "id": issue.id,
                    "title": issue.title,
                    "category": issue.category,
                    "created_at": issue.created_at.isoformat() if issue.created_at else None,
                }
                for issue in recent_agent_result.scalars().all()
            ]

        return {
            "issues_by_source": issue_by_source,
            "total_patterns": total_patterns,
            "top_categories": top_categories,
            "recent_agent_entries": recent_agent,
        }

    async def get_baseline_history(self, *, days: int = 30) -> Dict[str, Any]:
        since = _cutoff(days)

        async with AsyncSessionLocal() as session:
            cal_result = await session.execute(
                select(AnalysisHistoryModel)
                .where(
                    AnalysisHistoryModel.analysis_type == "baseline_calibration",
                    AnalysisHistoryModel.created_at >= since,
                )
                .order_by(AnalysisHistoryModel.created_at.desc())
                .limit(50)
            )
            events = [
                {
                    "id": row.id,
                    "log_group": row.log_group,
                    "summary": row.summary,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in cal_result.scalars().all()
            ]

            baselines_result = await session.execute(
                select(BaselineMetricModel).order_by(BaselineMetricModel.metric_name)
            )
            baselines = [
                {
                    "metric_name": b.metric_name,
                    "log_group": b.log_group,
                    "normal_range": {"min": b.normal_range_min, "max": b.normal_range_max},
                    "thresholds": {
                        "warning": b.threshold_warning,
                        "critical": b.threshold_critical,
                    },
                    "updated_at": b.updated_at.isoformat() if b.updated_at else None,
                }
                for b in baselines_result.scalars().all()
            ]

        return {
            "calibration_events": events,
            "current_baselines": baselines,
        }

    async def _anomaly_trends(
        self,
        session: Any,
        since: datetime,
        log_group: Optional[str],
    ) -> List[Dict[str, Any]]:
        query = (
            select(
                AnalysisHistoryModel.log_group,
                func.date_trunc("week", AnalysisHistoryModel.created_at).label("week"),
                func.sum(AnalysisHistoryModel.anomalies_found).label("total_anomalies"),
                func.count(AnalysisHistoryModel.id).label("analysis_count"),
            )
            .where(AnalysisHistoryModel.created_at >= since)
            .group_by(
                AnalysisHistoryModel.log_group,
                func.date_trunc("week", AnalysisHistoryModel.created_at),
            )
            .order_by(
                func.date_trunc("week", AnalysisHistoryModel.created_at).desc(),
                AnalysisHistoryModel.log_group,
            )
        )
        if log_group:
            query = query.where(AnalysisHistoryModel.log_group == log_group)

        result = await session.execute(query)
        return [
            {
                "log_group": row.log_group,
                "week": row.week.isoformat() if row.week else None,
                "total_anomalies": int(row.total_anomalies or 0),
                "analysis_count": int(row.analysis_count or 0),
            }
            for row in result
        ]

    async def _resolution_rate(self, session: Any, since: datetime) -> Dict[str, Any]:
        total_result = await session.execute(
            select(func.count(ExecutionModel.id)).where(
                ExecutionModel.started_at >= since,
                ExecutionModel.status == "success",
            )
        )
        total_executions = total_result.scalar() or 0

        agent_entries_result = await session.execute(
            select(func.count(KnowledgeEntryModel.id)).where(
                KnowledgeEntryModel.source == "agent",
                KnowledgeEntryModel.created_at >= since,
            )
        )
        agent_entries = agent_entries_result.scalar() or 0

        rate = (
            round((agent_entries / total_executions) * 100, 1)
            if total_executions > 0
            else 0.0
        )

        return {
            "successful_executions": total_executions,
            "agent_playbooks_created": agent_entries,
            "resolution_rate_pct": rate,
        }

    async def _kb_growth(self, session: Any, since: datetime) -> Dict[str, Any]:
        weekly_result = await session.execute(
            select(
                KnowledgeEntryModel.source,
                func.date_trunc("week", KnowledgeEntryModel.created_at).label("week"),
                func.count(KnowledgeEntryModel.id).label("count"),
            )
            .where(KnowledgeEntryModel.created_at >= since)
            .group_by(
                KnowledgeEntryModel.source,
                func.date_trunc("week", KnowledgeEntryModel.created_at),
            )
            .order_by(func.date_trunc("week", KnowledgeEntryModel.created_at).desc())
        )
        weekly = [
            {
                "source": row.source or "manual",
                "week": row.week.isoformat() if row.week else None,
                "new_issues": int(row.count or 0),
            }
            for row in weekly_result
        ]

        totals_result = await session.execute(
            select(KnowledgeEntryModel.source, func.count(KnowledgeEntryModel.id))
            .group_by(KnowledgeEntryModel.source)
        )
        totals = {row[0] or "manual": row[1] for row in totals_result}

        patterns_result = await session.execute(select(func.count(LogPatternModel.id)))
        total_patterns = patterns_result.scalar() or 0

        return {
            "total_issues_by_source": totals,
            "total_patterns": total_patterns,
            "weekly_new_issues": weekly,
        }

    async def _top_tools(self, session: Any, since: datetime) -> List[Dict[str, Any]]:
        try:
            sql = text("""
                SELECT
                    tc ->> 'name'  AS tool_name,
                    COUNT(*)       AS call_count
                FROM executions e,
                     jsonb_array_elements(e.trajectory) AS msg,
                     jsonb_array_elements(msg -> 'tool_calls') AS tc
                WHERE e.started_at >= :since
                  AND e.trajectory IS NOT NULL
                  AND msg ->> 'role' = 'assistant'
                  AND msg -> 'tool_calls' IS NOT NULL
                GROUP BY tool_name
                ORDER BY call_count DESC
                LIMIT 20
            """)
            result = await session.execute(sql, {"since": since})
            return [
                {"tool": row.tool_name, "call_count": int(row.call_count)}
                for row in result
                if row.tool_name
            ]
        except Exception:
            return []

    async def _calibration_events(
        self, session: Any, since: datetime
    ) -> List[Dict[str, Any]]:
        result = await session.execute(
            select(AnalysisHistoryModel)
            .where(
                AnalysisHistoryModel.analysis_type == "baseline_calibration",
                AnalysisHistoryModel.created_at >= since,
            )
            .order_by(AnalysisHistoryModel.created_at.desc())
            .limit(10)
        )
        return [
            {
                "log_group": row.log_group,
                "summary": row.summary,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in result.scalars().all()
        ]


insights_service = InsightsService()
