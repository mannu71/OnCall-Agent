"""Knowledge base service for RAG-based log analysis.

This service provides vector similarity search for log patterns, known issues,
and baseline metrics using pgvector.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
import json

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    LogPatternModel,
    KnowledgeEntryModel,
    BaselineMetricModel,
    AnalysisHistoryModel
)

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating embeddings using AWS Bedrock."""
    
    def __init__(self, region: str = "us-east-1"):
        """Initialize embedding service.
        
        Args:
            region: AWS region for Bedrock
        """
        self.region = region
        self._client = None
    
    @property
    def client(self):
        """Get Bedrock client (lazy initialization)."""
        if self._client is None:
            import boto3
            self._client = boto3.client('bedrock-runtime', region_name=self.region)
        return self._client
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using Amazon Titan.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector (1536 dimensions for Titan)
        """
        from app.core.thread_pools import run_in_aws_pool

        max_tokens = 8000
        if len(text) > max_tokens:
            text = text[:max_tokens]

        # Call Bedrock API
        response = await run_in_aws_pool(
            lambda: self.client.invoke_model(
                modelId='amazon.titan-embed-text-v1',
                body=json.dumps({'inputText': text})
            )
        )
        
        result = json.loads(response['body'].read())
        return result.get('embedding', [])
    
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts (bounded parallel)."""
        if not texts:
            return []

        from app.config import settings

        sem = asyncio.Semaphore(settings.embedding_concurrency)

        async def _one(text: str) -> List[float]:
            async with sem:
                return await self.generate_embedding(text)

        return list(await asyncio.gather(*[_one(t) for t in texts]))


class KnowledgeBaseService:
    """Service for knowledge base operations using pgvector."""
    
    def __init__(self, embedding_service: Optional[EmbeddingService] = None):
        """Initialize knowledge base service.
        
        Args:
            embedding_service: Embedding service for vector generation
        """
        self.embedding_service = embedding_service or EmbeddingService()
    
    # ============================================
    # LOG PATTERN OPERATIONS
    # ============================================
    
    async def add_log_pattern(
        self,
        name: str,
        pattern: str,
        pattern_type: str,
        severity: int = 1,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add a log pattern to the knowledge base.
        
        Args:
            name: Pattern name
            pattern: Pattern regex or text
            pattern_type: Type of pattern (error, warning, info, custom)
            severity: Severity level (1-5, 5 being most severe)
            description: Pattern description
            
        Returns:
            Created pattern
        """
        # Generate embedding for pattern
        embedding = await self.embedding_service.generate_embedding(f"{name} {pattern} {description or ''}")
        
        async with AsyncSessionLocal() as session:
            pattern_model = LogPatternModel(
                name=name,
                pattern=pattern,
                pattern_type=pattern_type,
                severity=severity,
                description=description,
                embedding=embedding,
                created_at=datetime.now(timezone.utc)
            )
            session.add(pattern_model)
            await session.commit()
            await session.refresh(pattern_model)
            
            return {
                "id": pattern_model.id,
                "name": pattern_model.name,
                "pattern": pattern_model.pattern,
                "pattern_type": pattern_model.pattern_type,
                "severity": pattern_model.severity
            }
    
    async def search_similar_patterns(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Search for similar log patterns using vector similarity.
        
        Args:
            query: Query text to match
            limit: Maximum results
            threshold: Similarity threshold (0-1)
            
        Returns:
            List of similar patterns with similarity scores
        """
        # Generate embedding for query
        query_embedding = await self.embedding_service.generate_embedding(query)
        
        async with AsyncSessionLocal() as session:
            # Use pgvector cosine similarity search
            # Note: This requires the pgvector extension
            from sqlalchemy import text
            
            query_str = """
                SELECT id, name, pattern, pattern_type, severity, description,
                       1 - (embedding <=> :embedding::vector) as similarity
                FROM log_patterns
                WHERE 1 - (embedding <=> :embedding::vector) > :threshold
                ORDER BY similarity DESC
                LIMIT :limit
            """
            
            result = await session.execute(
                text(query_str),
                {
                    "embedding": str(query_embedding),
                    "threshold": threshold,
                    "limit": limit
                }
            )
            
            patterns = []
            for row in result:
                patterns.append({
                    "id": row.id,
                    "name": row.name,
                    "pattern": row.pattern,
                    "pattern_type": row.pattern_type,
                    "severity": row.severity,
                    "description": row.description,
                    "similarity": float(row.similarity) if row.similarity else 0.0
                })
            
            return patterns
    
    async def list_log_patterns(
        self,
        pattern_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List all log patterns.
        
        Args:
            pattern_type: Filter by type
            limit: Maximum results
            
        Returns:
            List of patterns
        """
        async with AsyncSessionLocal() as session:
            query = select(LogPatternModel).limit(limit)
            if pattern_type:
                query = query.where(LogPatternModel.pattern_type == pattern_type)
            
            result = await session.execute(query)
            patterns = result.scalars().all()
            
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "pattern": p.pattern,
                    "pattern_type": p.pattern_type,
                    "severity": p.severity,
                    "description": p.description
                }
                for p in patterns
            ]
    
    # ============================================
    # KNOWLEDGE ENTRY OPERATIONS
    # ============================================

    async def add_known_issue(
        self,
        title: str,
        description: str,
        symptoms: List[str],
        solution: str,
        category: str,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """Add a known issue to the knowledge base.
        
        Args:
            title: Issue title
            description: Detailed description
            symptoms: List of symptoms/signatures
            solution: Resolution steps
            category: Issue category
            source: Provenance — 'manual', 'agent', or 'verified'
            
        Returns:
            Created known issue
        """
        # Generate embedding from combined text
        combined_text = f"{title} {description} {' '.join(symptoms)}"
        embedding = await self.embedding_service.generate_embedding(combined_text)
        
        async with AsyncSessionLocal() as session:
            issue_model = KnowledgeEntryModel(
                title=title,
                description=description,
                symptoms=symptoms,
                solution=solution,
                category=category,
                source=source,
                embedding=embedding,
                created_at=datetime.now(timezone.utc)
            )
            session.add(issue_model)
            await session.commit()
            await session.refresh(issue_model)
            
            return {
                "id": issue_model.id,
                "title": issue_model.title,
                "category": issue_model.category,
                "source": issue_model.source,
            }

    async def upsert_playbook(
        self,
        title: str,
        symptoms: List[str],
        solution: str,
        category: str,
        source: str = "agent",
    ) -> Dict[str, Any]:
        """Create or update a runbook/playbook for a known issue type.

        Matches on (title, category). Updates solution and updated_at when an
        existing entry is found; creates a new entry otherwise.

        Args:
            title: Short issue title (used as the match key alongside category).
            symptoms: List of symptom strings.
            solution: Resolution steps or investigation procedure.
            category: Issue category.
            source: Provenance — typically 'agent' when written by the agent.

        Returns:
            Dict with id, title, category, source, and whether it was created or updated.
        """
        combined_text = f"{title} {' '.join(symptoms)} {solution}"
        embedding = await self.embedding_service.generate_embedding(combined_text)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(KnowledgeEntryModel).where(
                    KnowledgeEntryModel.title == title,
                    KnowledgeEntryModel.category == category,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.symptoms = symptoms
                existing.solution = solution
                existing.source = source
                existing.embedding = embedding
                existing.updated_at = datetime.now(timezone.utc)
                await session.commit()
                await session.refresh(existing)
                return {
                    "id": existing.id,
                    "title": existing.title,
                    "category": existing.category,
                    "source": existing.source,
                    "action": "updated",
                }
            else:
                issue_model = KnowledgeEntryModel(
                    title=title,
                    description=solution[:500],
                    symptoms=symptoms,
                    solution=solution,
                    category=category,
                    source=source,
                    embedding=embedding,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(issue_model)
                await session.commit()
                await session.refresh(issue_model)
                return {
                    "id": issue_model.id,
                    "title": issue_model.title,
                    "category": issue_model.category,
                    "source": issue_model.source,
                    "action": "created",
                }

    async def patch_playbook_solution(
        self,
        issue_id: int,
        new_solution: str,
        source: str = "agent",
    ) -> Dict[str, Any]:
        """Update the solution field of an existing known issue.

        Args:
            issue_id: Primary key of the KnowledgeEntryModel to update.
            new_solution: Replacement solution text.
            source: Provenance of the update.

        Returns:
            Dict with id, title, and source, or an error key if not found.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(KnowledgeEntryModel).where(KnowledgeEntryModel.id == issue_id)
            )
            existing = result.scalar_one_or_none()

            if not existing:
                return {"error": f"KnowledgeEntry id={issue_id} not found"}

            existing.solution = new_solution
            existing.source = source
            existing.updated_at = datetime.now(timezone.utc)

            # Regenerate embedding to reflect updated solution content.
            combined_text = f"{existing.title} {' '.join(existing.symptoms or [])} {new_solution}"
            existing.embedding = await self.embedding_service.generate_embedding(combined_text)

            await session.commit()
            await session.refresh(existing)
            return {
                "id": existing.id,
                "title": existing.title,
                "source": existing.source,
                "action": "patched",
            }

    
    async def search_known_issues(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
        threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Search for known issues using vector similarity.
        
        Args:
            query: Query text (e.g., error message)
            category: Filter by category
            limit: Maximum results
            threshold: Similarity threshold
            
        Returns:
            List of matching known issues
        """
        query_embedding = await self.embedding_service.generate_embedding(query)
        
        async with AsyncSessionLocal() as session:
            from sqlalchemy import text
            
            category_filter = ""
            params = {
                "embedding": str(query_embedding),
                "threshold": threshold,
                "limit": limit
            }
            
            if category:
                category_filter = "AND category = :category"
                params["category"] = category
            
            query_str = f"""
                SELECT id, title, description, symptoms, solution, category,
                       1 - (embedding <=> :embedding::vector) as similarity
                FROM knowledge_entries
                WHERE 1 - (embedding <=> :embedding::vector) > :threshold
                {category_filter}
                ORDER BY similarity DESC
                LIMIT :limit
            """
            
            result = await session.execute(text(query_str), params)
            
            issues = []
            for row in result:
                issues.append({
                    "id": row.id,
                    "title": row.title,
                    "description": row.description,
                    "symptoms": row.symptoms,
                    "solution": row.solution,
                    "category": row.category,
                    "similarity": float(row.similarity) if row.similarity else 0.0
                })
            
            return issues
    
    # ============================================
    # SKILL RECALL (convenience wrapper)
    # ============================================

    async def recall_skills_for_agent(
        self,
        query: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return matching skills for injection into the agent prompt.

        Delegates to :class:`~app.core.skills.SkillService`.  Returns an
        empty list (never raises) so callers don't need to guard.
        """
        try:
            from app.core.skills import skill_service
            return await skill_service.recall(query, limit=limit)
        except Exception as exc:
            logger.debug("knowledge_base.recall_skills_for_agent: %s", exc)
            return []

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
        """Set or update a baseline metric.
        
        Args:
            metric_name: Name of the metric
            log_group: Associated log group
            normal_range_min: Minimum normal value
            normal_range_max: Maximum normal value
            threshold_warning: Warning threshold
            threshold_critical: Critical threshold
            time_window: Time window for the metric
            
        Returns:
            Created/updated baseline metric
        """
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
        """Get baseline metrics.
        
        Args:
            log_group: Filter by log group
            
        Returns:
            List of baseline metrics
        """
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
        """Check if a metric value is anomalous.
        
        Args:
            metric_name: Name of the metric
            log_group: Log group
            current_value: Current metric value
            
        Returns:
            Anomaly check result
        """
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

        Args:
            metric_name: Name of the metric to calibrate.
            log_group: Log group the metric belongs to.
            observed_value: The current observed value that triggered recalibration.
            calibration_window: Number of recent analyses to check (default 5).

        Returns:
            Dict describing whether calibration occurred and old/new range values.
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
        """Record an analysis for historical tracking.
        
        Args:
            log_group: Analyzed log group
            analysis_type: Type of analysis
            start_time: Analysis start time
            end_time: Analysis end time
            summary: Analysis summary
            anomalies_found: Number of anomalies found
            patterns_matched: Number of patterns matched
            
        Returns:
            Created analysis record
        """
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
        """Get analysis history.
        
        Args:
            log_group: Filter by log group
            analysis_type: Filter by analysis type
            limit: Maximum results
            
        Returns:
            List of analysis records
        """
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


# Singleton instances
embedding_service = EmbeddingService()
knowledge_base = KnowledgeBaseService(embedding_service)