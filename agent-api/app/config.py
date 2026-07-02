"""Application configuration."""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, List, Optional

from pydantic import Field, field_validator, model_validator
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

    # ── Runtime profile (lightweight switch) ─────────────────────────────────
    # APP_PROFILE=lite flips heavy features off by default (semantic memory,
    # sandbox, CloudWatch auto-escalation/drilldown, startup indexing recovery)
    # for a minimal footprint. Any explicit env var always wins. 'full' = today.
    app_profile: str = Field(default="full", validation_alias="APP_PROFILE")
    # Re-fire interrupted repo-indexing jobs on startup (crawler). Off under lite.
    startup_indexing_recovery_enabled: bool = Field(
        default=True, validation_alias="STARTUP_INDEXING_RECOVERY_ENABLED"
    )
    
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
    # codegraph — the native C code-intelligence engine baked into the image. Used
    # as an alternative Code-Crawler-node backend, driven in-process over stdio
    # (codegraph serve) via an inline MCP config — NOT registered as a platform
    # MCP server. Path is configurable for non-container / test hosts.
    codegraph_bin: str = Field(
        default="/usr/local/bin/codegraph",
        validation_alias="CODEGRAPH_BIN",
    )
    # v0.10.0+ of the codegraph engine stores one SQLite DB per project under a
    # cache directory (default ~/.cache/codegraph). Admin reads scan this dir.
    # Override via CODEGRAPH_CACHE_DIR.
    codegraph_cache_dir: str = Field(
        default=os.path.expanduser("~/.cache/codegraph"),
        validation_alias="CODEGRAPH_CACHE_DIR",
    )
    # Legacy single-DB path (used by v0.1.x); kept for reference only — no
    # longer read by admin code. Override via CODEGRAPH_STORE_DB.
    codegraph_db_path: str = Field(
        default="/tmp/codegraph.db",
        validation_alias="CODEGRAPH_STORE_DB",
    )
    # Per-call timeout (seconds) for codegraph's `search_code` tool only. It shells
    # out to grep over source files (the one non-graph, potentially slow tool); the
    # fast graph tools inherit the default MCP tool timeout. Capping it stops a
    # single broad grep from exhausting an agent run's time/iteration budget — the
    # agent gets a recoverable tool error and falls back to the graph tools. A
    # timeout no longer forces an MCP reconnect (the session survives it safely —
    # see mcp_client_manager.execute_tool), so this only needs to be generous
    # enough that a normal grep over a large repo (e.g. the 9GB compliance-api)
    # rarely trips it; 120s (matches the container's CODEGRAPH_SEARCH_TIMEOUT env).
    codegraph_search_timeout_seconds: float = Field(
        default=120.0,
        validation_alias="CODEGRAPH_SEARCH_TIMEOUT",
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
    # Bedrock embedding model + output dimension. Titan Text Embeddings V2
    # (amazon.titan-embed-text-v2:0) supports 256/512/1024 dims (1024 best
    # quality) and is the account-enabled model; V1 (titan-embed-text-v1, 1536)
    # is not enabled here. All embedding columns are vector(1024) (migration 019).
    embedding_model_id: str = Field(
        default="amazon.titan-embed-text-v2:0", validation_alias="EMBEDDING_MODEL_ID"
    )
    embedding_dimensions: int = Field(
        default=1024, validation_alias="EMBEDDING_DIMENSIONS"
    )
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
    # Which ReAct loop engine drives an agent run. "langgraph" (default) keeps
    # today's langgraph.prebuilt.create_react_agent behavior untouched. "native"
    # opts into the hand-rolled turn loop (app.harness.engine) being built out
    # incrementally — gated on the accuracy eval before it becomes the default.
    # Per-workflow override: agent_config["engine"] / params["engine"].
    agent_engine: str = Field(default="langgraph", validation_alias="AGENT_ENGINE")
    # Non-streaming agent invocation wall-clock cap (agent_runner.invoke_agent).
    # Used by the workflow-executor's non-streaming fallback and by subagent
    # delegation (subagent_factory._run_child calls execute_agent with no
    # stream_callback). Previously hardcoded to 300s with no override, which
    # could silently cut a run short even when a caller's OWN wait_for wrapped
    # it with a higher timeout (e.g. DELEGATION_CHILD_TIMEOUT_SECONDS > 300).
    agent_invoke_timeout_seconds: float = Field(
        default=300.0, validation_alias="AGENT_INVOKE_TIMEOUT_SECONDS"
    )
    # Bedrock client read timeout (seconds). Botocore's 60s default is too short
    # for large-context synthesis calls (200K+ tokens, including the forced
    # recovery synthesis after a recursion-limit hit), which raised
    # "Read timeout on endpoint URL …/converse" and failed the run.
    # Override via BEDROCK_READ_TIMEOUT_SECONDS.
    bedrock_read_timeout_seconds: int = Field(default=300, validation_alias="BEDROCK_READ_TIMEOUT_SECONDS")
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
    # Confidence-gated distillation: a distilled skill scoring
    # below this is saved as 'draft' (hidden from recall) until audited/proven.
    skill_confidence_min: float = Field(
        default=0.6, validation_alias="SKILL_CONFIDENCE_MIN"
    )
    # A draft skill with at least this many successful executions is auto-promoted
    # to 'active' by the curator audit (proven-by-use).
    skill_promote_success_count: int = Field(
        default=2, validation_alias="SKILL_PROMOTE_SUCCESS_COUNT"
    )
    guardrail_hard_stop: bool = False

    # ── Bedrock fallback-chain routing ───────────────────────────────────────
    # On a Bedrock ThrottlingException the runner walks a fallback chain
    # (alt credential → alt region → fallback model) instead of hammering the
    # same throttled target. Set False to restore the old single-target retry.
    routing_fallback_enabled: bool = Field(
        default=True, validation_alias="ROUTING_FALLBACK_ENABLED"
    )
    # Concrete AWS regions to fail over to, in order. Each must host the
    # cross-region inference profile for the model; build_llm derives the
    # us./eu./ap. prefix from the region. Comma-separated via env.
    # Defaults to US regions only — most deployments' Bedrock credentials are
    # US-scoped, and same-geography inference profiles avoid cross-region auth
    # failures. Add eu-*/ap-* via env when the credentials have that access.
    bedrock_fallback_regions: List[str] = Field(
        default_factory=lambda: ["us-east-1", "us-west-2"],
        validation_alias="BEDROCK_FALLBACK_REGIONS",
    )
    # Bedrock model IDs to fall back to (e.g. Sonnet → Haiku) after region
    # failover is exhausted. Bare foundation-model IDs; the inference-profile
    # prefix is added per region. Comma-separated via env.
    bedrock_model_fallback: List[str] = Field(
        default_factory=lambda: ["anthropic.claude-haiku-4-5-20251001-v1:0"],
        validation_alias="BEDROCK_MODEL_FALLBACK",
    )

    # ── Bank-scoped semantic memory ──────────────────────────────────────────
    # Learned, operational memory: auto-captured investigation findings recalled
    # (hybrid FTS+vector) before each run, scoped per-repo + a shared global bank.
    # Opt-in (default off) so it is a no-op until an operator enables it.
    semantic_memory_enabled: bool = Field(
        default=False, validation_alias="SEMANTIC_MEMORY_ENABLED"
    )
    # Retrieval mode: 'hybrid' (FTS+vector, best recall/lowest tokens), 'fts'
    # (no embeddings — zero embed calls), or 'vector' (pure semantic).
    memory_retrieval_mode: str = Field(
        default="hybrid", validation_alias="MEMORY_RETRIEVAL_MODE"
    )
    # Token-budget guardrails (keep recall net-positive, not a per-turn leak):
    memory_recall_k: int = Field(default=4, validation_alias="MEMORY_RECALL_K")
    # Cosine threshold for the vector recall leg. Tuned for Titan V2, whose
    # normalized embeddings score related text ~0.4–0.5 (lower than V1) — 0.6
    # filtered everything. Hybrid RRF + the k-cap keep precision.
    memory_recall_min_score: float = Field(
        default=0.35, validation_alias="MEMORY_RECALL_MIN_SCORE"
    )
    memory_max_chars: int = Field(default=600, validation_alias="MEMORY_MAX_CHARS")
    # Only auto-capture an investigation finding when confidence is at least this
    # (avoids storing low-value/uncertain results). 0 captures everything.
    memory_capture_min_confidence: float = Field(
        default=0.6, validation_alias="MEMORY_CAPTURE_MIN_CONFIDENCE"
    )

    # ── Tool-output compression ──────────────────────────────────────────────
    # Replaces lossy char-truncation with type-aware reversible compression for
    # large MCP tool outputs. Compression runs in a local sidecar (no LLM call).
    # Opt-in (default off); on timeout/error falls back to existing truncation.
    compression_enabled: bool = Field(
        default=False, validation_alias="COMPRESSION_ENABLED"
    )
    compression_endpoint: str = Field(
        default="http://headroom:8787", validation_alias="COMPRESSION_ENDPOINT"
    )
    # Only compress outputs larger than this; smaller ones go straight to truncation.
    compression_min_chars: int = Field(
        default=2000, validation_alias="COMPRESSION_MIN_CHARS"
    )
    # Hard timeout for a single compress call (ms). On expiry, fall back to truncation.
    compression_timeout_ms: int = Field(
        default=250, validation_alias="COMPRESSION_TIMEOUT_MS"
    )

    # ── Per-turn durable-fact extraction + audit loop ────────────────────────
    # After each turn, extract a few DURABLE operational/session facts and store
    # them in semantic_memory (conservative caps: a couple short facts per turn).
    # Opt-in (default off) — a no-op until enabled. Reuses the existing store and
    # its sha256 dedup, so it never duplicates infra.
    memory_fact_extraction_enabled: bool = Field(
        default=False, validation_alias="MEMORY_FACT_EXTRACTION_ENABLED"
    )
    memory_fact_max_per_turn: int = Field(
        default=2, validation_alias="MEMORY_FACT_MAX_PER_TURN"
    )
    # The audit/consolidation pass (run by the curator) merges memories that
    # state the same fact in different words. A SHA256 fingerprint of the store
    # short-circuits the LLM call when nothing changed; a hard guard refuses any
    # pass that would delete more than this fraction of memories.
    memory_audit_enabled: bool = Field(
        default=False, validation_alias="MEMORY_AUDIT_ENABLED"
    )
    memory_audit_max_delete_fraction: float = Field(
        default=0.5, validation_alias="MEMORY_AUDIT_MAX_DELETE_FRACTION"
    )

    # ── PII pseudonymization (privacy boundary before Bedrock) ───────────────
    # Detect PII in everything bound for the model and swap in stable, reversible
    # placeholders ([EMAIL_1] …); the final answer is re-hydrated for the user.
    # ON by default — this is a compliance control for a KYC product. Set the
    # flag False to disable, or trim the entity set, without a redeploy.
    pii_pseudonymization_enabled: bool = Field(
        default=True, validation_alias="PII_PSEUDONYMIZATION_ENABLED"
    )
    pii_entity_types: List[str] = Field(
        default_factory=lambda: ["EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IP", "ACCOUNT_ID"],
        validation_alias="PII_ENTITY_TYPES",
    )
    # NER-based PERSON/ADDRESS detection — off until validated (regex-only first).
    pii_person_detection: bool = Field(
        default=False, validation_alias="PII_PERSON_DETECTION"
    )

    # ── Plan → execute → verify agent loop ───────────────────────────────────
    # When True, multi-step agents are instructed to draft a markdown task list,
    # work it item-by-item, and close with a Verification section. Gated to
    # multi-step mode so trivial single-tool runs are not bloated.
    agent_planning_enabled: bool = Field(
        default=True, validation_alias="AGENT_PLANNING_ENABLED"
    )

    # ── RAG-selected skills ──────────────────────────────────────────────────
    # Auto-select relevant skills per query (reusing semantic recall) instead of
    # requiring an explicit /slash-command invocation.
    skill_rag_selection_enabled: bool = Field(
        default=True, validation_alias="SKILL_RAG_SELECTION_ENABLED"
    )
    skill_rag_k: int = Field(default=2, validation_alias="SKILL_RAG_K")

    # ── Skill storage (file-based; no DB) ────────────────────────────────────
    # Executable skills (distilled + user-created) are stored as one JSON file
    # per skill here. Markdown guidance skills (SkillManager) live under skills_dir.
    skills_store_dir: str = Field(
        default="data/skills_store", validation_alias="SKILLS_STORE_DIR"
    )
    skills_dir: str = Field(default="data/skills", validation_alias="SKILLS_DIR")

    # ── Delegation (multi-agent) ─────────────────────────────────────────────
    # Bounds for the orchestrator→specialist delegation layer.  All per-node /
    # per-profile overrides layer on top via spec_factory.resolve_profile_fields.
    delegation_max_depth: int = Field(
        default=1, validation_alias="DELEGATION_MAX_DEPTH"
    )
    delegation_max_concurrent: int = Field(
        default=3, validation_alias="DELEGATION_MAX_CONCURRENT"
    )
    delegation_child_timeout_seconds: float = Field(
        default=180.0, validation_alias="DELEGATION_CHILD_TIMEOUT_SECONDS"
    )
    delegation_output_max_chars: int = Field(
        default=8000, validation_alias="DELEGATION_OUTPUT_MAX_CHARS"
    )
    # CSV of fnmatch patterns always stripped from child tool sets.
    # Children are investigate/read-only by default; orchestrator owns mutations.
    delegation_blocked_tools: str = Field(
        default="delegate_*,apply_fix,edit_file,fs_write*,run_command,write_todos,send_*,wiki_*",
        validation_alias="DELEGATION_BLOCKED_TOOLS",
    )

    # ── Self-improvement (hill-climbing) loop ────────────────────────────────
    # OFF by default. When enabled, an on-demand analyzer samples recent execution
    # traces and proposes prompt/tool/skill refinements as DRAFTS for operator
    # approval — it never auto-applies anything.
    self_improvement_enabled: bool = Field(
        default=False, validation_alias="SELF_IMPROVEMENT_ENABLED"
    )
    self_improvement_sample: int = Field(
        default=30, validation_alias="SELF_IMPROVEMENT_SAMPLE"
    )

    # Loop 4 — hill-climbing auto-apply (OFF by default).
    # When True the scheduler job promotes safe proposals (skill drafts,
    # reliability notes) to active after passing the eval guardrail.
    # 'prompt' / 'policy' proposals are always left as human-reviewed drafts.
    hillclimb_apply_enabled: bool = Field(
        default=False, validation_alias="HILLCLIMB_APPLY_ENABLED"
    )

    # ── Pinned-facts memory tier ─────────────────────────────────────────────
    # An always-injected (not similarity-gated) memory tier on the existing
    # semantic store (bank='pinned'), with a per-turn token budget.
    pinned_facts_enabled: bool = Field(
        default=True, validation_alias="PINNED_FACTS_ENABLED"
    )
    memory_turn_token_budget: int = Field(
        default=800, validation_alias="MEMORY_TURN_TOKEN_BUDGET"
    )
    pinned_facts_max_tokens: int = Field(
        default=400, validation_alias="PINNED_FACTS_MAX_TOKENS"
    )
    pinned_promote_recall_threshold: int = Field(
        default=3, validation_alias="PINNED_PROMOTE_RECALL_THRESHOLD"
    )
    # ── Per-chat-session compaction ──────────────────────────────────────────
    # Chat.jsx replays the last ~12 messages every turn; each can carry a full
    # multi-KB investigation report. This budget is about keeping THAT replay
    # light (distinct from the model's full context window, which the
    # per-execution ContextCompactionManager already governs) — when a
    # session's stored messages exceed it, older turns collapse into one
    # system summary + a recent tail (session_repository.replace_with_summary).
    chat_session_compaction_tokens: int = Field(
        default=6_000, validation_alias="CHAT_SESSION_COMPACTION_TOKENS"
    )
    chat_session_keep_recent_tokens: int = Field(
        default=1_500, validation_alias="CHAT_SESSION_KEEP_RECENT_TOKENS"
    )
    # How often the scheduler runs the self-improvement curator (hours). The
    # cheap promotions run every cycle; LLM consolidation stays gated by
    # ``memory_audit_enabled``.
    curator_interval_hours: int = Field(
        default=6, validation_alias="CURATOR_INTERVAL_HOURS"
    )

    # ── Context references (@file / @folder / @url / @git in the query) ──────
    # When enabled, an inbound query is scanned for @-references and their
    # content is expanded inline. File access is restricted to
    # ``context_reference_root`` (defaults to the process CWD). No-op when the
    # message has no references.
    context_references_enabled: bool = Field(
        default=True, validation_alias="CONTEXT_REFERENCES_ENABLED"
    )
    context_reference_root: str = Field(
        default="", validation_alias="CONTEXT_REFERENCE_ROOT"
    )

    # ── Configurable agent persona / context document ───────────────────────
    # Optional operator-set persona (tone/role/standards) and a context doc,
    # prepended to the system prompt. Empty by default → prompt unchanged.
    agent_persona: str = Field(default="", validation_alias="AGENT_PERSONA")
    agent_context_doc: str = Field(default="", validation_alias="AGENT_CONTEXT_DOC")

    # ── Tool execution sandbox ───────────────────────────────────────────────
    # Isolate shell/code-execution tools. Backends:
    #   disabled  — no sandboxing (default; tools run in-process as before)
    #   auto      — pick the best available for the OS (container > bwrap > seatbelt)
    #   container — ephemeral Docker container (best for this Docker stack)
    #   bwrap     — Linux bubblewrap
    #   seatbelt  — macOS sandbox-exec
    sandbox_backend: str = Field(default="disabled", validation_alias="SANDBOX_BACKEND")
    sandbox_image: str = Field(default="python:3.12-slim", validation_alias="SANDBOX_IMAGE")
    sandbox_timeout_seconds: int = Field(default=60, validation_alias="SANDBOX_TIMEOUT_SECONDS")
    sandbox_memory: str = Field(default="512m", validation_alias="SANDBOX_MEMORY")
    sandbox_cpus: str = Field(default="1", validation_alias="SANDBOX_CPUS")
    sandbox_network: bool = Field(default=False, validation_alias="SANDBOX_NETWORK")
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
    # Reliability (Phase 1). Cap concurrent CloudWatch Logs Insights queries so a
    # wide fan-out (top_n drill-downs × log groups) can't trip StartQuery
    # concurrency limits. Reduced-scope retry gives a failed analyzer one more
    # chance over a halved window (transient errors only) before the run is
    # marked partial. Per-analyzer cache TTLs let cheap-to-stale data (alarms,
    # log-group discovery) cache longer than fast-moving error patterns.
    cloudwatch_insights_max_concurrency: int = 3
    cloudwatch_reduced_scope_retry: bool = True
    cloudwatch_cache_ttl_alarms: int = 300
    cloudwatch_cache_ttl_logs: int = 60
    # Accuracy (Phase 2). Deterministic drill-down scoring ranks candidates by
    # severity × volume/spike × evidence-grade instead of a flat z-score/count
    # sort. Alarm history surfaces flapping alarms (DescribeAlarmHistory).
    # Metrics discovery (ListMetrics) lets the agent find metrics for a log group
    # without hand-written MetricDataQuery dicts. Insights query fix-up auto-adds
    # a missing `| limit` clause instead of rejecting the query outright.
    cloudwatch_drilldown_scoring: bool = True
    cloudwatch_alarm_history: bool = True
    cloudwatch_metrics_discovery: bool = True
    cloudwatch_insights_query_fixup: bool = True
    # Cost & token controls (Phase 3). Every run aggregates bytes scanned by
    # Insights and estimates USD (insights_cost_per_gb). A soft per-run scan
    # budget degrades remaining drill-downs to a sampled window once exceeded
    # (recorded as budget_limited) rather than failing. A generous in-process
    # token-bucket rate limiter on StartQuery only bites under heavy fan-out.
    cloudwatch_max_gb_scanned_per_run: float = 5.0
    cloudwatch_insights_cost_per_gb: float = 0.005
    cloudwatch_ratelimit_startquery_rps: float = 5.0
    # Feature breadth (Phase 4). Metrics are a first-class analysis type (uses
    # ListMetrics discovery to build queries when none are hand-written). Time
    # ranges beyond 24h are split into sequential buckets (newest-first, early
    # stop once enough events are gathered). The hard cap defaults to 24h; raise
    # cloudwatch_max_time_range_minutes (up to 10080 = 7d) to enable longer
    # ranges — bucketing then keeps each Insights query within bucket_minutes.
    cloudwatch_max_time_range_minutes: int = 1440
    cloudwatch_bucket_minutes: int = 1440
    # Multi-region fan-out (Phase 5). When a node lists more than one region the
    # investigation runs per-region triage (bounded by this cap), namespaces all
    # findings as "region:log_group", and merges into one evidence bundle for a
    # single synthesis. One bad region degrades to partial coverage, not a failed
    # run. Cross-account (assume-role) is intentionally out of scope here.
    cloudwatch_max_regions_per_run: int = 3

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
        "sandbox_network",
        "routing_fallback_enabled",
        "semantic_memory_enabled",
        "memory_fact_extraction_enabled",
        "memory_audit_enabled",
        "self_improvement_enabled",
        "hillclimb_apply_enabled",
        mode="before",
    )
    @classmethod
    def _parse_bool_fields(cls, value: Any) -> bool:
        return parse_env_bool(value)

    @field_validator(
        "bedrock_fallback_regions",
        "bedrock_model_fallback",
        mode="before",
    )
    @classmethod
    def _parse_csv_list(cls, value: Any) -> Any:
        """Accept a comma-separated string (env) or a real list (default)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _apply_lite_profile_defaults(self) -> "Settings":
        """When APP_PROFILE=lite, flip heavy features off — unless explicitly set.

        Only fields the operator did NOT provide (via env/.env, tracked by
        ``model_fields_set``) are changed, so an explicit override always wins.
        'full' (default) preserves today's behaviour exactly. No code is removed —
        this only changes defaults for a minimal-footprint runtime.
        """
        if (self.app_profile or "full").lower() != "lite":
            return self
        lite_defaults = {
            "semantic_memory_enabled": False,
            "sandbox_backend": "disabled",
            "cloudwatch_auto_escalate": False,
            "cloudwatch_auto_drilldown": False,
            "startup_indexing_recovery_enabled": False,
            "compression_enabled": False,
        }
        for name, value in lite_defaults.items():
            if name not in self.model_fields_set:
                setattr(self, name, value)
        return self

    @property
    def async_database_url(self) -> str:
        """Get async database URL for asyncpg."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://")

    @property
    def effective_bedrock_region(self) -> str:
        return self.bedrock_region or self.aws_region


settings = Settings()
