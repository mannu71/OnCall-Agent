"""Agent self-improvement insights endpoint.

Surfaces trends and statistics derived from the agent's accumulated execution
history, knowledge base, and trajectory data.  Intended for operators who want
to understand how the agent is learning and where it is spending effort.

Routes
------
GET /insights          â€” Full insight report (anomaly trends, resolution rate,
                          KB growth, top tools used).
GET /insights/kb        â€” Knowledge base provenance breakdown (manual / agent /
                          verified counts, recent agent-written entries).
GET /insights/baselines â€” Baseline calibration history (automatic drift events).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    AnalysisHistoryModel,
    BaselineMetricModel,
    ExecutionModel,
    KnowledgeEntryModel,
    LogPatternModel,
)

router = APIRouter(prefix="/insights", tags=["insights"])

# ---------------------------------------------------------------------------
# Helper: date cutoff
# ---------------------------------------------------------------------------


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Main insights report
# ---------------------------------------------------------------------------


@router.get("", response_model=Dict[str, Any])
async def get_insights(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    log_group: Optional[str] = Query(None, description="Filter anomaly trends to a specific log group"),
) -> Dict[str, Any]:
    """Return a consolidated self-improvement insight report.

    Sections
    --------
    anomaly_trends
        Weekly anomaly counts per log group (or a single group when filtered).
    resolution_rate
        Percentage of agent executions that produced an agent-written playbook.
    kb_growth
        Knowledge base item counts over time, split by source.
    top_tools
        Most-called tools extracted from stored execution trajectories.
    calibration_events
        Recent baseline auto-calibration events recorded by the agent.
    """
    since = _cutoff(days)

    async with AsyncSessionLocal() as session:
        anomaly_trends = await _anomaly_trends(session, since, log_group)
        resolution_rate = await _resolution_rate(session, since)
        kb_growth = await _kb_growth(session, since)
        top_tools = await _top_tools(session, since)
        calibration_events = await _calibration_events(session, since)

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


# ---------------------------------------------------------------------------
# Knowledge base provenance breakdown
# ---------------------------------------------------------------------------


@router.get("/kb", response_model=Dict[str, Any])
async def get_kb_insights(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100, description="Max recent agent-written entries to return"),
) -> Dict[str, Any]:
    """Return a detailed breakdown of the knowledge base by provenance.

    Includes per-source counts for issues and patterns, recent agent-written
    entries, and the most populated issue categories.
    """
    since = _cutoff(days)

    async with AsyncSessionLocal() as session:
        # Issue counts by source.
        source_counts_result = await session.execute(
            select(KnowledgeEntryModel.source, func.count(KnowledgeEntryModel.id))
            .group_by(KnowledgeEntryModel.source)
        )
        issue_by_source = {row[0] or "manual": row[1] for row in source_counts_result}

        # Pattern count (patterns have no source column, just total).
        pattern_count_result = await session.execute(
            select(func.count(LogPatternModel.id))
        )
        total_patterns = pattern_count_result.scalar() or 0

        # Category breakdown.
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

        # Recent agent-written entries.
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


# ---------------------------------------------------------------------------
# Baseline calibration history
# ---------------------------------------------------------------------------


@router.get("/baselines", response_model=Dict[str, Any])
async def get_baseline_insights(
    days: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Return baseline calibration events and current baseline values."""
    since = _cutoff(days)

    async with AsyncSessionLocal() as session:
        # Calibration events from analysis_history.
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

        # Current baseline values.
        baselines_result = await session.execute(
            select(BaselineMetricModel).order_by(BaselineMetricModel.metric_name)
        )
        baselines = [
            {
                "metric_name": b.metric_name,
                "log_group": b.log_group,
                "normal_range": {"min": b.normal_range_min, "max": b.normal_range_max},
                "thresholds": {"warning": b.threshold_warning, "critical": b.threshold_critical},
                "updated_at": b.updated_at.isoformat() if b.updated_at else None,
            }
            for b in baselines_result.scalars().all()
        ]

    return {
        "calibration_events": events,
        "current_baselines": baselines,
    }


# ---------------------------------------------------------------------------
# Private query helpers
# ---------------------------------------------------------------------------


async def _anomaly_trends(
    session: Any,
    since: datetime,
    log_group: Optional[str],
) -> List[Dict[str, Any]]:
    """Weekly anomaly counts per log group."""
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


async def _resolution_rate(session: Any, since: datetime) -> Dict[str, Any]:
    """Percentage of agent executions that produced an agent-written knowledge entry."""
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

    rate = round((agent_entries / total_executions) * 100, 1) if total_executions > 0 else 0.0

    return {
        "successful_executions": total_executions,
        "agent_playbooks_created": agent_entries,
        "resolution_rate_pct": rate,
    }


async def _kb_growth(session: Any, since: datetime) -> Dict[str, Any]:
    """Issue and pattern counts over time, split by source."""
    # Weekly new issues by source.
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

    # Total counts by source (all time).
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


async def _top_tools(session: Any, since: datetime) -> List[Dict[str, Any]]:
    """Most frequently called tools extracted from execution trajectories.

    Trajectories are stored as a JSON array of message dicts.  Tool calls
    appear as ``{"role": "assistant", "tool_calls": [{"name": "..."}]}``.
    We use a Postgres JSON path query to unpack them efficiently.
    """
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
        # Trajectory column may be absent on older rows â€” return empty gracefully.
        return []


async def _calibration_events(session: Any, since: datetime) -> List[Dict[str, Any]]:
    """Recent auto-calibration events."""
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
