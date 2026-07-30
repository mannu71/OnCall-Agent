"""HTTP server, logging, database, CORS, SSE, scheduling.

One slice of :class:`app.config.Settings`. Mixins carry no behaviour of
their own — they exist so 200+ fields are readable in domain-sized files.
Settings inherits every one of them, so the flat ``settings.<field>``
surface every call site already uses is unchanged.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from app.config._shared import DEFAULT_DELEGATION_BLOCKED_TOOLS  # noqa: F401


class ServerSettings(BaseSettings):
    """HTTP server, logging, database, CORS, SSE, scheduling."""

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

    # SSE / streaming
    sse_stream_timeout_seconds: int = 600

    sse_heartbeat_interval_seconds: int = 15

    sse_queue_maxsize: int = 1000

    # Parallel flow / map-reduce
    parallel_flow_concurrency: int = 5

    parallel_flow_item_timeout: float = 180.0

    parallel_flow_batch_timeout: float = 0.0

    parallel_flow_fail_fast: bool = False

    parallel_flow_min_success_rate: float = Field(default=0.5, validation_alias="MAP_REDUCE_MIN_SUCCESS_RATE")

    # Runtime retention / concurrency
    max_runtime_events: int = 500

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

    # Scheduler settings
    scheduler_timezone: str = "UTC"

    max_concurrent_workflows: int = 5
