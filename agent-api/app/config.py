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


# CSV of fnmatch patterns always stripped from delegated child tool sets. Shared
# with app.harness.subagent_factory so its no-settings fallback (DB-free tests)
# can't drift from this default. Children are investigate/read-only by default;
# the parent orchestrator owns all mutations — fs_write*/fs_append/fs_upsert/
# fs_prune together cover every VFS mutation (metamemory files included).
DEFAULT_DELEGATION_BLOCKED_TOOLS = (
    "delegate_*,apply_fix,edit_file,fs_write*,fs_append,fs_upsert,fs_prune,"
    "run_command,write_todos,send_*,wiki_*"
)


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
    # Uncommon default for local (non-Docker) runs so it doesn't clash with other
    # services on 8000. Docker overrides this via API_PORT=8000 in compose.
    api_port: int = 48000
    api_reload: bool = False
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"

    # ── Runtime profile (lightweight switch) ─────────────────────────────────
    # APP_PROFILE=lite flips heavy features off by default (semantic memory,
    # sandbox, CloudWatch auto-escalation/drilldown, startup indexing recovery)
    # for a minimal footprint. Any explicit env var always wins. 'full' = today.
    app_profile: str = Field(default="full", validation_alias="APP_PROFILE")
    # Re-fire interrupted codegraph repo-indexing jobs on startup. Off under lite.
    startup_indexing_recovery_enabled: bool = Field(
        default=True, validation_alias="STARTUP_INDEXING_RECOVERY_ENABLED"
    )
    
    # Database
    database_url: str = Field(
        # localhost:45432 matches the uncommon host port the compose postgres
        # service publishes. Docker overrides this via DATABASE_URL in compose.
        default="postgresql://kycuser:kycpassword@localhost:45432/kycagent",
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

    # ── API-key authentication (opt-in) ──────────────────────────────────────
    # OFF by default — workflow execute, MCP CRUD, improvement apply, and
    # crawler investigate are otherwise unauthenticated, suitable only for an
    # internal/trusted network. Set both to require a key on every route
    # except health/docs/root (see app.api.middleware.api_auth).
    api_auth_enabled: bool = Field(
        default=False, validation_alias="API_AUTH_ENABLED"
    )
    api_auth_keys: str = Field(
        default="", validation_alias="API_AUTH_KEYS",
        description="Comma-separated list of valid API keys.",
    )

    # ── Deep-agent scratch store backend (todos + VFS) ───────────────────────
    # "memory" (default): process-local dict, same behavior as before this
    # setting existed. "postgres": execution_scratch_store table (migration
    # 029) — needed for horizontal scaling, where a run's tool calls can land
    # on a different replica than the one that wrote earlier scratch state.
    scratch_store_backend: str = Field(
        default="memory", validation_alias="SCRATCH_STORE_BACKEND",
    )

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
    # Per-call timeout (seconds) for codegraph's `search_code` tool only. It shells
    # out to grep over source files (the one non-graph, potentially slow tool); the
    # fast graph tools inherit the default MCP tool timeout. Capping it stops a
    # single broad grep from exhausting an agent run's time/iteration budget — the
    # agent gets a recoverable tool error and falls back to the graph tools. A
    # timeout no longer forces an MCP reconnect (the session survives it safely —
    # see mcp_client_manager.execute_tool).
    #
    # 45s (was 120s): this cap MUST stay well below the delegated-subagent budget
    # (delegation_child_timeout_seconds, 240s) — at 120s a single slow grep ate
    # two-thirds of a code-investigation subagent's wall clock and timed it out
    # before it could fall back to the fast indexed tools. search_code is already
    # steered to be a last resort (see build_codegraph_tools), so failing fast and
    # falling back costs little; a genuinely huge grep just returns a recoverable
    # timeout sooner.
    codegraph_search_timeout_seconds: float = Field(
        default=45.0,
        validation_alias="CODEGRAPH_SEARCH_TIMEOUT",
    )
    code_analyzer_list_cache_ttl_seconds: float = 60.0
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

    # ── ONNX code-embedding semantic search ──────────────────────────────────
    # A Bedrock-independent embedding path for concept-level code search (fills
    # codegraph's empty search_semantic). Runs a small quantized sentence-
    # transformer on CPU via onnxruntime with a SHA256(model+content) cache.
    # Always on: it degrades gracefully (the tool returns an error/indexing note)
    # until a model is provisioned, so there's no reason to gate it. The env var
    # remains as an escape hatch for a locked-down build.
    code_semantic_enabled: bool = Field(
        default=True, validation_alias="CODE_SEMANTIC_ENABLED"
    )
    # The single embedding model used: snowflake-arctic-embed-s — best accuracy in
    # the fast 384-dim/34MB tier (see app/core/code_semantic/models.py). Fixed
    # (not user-selectable); the env var stays for an ops override if ever needed.
    code_semantic_model: str = Field(
        default="snowflake-arctic-embed-s", validation_alias="CODE_SEMANTIC_MODEL"
    )
    # Base dir holding one subdir per model. Defaults to /opt/models, where the
    # arctic-embed-s model is baked into the image (NOT /app/data — a mounted
    # volume that would shadow baked files). The Docker image also sets
    # CODE_SEMANTIC_MODELS_ROOT=/opt/models explicitly.
    code_semantic_models_root: str = Field(
        default="/opt/models", validation_alias="CODE_SEMANTIC_MODELS_ROOT"
    )
    # SQLite embedding cache path (SHA256(model+content) → float32 vector).
    code_semantic_cache_db: str = Field(
        default="/app/data/code_semantic/embeddings.db",
        validation_alias="CODE_SEMANTIC_CACHE_DB",
    )
    # When the model files are missing, allow fetching them from HuggingFace over
    # an unverified TLS context (the only path past the corp self-signed-cert
    # wall). Off by default — a locked-down deploy should vendor the files.
    code_semantic_allow_download: bool = Field(
        default=False, validation_alias="CODE_SEMANTIC_ALLOW_DOWNLOAD"
    )
    code_semantic_batch_size: int = Field(
        default=32, validation_alias="CODE_SEMANTIC_BATCH_SIZE"
    )
    # Max chars of a symbol's real source body folded into its embedding text
    # (0 = header-only). Embedding the body is the main accuracy lever, but longer
    # text = proportionally slower indexing, so this is the primary index-cost
    # knob. 2000 ≈ ~50 lines — enough of the body for real retrieval signal past
    # the signature; the embedder's 512-token cap bounds the upper end anyway, and
    # background indexing absorbs the extra build cost. Was 600 (signature-only),
    # which made long functions retrievable only by their opening lines.
    code_semantic_body_max_chars: int = Field(
        default=2000, validation_alias="CODE_SEMANTIC_BODY_MAX_CHARS"
    )
    # Score multiplier applied to test/build-output entities when the query does
    # NOT express test intent — de-prioritizes tests without hiding them (mirrors
    # code-context-engine's 0.8 path penalty). 1.0 disables the penalty.
    code_semantic_test_penalty: float = Field(
        default=0.85, validation_alias="CODE_SEMANTIC_TEST_PENALTY"
    )

    # Agent / tools / output limits
    # Bedrock-only: this is the sole generative provider (see
    # app.workflow.strategies.react.llm_factory, which rejects anything else).
    # The default must match that policy — docker-compose sets
    # PROVIDER_TRANSPORT=bedrock, but anything running outside compose (tests,
    # evals on the host, a fresh deploy) falls back to this value.
    provider_transport: str = "bedrock"
    # Default model id for the shared DB-resolved LLM utility (app.core.llm.
    # call_llm) — used as the env fallback when no DB LLM config exists, and as a
    # last-resort model for compaction summaries. Backs graders, supervision, and
    # memory extraction. Override via CRAWLER_MODEL (name kept for compatibility
    # with the "crawler" gateway role).
    crawler_model: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    # Opt-in override for the shared call_llm utility. When set, it uses this
    # model instead of the DB-configured model — callers can be moved to a
    # cheaper tier once accuracy holds. None = keep DB model.
    crawler_model_override: Optional[str] = Field(
        default=None, validation_alias="CRAWLER_MODEL_OVERRIDE"
    )
    # ReAct loop bound. LangGraph counts a "step" as one node transition; each
    # ReAct iteration is ~2 steps (agent + tool node), so 25 ≈ 12 iterations.
    # Raised from 12 because code investigations legitimately need more hops
    # (find → paginate get_body → trace → synthesize); at 12 the agent was hit
    # the limit mid-investigation and returned a "Let me search…" preamble.
    # Override via AGENT_RECURSION_LIMIT.
    agent_recursion_limit: int = Field(default=25, validation_alias="AGENT_RECURSION_LIMIT")
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
    # ── Engine-level run budgets ──────────────────────────────────────────────
    # A wall-clock deadline threaded INTO the loop itself, distinct from the
    # outer supervisor_wall_clock_seconds (which is only checked BETWEEN agent
    # turns, so a single slow turn or tool run can overshoot it). At ~90% of the
    # deadline the agent gets one graceful "synthesize now" nudge (mirrors the
    # max-turns forced-synthesis rung); at 100% it stops with a partial answer
    # rather than being killed mid-thought — see app.harness.run_budget, which
    # enforces both rungs from the pre-model hook. Defaults to 840s so it fires just
    # before the 900s supervisor ceiling, converting that hard cliff into a
    # graceful partial. Set to 0 to disable. Override via env.
    agent_run_deadline_seconds: float = Field(
        default=840.0, validation_alias="AGENT_RUN_DEADLINE_SECONDS"
    )
    # Per-run total-token ceiling (input+output across all turns; cache reads and
    # writes are excluded). 0 = disabled. When >0 the agent gets the same graceful
    # synthesis nudge at ~90% then stops with stop_reason=token_budget at 100%.
    # Override via env.
    agent_run_token_budget: int = Field(
        default=0, validation_alias="AGENT_RUN_TOKEN_BUDGET"
    )
    # Per-tool-call wall-clock cap (seconds), enforced by app.harness.tool_timeout.
    # 0 = no explicit cap; a tool is then still bounded by whatever remains of
    # agent_run_deadline_seconds above (so a hung tool cannot outlive its run).
    # 240 recommended — matches DELEGATION_CHILD_TIMEOUT_SECONDS. A timed-out
    # tool returns an honest error string to the model and the turn continues;
    # it never aborts the run. The effective cap is always the smaller of this
    # and the remaining run deadline. Override via env.
    agent_tool_call_timeout_seconds: float = Field(
        default=0.0, validation_alias="AGENT_TOOL_CALL_TIMEOUT_SECONDS"
    )
    # Learn a per-model correction factor for the chars/4 token heuristic from
    # actual Bedrock usage (app.core.llm.token_calibration), so compaction thresholds
    # track real token counts. Off by default — an exact no-op (factor 1.0) until
    # enabled and an observation is recorded. Override via env.
    token_estimate_calibration_enabled: bool = Field(
        default=False, validation_alias="TOKEN_ESTIMATE_CALIBRATION_ENABLED"
    )
    mcp_tool_output_max_chars: int = 8000
    # Universal safety-net ceiling for ANY single tool result that lacks its own
    # cap (db/edit/playbook StructuredTools). Larger than the per-family 8 KB
    # caps so it only catches truly unbounded outputs. Set to 0 to disable.
    # Override via env.
    tool_output_max_chars: int = Field(default=50000, validation_alias="TOOL_OUTPUT_MAX_CHARS")
    # When AgentSpec.filesystem is on, a StructuredTool result longer than this
    # is offloaded to the session VFS (app.harness.tool_offload) and replaced
    # with a short handle+preview instead of bloating every subsequent turn's
    # context. Applied BEFORE tool_output_max_chars so an offloaded handle is
    # never itself truncated. Matches the VFS backend's own _OFFLOAD_THRESHOLD
    # default so behavior is identical whether offload happens via this live
    # wrapper or the lower-level vfs_offload_if_large() helper. Set to 0 to
    # disable. Override via env.
    tool_result_offload_chars: int = Field(default=6000, validation_alias="TOOL_RESULT_OFFLOAD_CHARS")
    # Tool exposure: "legacy" keeps today's apply_tool_disclosure behavior
    # (all-or-nothing defer at a count/token threshold) byte-for-byte.
    # "window" switches to app.harness.tool_exposure.ToolExposureManager,
    # which caps the number of non-core tools bound directly on any one
    # assembly to tool_exposure_max — MCP tool-density research reports
    # bound-tool selection accuracy drops below 90% once the active tool
    # count exceeds roughly this range. Core/family tools (CloudWatch,
    # crawler, DB, playbook, delegate, edit, planning, filesystem) are
    # exempt from the cap in both modes; tools outside the window stay
    # reachable via the same search_tools/call_tool bridge. Override via env.
    tool_exposure_mode: str = Field(default="legacy", validation_alias="TOOL_EXPOSURE_MODE")
    tool_exposure_max: int = Field(default=12, validation_alias="TOOL_EXPOSURE_MAX")
    # ── LangGraph runtime perf knobs (all default to today's behavior) ──
    # Checkpoint durability for a LangGraph agent run. "async" (default) writes
    # each superstep's state in the background — today's implicit behavior.
    # "exit" writes only once, when the run finishes or pauses — cuts per-turn
    # Postgres serialization+I/O for the common case that never resumes. "sync"
    # blocks each superstep on the write (strongest durability, slowest). HITL
    # runs are clamped to "async" at the call site (run_agent_once) because
    # resume relies on interrupt-time checkpoints. Override via env.
    agent_durability: str = Field(default="async", validation_alias="AGENT_DURABILITY")
    # Cap on concurrent tasks within a LangGraph superstep (parallel tool calls,
    # Send() fan-out). 0 = unset (today's unbounded behavior); >0 sets the run
    # config's ``max_concurrency`` to bound fan-out against Bedrock rate limits.
    agent_max_concurrency: int = Field(default=0, validation_alias="AGENT_MAX_CONCURRENCY")
    # When on, the shared durable checkpointer uses AsyncShallowPostgresSaver
    # (keeps only the latest checkpoint per thread) instead of AsyncPostgresSaver.
    # HITL pause/resume needs only the latest checkpoint, so it keeps working;
    # what is dropped is time-travel history (unused product-wise). Off by
    # default; falls back to the full saver if the class is unavailable.
    checkpoint_shallow: bool = Field(default=False, validation_alias="CHECKPOINT_SHALLOW")
    # botocore HTTP connection-pool size for the shared Bedrock runtime client.
    # Botocore's default is 10; one client serves parallel tool supersteps +
    # delegate_parallel children, so at 10 concurrent LLM calls queue on the
    # pool. Baked at 20 to give headroom for parallel tool fan-out plus the
    # delegation_max_concurrent (=3) children without queueing. Raise further
    # (e.g. 50) for very wide fan-outs. Override via env.
    bedrock_max_pool_connections: int = Field(default=20, validation_alias="BEDROCK_MAX_POOL_CONNECTIONS")
    # Opt into Bedrock latency-optimized inference (performance_config={"latency":
    # "optimized"}). Support is model/region-dependent, so this is off by default
    # and left per-deployment opt-in. Override via env.
    bedrock_latency_optimized: bool = Field(default=False, validation_alias="BEDROCK_LATENCY_OPTIMIZED")
    # Streaming path for a LangGraph agent run. Baked ON (default) to use the
    # lighter astream(stream_mode=["messages","updates"]) loop
    # (execute_agent_stream_v2), which also returns complete final state (incl.
    # ToolMessages) and drops the duplicate full-agent re-run fallback. Set to
    # False via env to fall back to the legacy astream_events(v2) event loop.
    agent_stream_mode_enabled: bool = Field(default=True, validation_alias="AGENT_STREAM_MODE_ENABLED")
    # Cache the compiled child agent across delegate calls instead of rebuilding
    # it (LLM resolution + create_react_agent) on every delegation. Scoped to
    # children with an EXPLICIT per-def model (a fixed DB llm_config) so the
    # baked LLM is stable across runs — children that inherit the parent LLM are
    # never cached (the parent's fallback chain can swap model/region/creds
    # mid-run). LangGraph-engine children only. Baked ON (default) for the
    # repeated/parallel-delegation speedup; set False via env to disable.
    subagent_compiled_cache_enabled: bool = Field(default=True, validation_alias="SUBAGENT_COMPILED_CACHE_ENABLED")
    # Explicit global operator override for EVERY delegated subagent's model —
    # the deliberate "force all subagents onto one model" hammer. A DB
    # llm_config name.
    # Outranks a subagent def's own ``model`` and the inherited parent LLM alike.
    # Deliberately off (None) by default: with no override set, an unpinned child
    # inherits the parent's model, so the workflow's Language Model node wins —
    # NOT a silently-assigned "subagent" gateway role.
    subagent_model_override: Optional[str] = Field(
        default=None, validation_alias="SUBAGENT_MODEL_OVERRIDE"
    )
    # Step-level trajectory events (app.harness.step_recorder, migration 030's
    # trajectory_events table). Off by default: a no-op recorder is used, zero
    # extra DB writes. When on, the agent records one event per
    # model turn / tool call, buffered in memory and flushed as a single batch
    # insert at the end of the run — never a per-step DB round-trip. LangGraph
    # engine runs are not yet instrumented (native-engine-only for now).
    step_events_enabled: bool = Field(default=False, validation_alias="STEP_EVENTS_ENABLED")
    # Metamemory (app.harness.metamemory): seeds /plan.txt, /milestones.txt,
    # /context_summary.txt in the session VFS at run start and steers compaction
    # to inject them instead of re-summarizing raw history. Also read at the
    # start of a follow-up turn so a resumed investigation begins with prior
    # plan/progress in view (preflight injects a resumed-state block). Default
    # ON: still a full no-op unless AgentSpec.filesystem is set on the profile
    # (nowhere to seed files otherwise), so existing non-filesystem workflows
    # are byte-identical. Toggle in Settings → Feature flags.
    metamemory_enabled: bool = Field(default=True, validation_alias="METAMEMORY_ENABLED")
    # Persist metamemory files across chat turns (migration 029's
    # execution_scratch_store, keyed "session:<chat_session_id>") instead of the
    # per-run-only default. Default ON but self-gating: a no-op unless
    # scratch_store_backend == "postgres" (the durable table the cross-turn
    # persistence relies on), so memory-backend deployments are unaffected.
    vfs_session_persistence_enabled: bool = Field(
        default=True, validation_alias="VFS_SESSION_PERSISTENCE_ENABLED",
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
    # (Postgres FTS) before each run, scoped per-repo + a shared global bank.
    # Opt-in (default off) so it is a no-op until an operator enables it. Recall
    # is full-text over the OKF bundle + semantic_memory banks; document-side
    # tags act as paraphrase synonyms (the Bedrock-embedding legs were removed).
    semantic_memory_enabled: bool = Field(
        default=False, validation_alias="SEMANTIC_MEMORY_ENABLED"
    )
    # Token-budget guardrails (keep recall net-positive, not a per-turn leak):
    memory_recall_k: int = Field(default=4, validation_alias="MEMORY_RECALL_K")
    memory_max_chars: int = Field(default=600, validation_alias="MEMORY_MAX_CHARS")
    # Only auto-capture an investigation finding when confidence is at least this
    # (avoids storing low-value/uncertain results). 0 captures everything.
    memory_capture_min_confidence: float = Field(
        default=0.6, validation_alias="MEMORY_CAPTURE_MIN_CONFIDENCE"
    )

    # ── Tool-output compression ──────────────────────────────────────────────
    # Replaces lossy char-truncation with type-aware reversible compression for
    # large tool outputs. Compression runs in a local sidecar (no LLM call).
    # Opt-in (default off); on timeout/error falls back to existing truncation.
    # Compression is applied at the tool-output source and ALWAYS re-capped, so
    # conversation-history compaction is intentionally left to the existing
    # structure-aware ladder (it preserves Bedrock tool_use/tool_result pairing,
    # which a message-array compressor cannot guarantee). Reversible retrieval
    # (recover originals) is covered by the VFS offload + fs_read path.
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
        default=2000, validation_alias="COMPRESSION_TIMEOUT_MS"
    )
    # Retries on transient sidecar errors. Compression is opportunistic (the
    # truncation fallback is always correct), so keep this low to bound the
    # latency added to the hot tool-call path.
    compression_max_retries: int = Field(
        default=1, validation_alias="COMPRESSION_MAX_RETRIES"
    )
    # Model id sent to the sidecar so it selects the matching tokenizer. Empty
    # falls back to a neutral placeholder. Never hardcoded — operators set the
    # deployed Bedrock model id here (models are DB-resolved elsewhere).
    compression_model_hint: str = Field(
        default="", validation_alias="COMPRESSION_MODEL_HINT"
    )
    # Extend compression from MCP tool outputs to every other tool family
    # (db, cloudwatch, crawler, code analyzer) via the universal output cap.
    # Default off → non-MCP tool behaviour is byte-identical until enabled.
    compression_all_tools: bool = Field(
        default=False, validation_alias="COMPRESSION_ALL_TOOLS"
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
    # Verified completion: when True, update_todo refuses to mark a plan item
    # completed without cited evidence (a tool ref / file:line / evidence ID),
    # and the finalizer flags a plan left with incomplete items as UNVERIFIED
    # so a half-finished run can't self-report success. Off by default — only
    # meaningful when the planning tools are in play (AgentSpec.planning).
    todo_evidence_required: bool = Field(
        default=False, validation_alias="TODO_EVIDENCE_REQUIRED"
    )

    # ── Skill + tool disclosure (two-stage: map + search/load tools) ─────────
    # Stage one is a MAP — names only, near-zero per-turn cost: every
    # non-conversational turn gets a ``# Skill map`` block naming the available
    # skills, and a deferred MCP tool set is named in the ``search_tools``
    # description. Stage two is the model's own lookup: ``search_skills`` /
    # ``search_tools`` rank the map by intent, then ``skill`` loads the full
    # runbook (or ``call_tool`` invokes the tool). A user message starting with
    # ``/<skill-name>`` bypasses both stages and expands that skill directly.
    skill_tool_enabled: bool = Field(
        default=True, validation_alias="SKILL_TOOL_ENABLED"
    )
    # Deterministic ``/<skill-name> [args]`` slash-command expansion in chat.
    skill_slash_commands_enabled: bool = Field(
        default=True, validation_alias="SKILL_SLASH_COMMANDS_ENABLED"
    )
    # Char budget for the per-turn skill map (names only). Over budget the map
    # degrades to a count-only hint — search_skills still reaches every skill.
    skill_map_char_budget: int = Field(
        default=1500, validation_alias="SKILL_MAP_CHAR_BUDGET"
    )
    # Default number of skills ``search_skills`` returns per query.
    skill_search_k: int = Field(default=5, validation_alias="SKILL_SEARCH_K")
    # Char budget for the tool map embedded in the ``search_tools`` description
    # (deferred tool names). Over budget it truncates to "+K more".
    tool_map_char_budget: int = Field(
        default=2000, validation_alias="TOOL_MAP_CHAR_BUDGET"
    )
    # Cap on the full runbook body injected when a skill is loaded by the
    # ``skill`` tool. Longer bodies are truncated.
    skill_body_inject_chars: int = Field(
        default=8000, validation_alias="SKILL_BODY_INJECT_CHARS"
    )

    # ── Knowledge bundle (OKF) + skill storage (file-based; no DB) ────────────
    # Durable knowledge (known issues, log patterns, services, skills) lives as
    # an Open Knowledge Format bundle: a portable directory of markdown files
    # with YAML frontmatter. Auto-learn writes here (reviewable file diffs) and
    # concepts are indexed into the ``kb`` memory bank for recall.
    knowledge_dir: str = Field(default="data/knowledge", validation_alias="KNOWLEDGE_DIR")
    knowledge_bundle_enabled: bool = Field(
        default=True, validation_alias="KNOWLEDGE_BUNDLE_ENABLED"
    )
    # Retrievability lint: recall is FTS-only (no vector fallback), so a concept
    # with no tags and a thin title is unfindable by paraphrase. write_concept
    # logs a warning when a doc has fewer than this many tags or a too-short
    # description; the same check backs the bundle-scan lint. Advisory only —
    # never blocks a write (0 disables the tag check).
    knowledge_min_tags: int = Field(default=3, validation_alias="KNOWLEDGE_MIN_TAGS")
    # Skills are markdown SKILL.md files (SkillManager) under skills_dir — now a
    # sub-tree of the knowledge bundle so skills + KB share one portable bundle
    # and can cross-link (a known-issue doc → its runbook skill).
    skills_dir: str = Field(
        default="data/knowledge/skills", validation_alias="SKILLS_DIR"
    )

    # ── Delegation (multi-agent) ─────────────────────────────────────────────
    # Bounds for the orchestrator→specialist delegation layer.  All per-node /
    # per-profile overrides layer on top via spec_factory.resolve_profile_fields.
    delegation_max_depth: int = Field(
        default=1, validation_alias="DELEGATION_MAX_DEPTH"
    )
    delegation_max_concurrent: int = Field(
        default=3, validation_alias="DELEGATION_MAX_CONCURRENT"
    )
    # 240s (was 180s): a code/DB-investigation subagent legitimately chains
    # several codegraph + SQL calls; 180s cut it short once even one search hit
    # the old 120s per-call cap. Kept BELOW agent_invoke_timeout_seconds (300s,
    # the inner non-streaming cap) so this wait_for fires first and the child's
    # timeout-recovery envelope (partial result, "tools available but slow") is
    # returned cleanly instead of the inner invoke being killed mid-flight.
    delegation_child_timeout_seconds: float = Field(
        default=240.0, validation_alias="DELEGATION_CHILD_TIMEOUT_SECONDS"
    )
    delegation_output_max_chars: int = Field(
        default=8000, validation_alias="DELEGATION_OUTPUT_MAX_CHARS"
    )
    # CSV of fnmatch patterns always stripped from child tool sets.
    # Children are investigate/read-only by default; orchestrator owns mutations.
    # fs_write*/fs_append/fs_upsert/fs_prune together cover every VFS mutation
    # (metamemory files included) — children keep fs_read/fs_ls/fs_grep only.
    delegation_blocked_tools: str = Field(
        default=DEFAULT_DELEGATION_BLOCKED_TOOLS,
        validation_alias="DELEGATION_BLOCKED_TOOLS",
    )

    # ── Metabolic token economy (app.harness.budget_ledger) ─────────────────
    # OFF by default — a no-op ledger is used, zero behavior change. Applied at
    # the granularity delegate_batch actually supports (between children, not
    # mid-run): each specialist branch gets an initial energy grant, spends it
    # as its children run, earns more on success, drifts its role state (phi)
    # explore<->exploit, and triggers lifecycle turnover (half its remaining
    # energy returns to the communal pool; a fresh scout profile replaces it)
    # once energy is exhausted or it stalls repeatedly.
    token_economy_enabled: bool = Field(
        default=False, validation_alias="TOKEN_ECONOMY_ENABLED"
    )
    token_economy_run_budget: int = Field(
        default=200_000, validation_alias="TOKEN_ECONOMY_RUN_BUDGET",
    )
    token_economy_base_grant: int = Field(
        default=40_000, validation_alias="TOKEN_ECONOMY_BASE_GRANT",
    )
    token_economy_energy_min: int = Field(
        default=5_000, validation_alias="TOKEN_ECONOMY_ENERGY_MIN",
    )
    token_economy_stall_limit: int = Field(
        default=3, validation_alias="TOKEN_ECONOMY_STALL_LIMIT",
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

    # ── Failure->governance conversion (app.core.improvement.analyzer) ──────
    # OFF by default. When True: (1) analyze_recent() upserts its batch's
    # error fingerprints into failure_ledger (migration 030); (2) a fingerprint
    # recurring >= governance_convert_threshold times becomes an 'eval'-kind
    # draft proposal (convert_recurring_failures) that apply.py can
    # auto-apply — bookkeeping only (marks the ledger row 'converted'), never
    # fabricates eval assertions.
    governance_conversion_enabled: bool = Field(
        default=False, validation_alias="GOVERNANCE_CONVERSION_ENABLED"
    )
    governance_convert_threshold: int = Field(
        default=3, validation_alias="GOVERNANCE_CONVERT_THRESHOLD"
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
    # ── Tool-inclusive chat history replay ───────────────────────────────────
    # A follow-up question re-runs every tool from scratch unless prior turns'
    # tool_calls/tool_results are replayed alongside the plain text history
    # (app.harness.chat_history). Fail-open by design: any error in the
    # rebuild path falls back to the UI's text-only history, so this defaults
    # on. The token/execution caps bound how much prior tool activity gets
    # replayed — per-turn context compaction handles overall window pressure
    # on top of this.
    chat_history_include_tools: bool = Field(
        default=True, validation_alias="CHAT_HISTORY_INCLUDE_TOOLS"
    )
    chat_tool_history_max_tokens: int = Field(
        default=12_000, validation_alias="CHAT_TOOL_HISTORY_MAX_TOKENS"
    )
    chat_tool_history_max_executions: int = Field(
        default=2, validation_alias="CHAT_TOOL_HISTORY_MAX_EXECUTIONS"
    )
    # How often the scheduler runs the self-improvement curator (hours). The
    # cheap promotions run every cycle; LLM consolidation stays gated by
    # ``memory_audit_enabled``.
    curator_interval_hours: int = Field(
        default=6, validation_alias="CURATOR_INTERVAL_HOURS"
    )

    # ── Multi-replica leader election ────────────────────────────────────────
    # WorkflowScheduler._ensure_leader acquires a Postgres advisory lock so only
    # one replica fires cron/curator/hill-climb jobs. Default is fail-OPEN
    # (lock-acquisition error → assume leader) so a single-node deployment with
    # no Postgres advisory-lock support still runs its scheduled jobs. Set this
    # True in a genuine multi-replica deployment to fail-CLOSED instead — a lock
    # error then means "assume NOT leader" so a transient DB hiccup can't cause
    # every replica to double-fire the same cron/curator/hill-climb run.
    leader_lock_fail_closed: bool = Field(
        default=False, validation_alias="LEADER_LOCK_FAIL_CLOSED"
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

    # ── Action Supervisor (pre-execution write gate) ──────────────────────────
    # Distinct from the post-run quality Supervisor above: this reviews each
    # intercepted write-class action BEFORE it runs. Default off; ships in
    # shadow mode first (review + record verdict, but the human/timeout still
    # decides) so the reviewer can be calibrated against real decisions.
    action_supervisor_enabled: bool = False
    action_supervisor_shadow_mode: bool = True
    action_supervisor_timeout_seconds: float = 20.0
    # Risk tiers (CSV fnmatch patterns). Low-risk → the Supervisor LLM decides;
    # high-risk → escalates to a human (Supervisor verdict shown as advisory).
    # Anything gated-but-untier'd is treated as high (fail toward the human).
    action_supervisor_low_risk_patterns: str = (
        "fs_write*,fs_append,fs_upsert,fs_prune,save_playbook,patch_playbook,"
        "pin_fact,write_todos"
    )
    action_supervisor_high_risk_patterns: str = (
        "edit_file,create_file,apply_patch,run_command,run_verify,"
        "delegate_investigation,wiki_publish,skill_write"
    )
    # Gate the wiki-publish output-node handler through the approval primitive.
    wiki_publish_approval_enabled: bool = False

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
    # ── Cadenced report-only loops ────────────────────────────────────────────
    # Optional webhook a scheduled (cron-fired) run POSTs a compact result
    # report to (app.core.observability.notify.post_run_report). Empty = off (no report sent);
    # any delivery failure is swallowed. The report-only "L1" loop pattern: the
    # cron fires an investigation that REPORTS rather than acts.
    loop_report_webhook_url: str = Field(
        default="", validation_alias="LOOP_REPORT_WEBHOOK_URL"
    )
    # Carry a scheduled loop's own progress ledger across fires: each cron fire
    # of workflow <name> resumes the metamemory ledger keyed "workflow:<name>"
    # (reuses the Phase-2 session-persistence machinery). Off by default; also
    # requires the metamemory + postgres-scratch prerequisites to have any
    # effect. Toggle in Settings → Feature flags.
    loop_state_continuity_enabled: bool = Field(
        default=False, validation_alias="LOOP_STATE_CONTINUITY_ENABLED"
    )

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
        "action_supervisor_enabled",
        "action_supervisor_shadow_mode",
        "wiki_publish_approval_enabled",
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
