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
    KnownIssueModel,
    BaselineMetricModel,
    AnalysisHistoryModel
)

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating embeddings using configurable providers."""
    
    def __init__(
        self,
        provider: str = None,
        model: str = None,
        region: str = None,
        api_key: str = None,
        base_url: str = None,
        access_key_id: str = None,
        secret_access_key: str = None,
        session_token: str = None,
    ):
        """Initialize embedding service.
        
        Args:
            provider: Embedding provider (bedrock, openai, azure, cohere). Defaults to settings.
            model: Model ID for embeddings. Defaults to settings.
            region: AWS region for Bedrock. Defaults to settings.
            api_key: API key for non-AWS providers
            base_url: Custom base URL for API providers
            access_key_id: AWS access key ID for Bedrock
            secret_access_key: AWS secret access key for Bedrock
            session_token: AWS session token for Bedrock (optional)
        """
        from app.config import settings
        
        self.provider = (provider or settings.embedding_provider).lower()
        self.model = model or settings.embedding_model
        self.region = region or settings.embedding_region
        self.api_key = api_key
        self.base_url = base_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.session_token = session_token
        self._client = None
        
        logger.info(
            "EmbeddingService initialized: provider=%s, model=%s, region=%s",
            self.provider, self.model, self.region
        )
    
    @property
    def client(self):
        """Get embedding client (lazy initialization based on provider)."""
        if self._client is None:
            if self.provider == "bedrock":
                import boto3
                import os
                from botocore.config import Config
                
                # Respect AWS_SSL_VERIFY environment variable
                ssl_verify = os.environ.get("AWS_SSL_VERIFY", "true").lower() not in ("false", "0", "no")
                
                # Build boto3 client kwargs
                kwargs = {"region_name": self.region}
                
                # Use explicit credentials if provided (from Model Keys)
                if self.access_key_id and self.secret_access_key:
                    kwargs["aws_access_key_id"] = self.access_key_id
                    kwargs["aws_secret_access_key"] = self.secret_access_key
                    if self.session_token:
                        kwargs["aws_session_token"] = self.session_token
                    logger.info("Using explicit AWS credentials from Model Keys for embeddings")
                else:
                    logger.info("Using default AWS credential chain for embeddings")
                
                if ssl_verify:
                    self._client = boto3.client('bedrock-runtime', **kwargs)
                else:
                    kwargs["verify"] = False
                    kwargs["config"] = Config(retries={"max_attempts": 3})
                    self._client = boto3.client('bedrock-runtime', **kwargs)
                    
            elif self.provider == "openai":
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
            elif self.provider == "azure":
                from openai import AsyncAzureOpenAI
                self._client = AsyncAzureOpenAI(
                    api_key=self.api_key,
                    azure_endpoint=self.base_url,
                    api_version="2024-02-01"
                )
            elif self.provider == "cohere":
                import cohere
                self._client = cohere.AsyncClient(api_key=self.api_key)
            else:
                raise ValueError(f"Unsupported embedding provider: {self.provider}")
                
        return self._client
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using configured provider.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        import asyncio
        from botocore.exceptions import ClientError
        
        # Truncate text if too long
        max_tokens = 8000
        if len(text) > max_tokens:
            text = text[:max_tokens]
        
        # Provider-specific embedding generation with retry on credential expiration
        max_retries = 2
        for attempt in range(max_retries):
            try:
                if self.provider == "bedrock":
                    # AWS Bedrock
                    response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.client.invoke_model(
                            modelId=self.model,
                            body=json.dumps({'inputText': text})
                        )
                    )
                    result = json.loads(response['body'].read())
                    return result.get('embedding', [])
                    
                elif self.provider == "openai":
                    # OpenAI
                    response = await self.client.embeddings.create(
                        model=self.model,
                        input=text
                    )
                    return response.data[0].embedding
                    
                elif self.provider == "azure":
                    # Azure OpenAI
                    response = await self.client.embeddings.create(
                        model=self.model,
                        input=text
                    )
                    return response.data[0].embedding
                    
                elif self.provider == "cohere":
                    # Cohere
                    response = await self.client.embed(
                        texts=[text],
                        model=self.model,
                        input_type="search_document"
                    )
                    return response.embeddings[0]
                    
                else:
                    raise ValueError(f"Unsupported provider: {self.provider}")
                    
            except ClientError as e:
                # Handle AWS credential expiration
                error_code = e.response.get('Error', {}).get('Code', '')
                
                if error_code == 'ExpiredTokenException' and attempt < max_retries - 1:
                    logger.warning(
                        "EmbeddingService: AWS token expired, refreshing client (attempt %d/%d)",
                        attempt + 1, max_retries
                    )
                    self._client = None  # Force client recreation with fresh credentials
                    continue
                    
                raise
            except Exception as e:
                # For non-AWS providers, don't retry
                if self.provider != "bedrock":
                    raise
                # For AWS, only retry on credential errors
                raise
    
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        embeddings = []
        for text in texts:
            embedding = await self.generate_embedding(text)
            embeddings.append(embedding)
        return embeddings


class KnowledgeBaseService:
    """Service for knowledge base operations using pgvector."""
    
    def __init__(self, embedding_service: Optional[EmbeddingService] = None):
        """Initialize knowledge base service.
        
        Args:
            embedding_service: Embedding service for vector generation
        """
        self._embedding_service = embedding_service
    
    async def _get_embedding_service(self) -> EmbeddingService:
        """Get or create embedding service based on LLM config marked for embeddings.
        
        Returns:
            EmbeddingService instance
        """
        if self._embedding_service is not None:
            return self._embedding_service
        
        # Try to find an LLM marked for embeddings
        try:
            from app.repositories.db_repository import db_repository
            llm_configs = await db_repository.list_llm_configs()
            
            for name, config in llm_configs.items():
                if config.get('use_for_embeddings'):
                    # Found an LLM marked for embeddings
                    logger.info(
                        "Using LLM '%s' (%s/%s) for embeddings",
                        name, config.get('provider'), config.get('model')
                    )
                    
                    # Normalize provider name for embedding service
                    provider = config.get('provider', '').lower().replace(' ', '_')
                    # Map common variations to standard names
                    provider_map = {
                        'aws_bedrock': 'bedrock',
                        'bedrock': 'bedrock',
                        'aws': 'bedrock',
                        'openai': 'openai',
                        'anthropic': 'openai',  # Anthropic uses OpenAI-compatible API
                        'azure_openai': 'azure',
                        'azure': 'azure',
                        'cohere': 'cohere',
                    }
                    normalized_provider = provider_map.get(provider, provider)
                    
                    # Get credentials from model_keys
                    api_key = None
                    access_key_id = None
                    secret_access_key = None
                    session_token = None
                    
                    # For Bedrock, get AWS credentials from Model Keys (same as workflow LLMs)
                    if normalized_provider == 'bedrock':
                        try:
                            # Try multiple key names for Bedrock
                            for bedrock_key in ("AWS Bedrock", "bedrock", "aws bedrock", "aws"):
                                mk = await db_repository.get_model_key(bedrock_key, include_secrets=True)
                                if mk:
                                    if mk.get("access_key_id"):
                                        access_key_id = mk["access_key_id"]
                                    if mk.get("secret_access_key"):
                                        secret_access_key = mk["secret_access_key"]
                                    if mk.get("session_token"):
                                        session_token = mk["session_token"]
                                    if mk.get("region") and (not config.get("region") or config.get("region") == "us-east-1"):
                                        config["region"] = mk["region"]
                                    logger.info(
                                        "Using AWS credentials from Model Key '%s' for embeddings",
                                        bedrock_key
                                    )
                                    break
                        except Exception as e:
                            logger.warning("Could not look up Model Key for Bedrock: %s", e)
                    
                    # For other providers, get API key
                    elif normalized_provider not in ('ollama',):
                        try:
                            # Use original provider name for model_key lookup
                            mk = await db_repository.get_model_key(config.get('provider'), include_secrets=True)
                            if mk:
                                api_key = mk.get('api_key')
                        except Exception:
                            pass
                    
                    self._embedding_service = EmbeddingService(
                        provider=normalized_provider,
                        model=config.get('model'),
                        region=config.get('region'),
                        api_key=api_key,
                        base_url=config.get('base_url') or config.get('endpoint'),
                        access_key_id=access_key_id,
                        secret_access_key=secret_access_key,
                        session_token=session_token,
                    )
                    return self._embedding_service
        except Exception as e:
            logger.warning("Failed to load LLM config for embeddings: %s", e)
        
        # Fall back to default settings
        logger.info("Using default embedding configuration from settings")
        self._embedding_service = EmbeddingService()
        return self._embedding_service
    
    @property
    async def embedding_service(self) -> EmbeddingService:
        """Get embedding service (async property)."""
        return await self._get_embedding_service()
    
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
        embedding_svc = await self._get_embedding_service()
        embedding = await embedding_svc.generate_embedding(f"{name} {pattern} {description or ''}")
        
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
        embedding_svc = await self._get_embedding_service()
        query_embedding = await embedding_svc.generate_embedding(query)
        
        async with AsyncSessionLocal() as session:
            # Use pgvector cosine similarity search
            # Note: This requires the pgvector extension
            from sqlalchemy import text
            
            # Convert embedding list to pgvector format string
            embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
            
            query_str = """
                SELECT id, name, pattern, pattern_type, severity, description,
                       1 - (embedding <=> $1::vector) as similarity
                FROM log_patterns
                WHERE 1 - (embedding <=> $1::vector) > $2
                ORDER BY similarity DESC
                LIMIT $3
            """
            
            result = await session.execute(text(query_str), (embedding_str, threshold, limit))
            
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
    # KNOWN ISSUE OPERATIONS
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
        embedding_svc = await self._get_embedding_service()
        embedding = await embedding_svc.generate_embedding(combined_text)
        
        async with AsyncSessionLocal() as session:
            issue_model = KnownIssueModel(
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
        embedding_svc = await self._get_embedding_service()
        embedding = await embedding_svc.generate_embedding(combined_text)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(KnownIssueModel).where(
                    KnownIssueModel.title == title,
                    KnownIssueModel.category == category,
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
                issue_model = KnownIssueModel(
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
            issue_id: Primary key of the KnownIssueModel to update.
            new_solution: Replacement solution text.
            source: Provenance of the update.

        Returns:
            Dict with id, title, and source, or an error key if not found.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(KnownIssueModel).where(KnownIssueModel.id == issue_id)
            )
            existing = result.scalar_one_or_none()

            if not existing:
                return {"error": f"KnownIssue id={issue_id} not found"}

            existing.solution = new_solution
            existing.source = source
            existing.updated_at = datetime.now(timezone.utc)

            # Regenerate embedding to reflect updated solution content.
            combined_text = f"{existing.title} {' '.join(existing.symptoms or [])} {new_solution}"
            embedding_svc = await self._get_embedding_service()
            existing.embedding = await embedding_svc.generate_embedding(combined_text)

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
        embedding_svc = await self._get_embedding_service()
        query_embedding = await embedding_svc.generate_embedding(query)
        
        async with AsyncSessionLocal() as session:
            from sqlalchemy import text
            
            # Convert embedding list to pgvector format string
            embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
            
            if category:
                query_str = """
                    SELECT id, title, description, symptoms, solution, category,
                           1 - (embedding <=> $1::vector) as similarity
                    FROM known_issues
                    WHERE 1 - (embedding <=> $1::vector) > $2
                    AND category = $3
                    ORDER BY similarity DESC
                    LIMIT $4
                """
                result = await session.execute(text(query_str), (embedding_str, threshold, category, limit))
            else:
                query_str = """
                    SELECT id, title, description, symptoms, solution, category,
                           1 - (embedding <=> $1::vector) as similarity
                    FROM known_issues
                    WHERE 1 - (embedding <=> $1::vector) > $2
                    ORDER BY similarity DESC
                    LIMIT $3
                """
                result = await session.execute(text(query_str), (embedding_str, threshold, limit))
            
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
# Note: embedding_service will be lazily initialized based on LLM config marked for embeddings
embedding_service = None
knowledge_base = KnowledgeBaseService(embedding_service)