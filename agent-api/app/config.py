"""Application configuration."""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def parse_env_bool(value: Any, *, default: bool = True) -> bool:
    """Parse common truthy/falsey environment string values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("false", "0", "no", "off")


# ─────────────────────────────────────────────────────────────────────────────
# AWS Secrets Manager loader
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def get_secret(key: str, region: str = "us-east-1") -> Optional[str]:
    """Retrieve a secret value from AWS Secrets Manager, with env-var fallback.

    In development (or when ``AWS_SECRETS_MANAGER_ENABLED`` is not 'true'),
    the env var matching *key* is returned directly — no AWS call is made.

    The result is cached per *(key, region)* for the lifetime of the process.
    Call ``get_secret.cache_clear()`` in tests to reset.

    Args:
        key: Secret name / ARN in Secrets Manager.  Also used as the env-var
             name looked up in the fallback path.
        region: AWS region where the secret is stored.

    Returns:
        The secret string, or ``None`` if not found anywhere.
    """
    if not settings.aws_secrets_manager_enabled:
        value = os.getenv(key)
        if value is None:
            logger.debug("get_secret: env var '%s' not set (dev mode)", key)
        return value

    try:
        import boto3  # type: ignore
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=key)
        secret = response.get("SecretString") or response.get("SecretBinary")
        if isinstance(secret, bytes):
            secret = secret.decode("utf-8")
        # If the secret is a JSON blob, callers get the raw JSON string —
        # use get_secret_json() below to parse it.
        return secret
    except Exception as exc:
        logger.warning("get_secret: failed to retrieve '%s': %s", key, exc)
        return os.getenv(key)  # final fallback to env var


def get_secret_json(key: str, region: str = "us-east-1") -> Optional[dict]:
    """Like :func:`get_secret` but JSON-parses the result.

    Returns ``None`` when the secret is absent or not valid JSON.
    """
    raw = get_secret(key, region)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("get_secret_json: secret '%s' is not valid JSON", key)
        return None


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )
    
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"
    
    # Database
    database_url: str = Field(
        default="postgresql://kycuser:kycpassword@localhost:5432/kycagent",
        description="PostgreSQL database URL"
    )
    db_pool_size: int = Field(
        default=20,
        description="SQLAlchemy async engine pool_size. Sized for parallel DAG node execution.",
    )
    db_max_overflow: int = Field(
        default=40,
        description="SQLAlchemy async engine max_overflow above pool_size.",
    )
    
    # CORS settings - configurable for production
    cors_origins: List[str] = Field(
        default=["*"],
        description="Allowed CORS origins. Set to specific domains in production."
    )
    
    # Storage paths (kept for backward compatibility, but database is preferred)
    storage_path: str = "data/storage"
    workflow_dir: str = "data/workflows"
    logs_dir: str = "data/logs"

    # Crawler / code analyzer
    repos_base_path: str = Field(
        default="/tmp/indexed_repos",
        validation_alias="REPOS_BASE_PATH",
    )
    code_analyzer_list_cache_ttl_seconds: float = 60.0
    code_analyzer_search_concurrency: int = 3
    background_index_concurrency: int = 2
    index_parse_concurrency: int = 4
    index_file_read_concurrency: int = 8
    # Bounded DB schema lookup tools (db_list_tables / db_describe_table / db_search_columns)
    db_schema_cache_ttl_seconds: float = 300.0
    db_schema_max_tables: int = 2000      # complete-catalog fetch cap (names are cheap)
    db_schema_output_max_rows: int = 200  # max names/columns returned per tool call
    db_schema_max_columns: int = 300

    # AWS
    aws_ssl_verify: bool = True
    aws_ca_bundle: Optional[str] = None
    aws_region: str = "us-east-1"
    bedrock_region: Optional[str] = None
    aws_profile: Optional[str] = None

    # Heartbeat monitor
    heartbeat_seen_ttl_hours: int = 24
    heartbeat_poll_interval: int = 60
    heartbeat_cooldown: int = 300
    heartbeat_max_concurrent: int = 3
    heartbeat_aws_region: str = "us-east-1"
    heartbeat_aws_profile: Optional[str] = None
    heartbeat_db_fallback: bool = True

    # SSE / streaming
    sse_stream_timeout_seconds: int = 600
    sse_heartbeat_interval_seconds: int = 15
    sse_queue_maxsize: int = 1000
    log_watch_sse_dedup_max: int = 10000

    # Thread pools
    aws_thread_pool_size: int = 16

    # Parallel flow / map-reduce
    parallel_flow_concurrency: int = 5
    parallel_flow_item_timeout: float = 180.0
    parallel_flow_batch_timeout: float = 0.0
    parallel_flow_fail_fast: bool = False
    parallel_flow_min_success_rate: float = Field(default=0.5, validation_alias="MAP_REDUCE_MIN_SUCCESS_RATE")

    # Runtime retention / concurrency
    max_runtime_events: int = 500
    embedding_concurrency: int = 4
    code_correlation_concurrency: int = 5

    # Agent / tools / output limits
    provider_transport: str = "anthropic"
    crawler_model: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    # Model tiering for crawler LLM flows (see app/crawler/call_llm.py).
    # Background indexing (ExtractAbstractions / AnalyzeRelationships) is the most
    # tolerant of a smaller model. Opt-in only: when set, indexing uses this
    # model (e.g. a Haiku inference profile) instead of the DB-configured agent
    # model. Leave unset to keep the DB model — set it only after confirming the
    # model/inference-profile is enabled in your Bedrock account, since an
    # invalid ID fails the indexing LLM call. Override via CRAWLER_INDEX_MODEL.
    crawler_index_model: Optional[str] = Field(
        default=None, validation_alias="CRAWLER_INDEX_MODEL"
    )
    # Opt-in override for the *search* crawler flows (semantic/find/trace/
    # investigate). When set, these flows use this model instead of the
    # DB-configured agent model — pricier flows can be moved to a cheaper tier
    # once the accuracy harness confirms recall holds. None = keep DB model.
    crawler_model_override: Optional[str] = Field(
        default=None, validation_alias="CRAWLER_MODEL_OVERRIDE"
    )
    # Total context-char cap for the search crawler flows' LLM prompt. The KG
    # fast path (QueryKGForHits / QueryKGForSymbol) short-circuits most queries;
    # the LLM path is the fallback, so a tighter cap halves its dominant token
    # cost with minimal recall risk. Override via CRAWLER_SEARCH_CONTEXT_MAX_CHARS.
    crawler_search_context_max_chars: int = Field(
        default=80_000, validation_alias="CRAWLER_SEARCH_CONTEXT_MAX_CHARS"
    )
    # Max chars of raw alert text sent to the investigateAlertFlow ParseAlert
    # node — defensive cap so a pathological alert can't blow the prompt budget.
    crawler_alert_max_chars: int = Field(
        default=10_000, validation_alias="CRAWLER_ALERT_MAX_CHARS"
    )
    # Snippet extraction for the search crawler flows: instead of sending whole
    # file bodies to the LLM, send line-numbered windows around lexical matches
    # of the query terms (±crawler_snippet_window lines). Cuts per-file tokens
    # 5–10× while keeping the lines the model needs to cite. Disable to fall back
    # to full-file truncation if recall regresses. Override via env.
    crawler_snippet_extraction: bool = Field(
        default=True, validation_alias="CRAWLER_SNIPPET_EXTRACTION"
    )
    crawler_snippet_window: int = Field(
        default=40, validation_alias="CRAWLER_SNIPPET_WINDOW"
    )
    # ReAct loop bound. LangGraph counts a "step" as one node transition; each
    # ReAct iteration is ~2 steps (agent + tool node), so 25 ≈ 12 iterations.
    # Raised from 12 because code investigations legitimately need more hops
    # (find → paginate get_body → trace → synthesize); at 12 the agent was hit
    # the limit mid-investigation and returned a "Let me search…" preamble.
    # Override via AGENT_RECURSION_LIMIT.
    agent_recursion_limit: int = Field(default=25, validation_alias="AGENT_RECURSION_LIMIT")
    # Default per-turn output-token cap for agent/workflow LLM calls. 4096 was
    # too small: the model could exhaust its budget mid-reasoning (right before
    # emitting a tool_use), get cut off with stopReason="max_tokens", and have
    # that truncated half-thought returned as the final answer. 8192 leaves room
    # for reasoning + a tool call in one turn. Override via AGENT_MAX_OUTPUT_TOKENS.
    agent_max_output_tokens: int = Field(default=8192, validation_alias="AGENT_MAX_OUTPUT_TOKENS")
    code_analyzer_output_max_chars: int = 8000
    mcp_tool_output_max_chars: int = 8000
    # Universal safety-net ceiling for ANY single tool result that lacks its own
    # cap (db/edit/playbook StructuredTools). Larger than the per-family 8 KB
    # caps so it only catches truly unbounded outputs; matches claude-code's
    # DEFAULT_MAX_RESULT_SIZE_CHARS. Set to 0 to disable. Override via env.
    tool_output_max_chars: int = Field(default=50000, validation_alias="TOOL_OUTPUT_MAX_CHARS")
    skill_min_tool_calls: int = 3
    guardrail_hard_stop: bool = False
    cloudwatch_auto_drilldown: bool = True
    cloudwatch_metrics_fusion: bool = True
    # When True, the deterministic cloudwatchAnalyzer node auto-escalates to a
    # contained agent investigation when its triage signals warrant it (high/
    # critical severity, low confidence, or a high-severity alert). Set False to
    # keep the analyzer purely deterministic regardless of node analysis_depth.
    cloudwatch_auto_escalate: bool = True
    # Staged investigation pipeline (run_investigation_pipeline): how many top
    # findings to drill into during Stage 2, and whether the heaviest stage
    # (cross-group correlation) may run. Tunable without a redeploy.
    cloudwatch_pipeline_drilldown_top_n: int = 5
    cloudwatch_pipeline_enable_correlation: bool = True
    # RCA data-fidelity caps (this is an internal root-cause tool — keep enough
    # raw detail that correlation/profile/trace IDs and stack traces survive).
    # Tunable per environment without a redeploy.
    cloudwatch_synthesis_max_chars: int = 24_000   # LLM synthesis payload size
    cloudwatch_example_msg_chars: int = 800        # per-pattern example message
    cloudwatch_drill_sample_chars: int = 1500      # per drill-down sample sent to LLM
    # Pipeline LLM synthesis control. The synthesis is a single Bedrock call that
    # produces the narrative; when an account-level Bedrock guardrail blocks it,
    # it wastes ~24s + tokens every run. Set False to disable entirely; otherwise
    # a circuit breaker skips it after N consecutive guardrail refusals.
    cloudwatch_pipeline_llm_synthesis: bool = True
    cloudwatch_synthesis_guardrail_cooldown: int = 3   # consecutive refusals before skip

    # Supervisor
    supervisor_max_retries: int = 1
    supervisor_pass_threshold: float = 0.60
    supervisor_hitl_threshold: float = 0.50
    supervisor_hitl_enabled: bool = True
    supervisor_llm_scoring: bool = False
    supervisor_token_budget: int = 100_000
    # Defensive wall-clock ceiling for the supervisor retry loop (seconds).
    # Independent of max_retries/token_budget — guarantees the loop terminates.
    supervisor_wall_clock_seconds: float = 900.0

    # Semantic router
    router_query_max_chars: int = 2000
    router_classify_max_output: int = 32

    # Azure DevOps
    azure_config_path: str = "data/config/azure_devops.json"
    azure_devops_encryption_key: Optional[str] = None

    # Telemetry
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: Optional[str] = None
    aws_secrets_manager_enabled: bool = False
    
    # Scheduler settings
    scheduler_timezone: str = "UTC"
    max_concurrent_workflows: int = 5

    @field_validator(
        "aws_ssl_verify",
        "heartbeat_db_fallback",
        "parallel_flow_fail_fast",
        "guardrail_hard_stop",
        "supervisor_hitl_enabled",
        "supervisor_llm_scoring",
        "cloudwatch_auto_drilldown",
        "cloudwatch_metrics_fusion",
        "otel_enabled",
        "aws_secrets_manager_enabled",
        mode="before",
    )
    @classmethod
    def _parse_bool_fields(cls, value: Any) -> bool:
        return parse_env_bool(value)

    @property
    def async_database_url(self) -> str:
        """Get async database URL for asyncpg."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://")

    @property
    def effective_bedrock_region(self) -> str:
        return self.bedrock_region or self.aws_region


settings = Settings()
