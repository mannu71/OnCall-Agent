"""Baseline-metric and analysis-history service.

Formerly a pgvector RAG service for log patterns / known issues. Those tables
(``log_patterns``, ``knowledge_entries``) and the Bedrock Titan
``EmbeddingService`` were removed — durable operational knowledge now lives in
the OKF knowledge bundle and is recalled via Postgres FTS
(:mod:`app.services.semantic_memory`, bank ``kb``). What remains here are the
non-embedding concerns: baseline metrics and analysis history.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    BaselineMetricModel,
    AnalysisHistoryModel,
)

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """Baseline-metric and analysis-history operations (no embeddings)."""

    # ============================================
    # BASELINE METRIC OPERATIONS
    # ============================================

    async def set_baseline_metric(
        self,
        metric_name: str,
        log_group: str,
        normal_range_min: float,
        normal_range_max: float,
        threshold_warning: float,
        threshold_critical: float,
        time_window: str = "5m"
    ) -> Dict[str, Any]:
        """Set or update a baseline metric."""
        async with AsyncSessionLocal() as session:
            # Check if exists
            result = await session.execute(
                select(BaselineMetricModel).where(
                    BaselineMetricModel.metric_name == metric_name,
                    BaselineMetricModel.log_group == log_group
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.normal_range_min = normal_range_min
                existing.normal_range_max = normal_range_max
                existing.threshold_warning = threshold_warning
                existing.threshold_critical = threshold_critical
                existing.time_window = time_window
                existing.updated_at = datetime.now(timezone.utc)
                await session.commit()
                await session.refresh(existing)
                return {"id": existing.id, "metric_name": existing.metric_name}
            else:
                metric = BaselineMetricModel(
                    metric_name=metric_name,
                    log_group=log_group,
                    normal_range_min=normal_range_min,
                    normal_range_max=normal_range_max,
                    threshold_warning=threshold_warning,
                    threshold_critical=threshold_critical,
                    time_window=time_window,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(metric)
                await session.commit()
                await session.refresh(metric)
                return {"id": metric.id, "metric_name": metric.metric_name}

    async def get_baseline_metrics(
        self,
        log_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get baseline metrics, optionally filtered by log group."""
        async with AsyncSessionLocal() as session:
            query = select(BaselineMetricModel)
            if log_group:
                query = query.where(BaselineMetricModel.log_group == log_group)

            result = await session.execute(query)
            metrics = result.scalars().all()

            return [
                {
                    "id": m.id,
                    "metric_name": m.metric_name,
                    "log_group": m.log_group,
                    "normal_range": {
                        "min": m.normal_range_min,
                        "max": m.normal_range_max
                    },
                    "thresholds": {
                        "warning": m.threshold_warning,
                        "critical": m.threshold_critical
                    },
                    "time_window": m.time_window
                }
                for m in metrics
            ]

    async def check_metric_anomaly(
        self,
        metric_name: str,
        log_group: str,
        current_value: float
    ) -> Dict[str, Any]:
        """Check if a metric value is anomalous against its baseline."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(BaselineMetricModel).where(
                    BaselineMetricModel.metric_name == metric_name,
                    BaselineMetricModel.log_group == log_group
                )
            )
            metric = result.scalar_one_or_none()

            if not metric:
                return {
                    "anomaly": False,
                    "reason": "No baseline found"
                }

            is_anomaly = False
            severity = "normal"

            if current_value >= metric.threshold_critical:
                is_anomaly = True
                severity = "critical"
            elif current_value >= metric.threshold_warning:
                is_anomaly = True
                severity = "warning"
            elif current_value < metric.normal_range_min or current_value > metric.normal_range_max:
                is_anomaly = True
                severity = "low"

            return {
                "anomaly": is_anomaly,
                "severity": severity,
                "current_value": current_value,
                "baseline": {
                    "normal_range": {
                        "min": metric.normal_range_min,
                        "max": metric.normal_range_max
                    },
                    "thresholds": {
                        "warning": metric.threshold_warning,
                        "critical": metric.threshold_critical
                    }
                }
            }

    async def recalibrate_baseline(
        self,
        metric_name: str,
        log_group: str,
        observed_value: float,
        calibration_window: int = 5,
    ) -> Dict[str, Any]:
        """Auto-calibrate a baseline metric using a dampened moving average.

        Checks the last ``calibration_window`` analysis records for this log
        group.  If all of them report anomalies, the baseline is likely stale
        and is nudged towards the observed value at a learning rate of 0.2.
        Each calibration event is recorded in ``analysis_history`` with type
        ``"baseline_calibration"`` so humans can audit drift.
        """
        async with AsyncSessionLocal() as session:
            # Load existing baseline.
            result = await session.execute(
                select(BaselineMetricModel).where(
                    BaselineMetricModel.metric_name == metric_name,
                    BaselineMetricModel.log_group == log_group,
                )
            )
            metric = result.scalar_one_or_none()
            if not metric:
                return {"recalibrated": False, "reason": "no baseline found"}

            # Check recent analysis anomaly counts.
            history_result = await session.execute(
                select(AnalysisHistoryModel)
                .where(AnalysisHistoryModel.log_group == log_group)
                .order_by(AnalysisHistoryModel.created_at.desc())
                .limit(calibration_window)
            )
            recent = history_result.scalars().all()

            if len(recent) < calibration_window:
                return {"recalibrated": False, "reason": "insufficient history"}

            all_anomalous = all((r.anomalies_found or 0) > 0 for r in recent)
            if not all_anomalous:
                return {"recalibrated": False, "reason": "not all recent analyses show anomalies"}

            # Apply dampened moving average (learning rate = 0.2).
            alpha = 0.2
            old_min = float(metric.normal_range_min or 0.0)
            old_max = float(metric.normal_range_max or 0.0)
            new_min = (1 - alpha) * old_min + alpha * min(observed_value, old_min)
            new_max = (1 - alpha) * old_max + alpha * max(observed_value, old_max)

            metric.normal_range_min = new_min
            metric.normal_range_max = new_max
            metric.updated_at = datetime.now(timezone.utc)
            await session.commit()

        # Record the calibration event in analysis history.
        await self.record_analysis(
            log_group=log_group,
            analysis_type="baseline_calibration",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            summary=(
                f"Auto-recalibrated '{metric_name}': "
                f"range [{old_min:.3f}, {old_max:.3f}] → [{new_min:.3f}, {new_max:.3f}] "
                f"based on observed_value={observed_value:.3f}"
            ),
            anomalies_found=0,
            patterns_matched=0,
        )

        return {
            "recalibrated": True,
            "metric_name": metric_name,
            "log_group": log_group,
            "old_range": {"min": old_min, "max": old_max},
            "new_range": {"min": new_min, "max": new_max},
            "observed_value": observed_value,
        }

    # ============================================
    # ANALYSIS HISTORY OPERATIONS
    # ============================================

    async def record_analysis(
        self,
        log_group: str,
        analysis_type: str,
        start_time: datetime,
        end_time: datetime,
        summary: str,
        anomalies_found: int = 0,
        patterns_matched: int = 0
    ) -> Dict[str, Any]:
        """Record an analysis for historical tracking."""
        async with AsyncSessionLocal() as session:
            analysis = AnalysisHistoryModel(
                log_group=log_group,
                analysis_type=analysis_type,
                start_time=start_time,
                end_time=end_time,
                summary=summary,
                anomalies_found=anomalies_found,
                patterns_matched=patterns_matched,
                created_at=datetime.now(timezone.utc)
            )
            session.add(analysis)
            await session.commit()
            await session.refresh(analysis)

            return {
                "id": analysis.id,
                "log_group": analysis.log_group,
                "analysis_type": analysis.analysis_type
            }

    async def get_analysis_history(
        self,
        log_group: Optional[str] = None,
        analysis_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get analysis history."""
        async with AsyncSessionLocal() as session:
            query = select(AnalysisHistoryModel).order_by(
                AnalysisHistoryModel.created_at.desc()
            ).limit(limit)

            if log_group:
                query = query.where(AnalysisHistoryModel.log_group == log_group)
            if analysis_type:
                query = query.where(AnalysisHistoryModel.analysis_type == analysis_type)

            result = await session.execute(query)
            analyses = result.scalars().all()

            return [
                {
                    "id": a.id,
                    "log_group": a.log_group,
                    "analysis_type": a.analysis_type,
                    "start_time": a.start_time.isoformat() if a.start_time else None,
                    "end_time": a.end_time.isoformat() if a.end_time else None,
                    "summary": a.summary,
                    "anomalies_found": a.anomalies_found,
                    "patterns_matched": a.patterns_matched,
                    "created_at": a.created_at.isoformat() if a.created_at else None
                }
                for a in analyses
            ]


# Singleton instance
knowledge_base = KnowledgeBaseService()
