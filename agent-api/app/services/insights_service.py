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
)

logger = logging.getLogger(__name__)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an OKF frontmatter ``timestamp`` (ISO 8601) to an aware datetime."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _week_start(dt: datetime) -> str:
    """Monday-anchored ISO date of the week containing *dt* (mirrors date_trunc)."""
    monday = (dt - timedelta(days=dt.weekday())).date()
    return monday.isoformat()


def _load_bundle_docs() -> List[Dict[str, Any]]:
    """Load OKF known-issue + log-pattern concepts as analytics rows.

    Replaces the dropped ``knowledge_entries`` / ``log_patterns`` tables — the
    bundle files on disk are the source of truth. Best-effort; returns [] on any
    error so a dashboard never breaks.
    """
    try:
        from app.core.knowledge import get_default_bundle
    except Exception:  # noqa: BLE001
        return []
    bundle = get_default_bundle()
    rows: List[Dict[str, Any]] = []
    for section in ("known-issues", "log-patterns"):
        for p in bundle.list_concepts(section):
            doc = bundle.read_concept(p)
            if not doc:
                continue
            fm = doc.get("frontmatter") or {}
            tags = list(fm.get("tags") or [])
            rows.append({
                "section": section,
                "slug": p.stem,
                "title": fm.get("title") or p.stem,
                "source": fm.get("source") or "manual",
                "category": (tags[0] if tags else None),
                "created_at": _parse_ts(fm.get("timestamp")),
            })
    return rows


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
        docs = _load_bundle_docs()
        issues = [d for d in docs if d["section"] == "known-issues"]

        issue_by_source: Dict[str, int] = {}
        for d in issues:
            issue_by_source[d["source"]] = issue_by_source.get(d["source"], 0) + 1

        total_patterns = sum(1 for d in docs if d["section"] == "log-patterns")

        cat_counts: Dict[str, int] = {}
        for d in issues:
            key = d["category"] or "uncategorized"
            cat_counts[key] = cat_counts.get(key, 0) + 1
        top_categories = [
            {"category": k, "count": v}
            for k, v in sorted(cat_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ]

        recent_agent = [
            {
                "id": d["slug"],
                "title": d["title"],
                "category": d["category"],
                "created_at": d["created_at"].isoformat() if d["created_at"] else None,
            }
            for d in sorted(
                (d for d in issues
                 if d["source"] == "agent" and d["created_at"] and d["created_at"] >= since),
                key=lambda d: d["created_at"], reverse=True,
            )[:limit]
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

        agent_entries = sum(
            1 for d in _load_bundle_docs()
            if d["section"] == "known-issues" and d["source"] == "agent"
            and d["created_at"] and d["created_at"] >= since
        )

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
        docs = _load_bundle_docs()
        issues = [d for d in docs if d["section"] == "known-issues"]

        # Weekly new issues by source (Monday-anchored, mirrors date_trunc('week')).
        weekly_counts: Dict[tuple, int] = {}
        for d in issues:
            if not d["created_at"] or d["created_at"] < since:
                continue
            key = (d["source"], _week_start(d["created_at"]))
            weekly_counts[key] = weekly_counts.get(key, 0) + 1
        weekly = [
            {"source": src, "week": week, "new_issues": count}
            for (src, week), count in sorted(
                weekly_counts.items(), key=lambda kv: kv[0][1], reverse=True
            )
        ]

        totals: Dict[str, int] = {}
        for d in issues:
            totals[d["source"]] = totals.get(d["source"], 0) + 1

        total_patterns = sum(1 for d in docs if d["section"] == "log-patterns")

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
