"""SQLAlchemy database models."""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

# Import Base from database module
from app.core.database import Base


def _utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Migration note
# ---------------------------------------------------------------------------
# If upgrading an existing deployment, run the following SQL once:
#
#   ALTER TABLE known_issues RENAME TO knowledge_entries;
#   CREATE INDEX IF NOT EXISTS ix_knowledge_entries_source
#       ON knowledge_entries(source);
#
#   CREATE TABLE IF NOT EXISTS skills (
#       id              SERIAL PRIMARY KEY,
#       name            VARCHAR(255) UNIQUE NOT NULL,
#       title           VARCHAR(255) NOT NULL,
#       description     TEXT,
#       trigger_patterns JSON,
#       steps           JSON,
#       workflow_name   VARCHAR(255),
#       source          VARCHAR(50) DEFAULT 'distilled',
#       status          VARCHAR(50) DEFAULT 'active',
#       success_count   INTEGER DEFAULT 0,
#       recall_count    INTEGER DEFAULT 0,
#       last_used_at    TIMESTAMPTZ,
#       promoted_from_id INTEGER,
#       created_at      TIMESTAMPTZ,
#       updated_at      TIMESTAMPTZ
#   );
# ---------------------------------------------------------------------------


class WorkflowModel(Base):
    """Workflow database model."""
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text)
    nodes = Column(JSON, nullable=False)
    edges = Column(JSON, nullable=False)
    viewport = Column(JSON)
    type = Column(String(20), default='workflow')
    enabled = Column(Boolean, default=True)
    schedule = Column(String(100))  # Cron expression
    indexing_status = Column(String(50), nullable=True)  # None | 'indexing' | 'indexing_failed: [repo]'
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class ExecutionModel(Base):
    """Execution history database model."""
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer)
    workflow_name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    duration_ms = Column(Integer)
    input = Column(JSON)
    output = Column(JSON)
    error = Column(Text)
    logs = Column(JSON)
    trajectory    = Column(JSON)  # Full message trace for analysis and training
    input_tokens  = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    total_tokens  = Column(Integer, default=0)
    # Set only when this execution was triggered by a chat turn (migration 028)
    # — every chat message reuses the same /execute endpoint as a real
    # scheduled/manual workflow run. NULL = a real workflow run; non-NULL lets
    # the Dashboard exclude ad-hoc chat turns from "Recent runs" / stats.
    chat_session_id = Column(String(36), nullable=True, index=True)


class LLMConfigModel(Base):
    """LLM configuration database model."""
    __tablename__ = "llm_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    provider = Column(String(100), nullable=False)
    model = Column(String(255), nullable=False)
    endpoint = Column(String(500))
    base_url = Column(String(500))
    temperature = Column(Float, default=0.7)
    max_tokens = Column(Integer, default=4096)
    region = Column(String(50), default="us-east-1")
    icon = Column(String(10))
    description = Column(Text)
    aws_profile = Column(String(100))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    # Flags this config as the active embedding model — Settings page sets
    # this through ``llm_config_repository.save``; the code indexer's
    # embedder reads the flagged row at runtime. Column already exists in
    # ``llm_configs`` (see migrations/add_use_for_embeddings_to_llm_configs.sql).
    use_for_embeddings = Column(Boolean, default=False)


class ModelKeyModel(Base):
    """Provider-level API key storage model.

    Multiple rows per provider are allowed (migration 017): each is a distinct
    credential identified by ``key_label`` that the fallback-chain router can
    rotate across when one is throttled. ``priority`` orders selection (lower
    first); ``enabled`` toggles a credential without deleting it.
    """
    __tablename__ = "model_keys"
    __table_args__ = (
        UniqueConstraint("provider", "key_label", name="model_keys_provider_label_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(100), nullable=False)
    key_label = Column(String(100), nullable=False, default="default")
    priority = Column(Integer, nullable=False, default=100)
    enabled = Column(Boolean, nullable=False, default=True)
    api_key = Column(String(500))
    secret_key = Column(String(500))
    endpoint = Column(String(500))
    region = Column(String(50))
    access_key_id = Column(String(500))
    secret_access_key = Column(String(500))
    session_token = Column(String(500))
    description = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class SemanticMemoryModel(Base):
    """Bank-scoped semantic memory (migration 018).

    Learned, operational memory captured while operating the system — distinct
    from the static, code-derived ``repo_docs`` intelligence. ``bank='repo'``
    rows are scoped to one ``repo_name``; ``bank='global'`` rows (repo_name NULL)
    are visible to every investigation. Recall is hybrid FTS + vector.
    """
    __tablename__ = "semantic_memory"
    __table_args__ = (
        Index("idx_semantic_memory_bank_repo", "bank", "repo_name"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bank = Column(String(16), nullable=False, default="repo")
    repo_name = Column(String(255))
    content = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    embedding = Column(Vector(1024))
    search_vector = Column(TSVECTOR)
    source = Column(String(32), nullable=False, default="agent")
    importance = Column(Float, nullable=False, default=0.5)
    veracity = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime(timezone=True))
    last_recalled_at = Column(DateTime(timezone=True))
    recall_count = Column(Integer, nullable=False, default=0)


class ChatSessionModel(Base):
    """A persistent chat conversation (migration 022).

    One row per conversation, holding only *metadata* — the messages live in
    ``chat_messages`` and are hydrated on demand when a session is opened. This
    keeps the session list cheap to load at boot even with thousands of rows.
    """
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True)  # app-generated uuid4
    title = Column(String(255), nullable=False, default="New chat")
    workflow_name = Column(String(255))
    model = Column(String(255))
    archived = Column(Boolean, nullable=False, default=False)
    is_important = Column(Boolean, nullable=False, default=False)
    message_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_message_at = Column(DateTime(timezone=True))
    # Cumulative token usage across every turn in this conversation (migration
    # 028) — distinct from a single turn's counts in chat_messages.metadata.
    total_input_tokens = Column(Integer, nullable=False, default=0)
    total_output_tokens = Column(Integer, nullable=False, default=0)
    total_cache_read_tokens = Column(Integer, nullable=False, default=0)
    total_cache_creation_tokens = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_chat_sessions_active", "archived", "last_message_at"),
    )


class ChatMessageModel(Base):
    """A single message within a chat session (migration 022).

    Cascade-deletes with its parent session. ``metadata`` carries the UI-side
    enrichments (tool steps, token counts, privacy redactions, trace) so a
    resumed conversation renders exactly as it did live.
    """
    __tablename__ = "chat_messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(36), nullable=False, index=True)  # FK → chat_sessions.id
    role = Column(String(16), nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False, default="")
    # 'metadata' is reserved on the SQLAlchemy declarative Base, so map the
    # column under a non-reserved attribute name.
    meta = Column("metadata", JSON)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("idx_chat_messages_session", "session_id", "created_at"),
    )


class MCPServerModel(Base):
    """MCP server configuration database model."""
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    command = Column(String(500), nullable=False)
    args = Column(JSON)
    env = Column(JSON)
    enabled = Column(Boolean, default=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class LogPatternModel(Base):
    """Log pattern database model for RAG."""
    __tablename__ = "log_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    pattern = Column(Text, nullable=False)
    pattern_type = Column(String(50), nullable=False)
    severity = Column(Integer, default=1)
    description = Column(Text)
    embedding = Column(Vector(1024))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class KnowledgeEntryModel(Base):
    """Knowledge entry — generic RAG store for resolutions, playbooks, and findings."""
    __tablename__ = "knowledge_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    symptoms = Column(JSON)          # Array of symptom/trigger strings
    solution = Column(Text)          # Resolution text
    category = Column(String(100))   # Workflow domain or topic
    source = Column(String(50), default="manual")  # manual | agent | verified | skill
    embedding = Column(Vector(1024))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


# Backward-compat alias — remove once all import sites are updated
KnownIssueModel = KnowledgeEntryModel


class SkillModel(Base):
    """Executable skill — a structured, reusable resolution procedure.

    DEPRECATED / UNUSED: skills are now FILE-backed (one JSON per skill under
    ``settings.skills_store_dir``); see ``app.core.skills.service``. This model
    is retained only so the legacy ``skills`` table remains importable for the
    one-time file migration. Do not add new reads/writes against it.
    """
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Identity
    name  = Column(String(255), unique=True, nullable=False)  # slug: restart_api_pods
    title = Column(String(255), nullable=False)               # human label

    description = Column(Text)

    # Matching — phrases / regex patterns that indicate this skill is relevant
    trigger_patterns = Column(JSON)   # List[str]

    # Execution — ordered list of step dicts:
    #   [{order, description, tool, args_template, condition, on_failure}]
    steps = Column(JSON, nullable=False, default=list)

    # Optional: run a named workflow instead of individual steps
    workflow_name = Column(String(255))

    # Provenance
    source = Column(String(50), default="distilled")
    # distilled  — auto-generated from investigation
    # manual     — written by an engineer
    # promoted   — promoted from a knowledge_entry

    # Lifecycle
    status = Column(String(50), default="active")
    # active | draft | archived

    # Confidence (0..1) from the distillation LLM. Below the configured
    # threshold a distilled skill is saved as 'draft' (hidden from recall).
    confidence = Column(Float)
    # Last curator decision: kept | demoted | promoted | archived (migration 023).
    audit_verdict = Column(String(16))

    # Usage counters (updated by SkillService)
    success_count = Column(Integer, default=0)
    recall_count  = Column(Integer, default=0)
    last_used_at  = Column(DateTime(timezone=True))

    # Lineage — knowledge_entries.id this skill was promoted from (nullable)
    promoted_from_id = Column(Integer)

    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class BaselineMetricModel(Base):
    """Baseline metric database model."""
    __tablename__ = "baseline_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_name = Column(String(255), nullable=False)
    log_group = Column(String(255), nullable=False)
    normal_range_min = Column(Float)
    normal_range_max = Column(Float)
    threshold_warning = Column(Float)
    threshold_critical = Column(Float)
    time_window = Column(String(50))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class AnalysisHistoryModel(Base):
    """Analysis history database model."""
    __tablename__ = "analysis_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    log_group = Column(String(255), nullable=False)
    analysis_type = Column(String(100), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    summary = Column(Text)
    anomalies_found = Column(Integer, default=0)
    patterns_matched = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True))


class AlertModel(Base):
    """Alert database model."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    log_group = Column(String(255), nullable=False)
    alert_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON)
    status = Column(String(20), default="new")
    created_at = Column(DateTime(timezone=True))
    resolved_at = Column(DateTime(timezone=True))
    resolved_by = Column(String(255))


# ---------------------------------------------------------------------------
# (v1 SCIP code-intelligence tables removed — dropped in migration 007)
# ---------------------------------------------------------------------------


class BackgroundJobModel(Base):
    """Durable record of a background job (e.g. repo indexing).

    Source of truth for background work lifecycle + live progress, replacing the
    fire-and-forget asyncio task as the observable state. ``claimed_by`` enables
    multi-worker claiming via ``SELECT ... FOR UPDATE SKIP LOCKED``. See migration
    ``013_background_jobs.sql``.
    """
    __tablename__ = "background_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(50), nullable=False)           # e.g. 'repo_index'
    target = Column(String(255), nullable=False)            # e.g. workflow name
    status = Column(String(20), nullable=False, default="queued")  # queued|running|completed|failed
    progress = Column(Integer, nullable=False, default=0)
    total = Column(Integer, nullable=False, default=0)
    detail = Column(Text)
    error = Column(Text)
    payload = Column(JSON)
    claimed_by = Column(String(64))
    heartbeat_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_background_jobs_status", "status"),
        Index("ix_background_jobs_type_target", "job_type", "target"),
    )


class ToolApprovalModel(Base):
    """Audit record for a human-in-the-loop tool approval gate.

    One row per ``ask`` tool call (see
    ``app/workflow/strategies/react/tool_permissions.py``): written as
    ``pending`` before the agent blocks on operator approval, then updated to
    ``approved`` / ``denied`` / ``timeout`` when the decision resolves. Durable
    replacement for the prior browser-localStorage-only record. See migration
    ``012_tool_approvals.sql``.
    """
    __tablename__ = "tool_approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(64), nullable=False, index=True)
    request_id = Column(String(64), nullable=False)
    tool_name = Column(String(255), nullable=False)
    args_summary = Column(Text)
    decision = Column(String(20), nullable=False, default="pending")  # pending|approved|denied|timeout
    decided_by = Column(String(255))
    reason = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    decided_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("execution_id", "request_id", name="uq_tool_approvals_request"),
    )


class AppSettingModel(Base):
    """Application-wide key/value settings (e.g. ``global_timezone``).

    A small generic store for runtime-editable scalar settings configured from
    the Settings page. See migration ``011_app_settings.sql``.
    """
    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class PolicySetModel(Base):
    """A named, reusable governance policy set for the declarative policy engine.

    ``policies`` is the JSON list of policy entries (see ``app/core/policy``); a
    workflow references a set by name and the engine expands it at agent-build
    time. See migration ``016_policy_sets.sql`` and
    ``app/infrastructure/persistence/policy_set_repository.py``.
    """
    __tablename__ = "policy_sets"

    name = Column(String(128), primary_key=True)
    description = Column(Text)
    policies = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AgentProfileModel(Base):
    """A named, reusable agent profile (see migration ``025_agent_profiles.sql``).

    Lets a user configure any *type* of agent — role sentence, composable
    capabilities, output schema, default governance policies and deep-agent
    features — independent of the workflow graph. An agent node references one by
    name (``agent_config.profile``) and the executor merges these fields under any
    explicit node overrides. See ``app/infrastructure/persistence/agent_profile_repository.py``.
    """
    __tablename__ = "agent_profiles"

    name = Column(String(128), primary_key=True)
    description = Column(Text)
    role_prompt = Column(Text)
    capabilities = Column(JSON, nullable=False, default=list)
    default_tools = Column(JSON, nullable=False, default=list)
    output_schema = Column(String(64))
    default_policies = Column(JSON, nullable=False, default=list)
    deep_features = Column(JSON, nullable=False, default=dict)
    builtin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ModelRoleAssignmentModel(Base):
    """Maps a gateway role (agent | crawler | subagent) to a named LLM config.

    When a workflow does not explicitly wire / name a Language Model node for a
    consumer, the resolution pipeline falls back to the model assigned to that
    role here (and only then to the first ``llm_configs`` row). See
    ``app/infrastructure/persistence/model_role_repository.py``.
    """
    __tablename__ = "model_role_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(50), unique=True, nullable=False)          # agent | crawler | subagent
    llm_config_name = Column(String(255), nullable=False)           # llm_configs.name
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MCPRoleAssignmentModel(Base):
    """Maps a gateway role to a default MCP server (one role → many servers).

    Used as the agent's default toolbox when a workflow wires no tool nodes;
    wired nodes still win. See
    ``app/infrastructure/persistence/mcp_role_repository.py``.
    """
    __tablename__ = "mcp_role_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(50), nullable=False)                       # agent
    server_name = Column(String(255), nullable=False)              # mcp_servers.name
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("role", "server_name", name="uq_mcp_role_assignments_role_server"),
    )


class ExecutionScratchModel(Base):
    """Optional Postgres-backed deep-agent session scratch (todos + VFS).

    One row per (execution_id, store) holding the whole store's content as a
    JSON blob — used only when ``settings.scratch_store_backend == "postgres"``;
    the default "memory" backend never touches this table. See migration
    ``029_execution_scratch_store.sql`` and
    ``app/infrastructure/persistence/execution_scratch_repository.py``.
    """
    __tablename__ = "execution_scratch_store"

    execution_id = Column(String(128), primary_key=True)
    store = Column(String(32), primary_key=True)   # 'todos' | 'vfs'
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class FailureLedgerModel(Base):
    """Recurring tool/run failure fingerprints for governance conversion.

    A fingerprint recurring ``count >= settings.governance_convert_threshold``
    times is eligible for conversion into a durable control (eval fixture,
    policy rule, selftest check) — see
    ``app.core.improvement.analyzer.GovernanceConverter`` and migration
    ``030_governance_and_trajectory.sql``. Inert until that feature is used;
    off by default has zero rows.
    """
    __tablename__ = "failure_ledger"

    fingerprint = Column(String(80), primary_key=True)
    first_seen = Column(DateTime(timezone=True), default=_utcnow)
    last_seen = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    count = Column(Integer, default=1)
    sample_execution_ids = Column(JSON, default=list)
    status = Column(String(16), default="open")  # open | converted | dismissed
    control_ref = Column(Text, nullable=True)


class TrajectoryEventModel(Base):
    """Typed, step-granular trajectory event (one per model turn / tool call).

    Recorded alongside the existing flat ``executions.trajectory`` JSON blob
    by ``app.harness.step_recorder`` when ``settings.step_events_enabled`` is
    on. See migration ``030_governance_and_trajectory.sql``.
    """
    __tablename__ = "trajectory_events"

    event_id = Column(String(32), primary_key=True)
    trace_id = Column(String(128), nullable=False)   # execution_id or subagent span
    span_id = Column(String(64), nullable=True)       # NULL = root run
    step_index = Column(Integer, nullable=False)
    ts = Column(DateTime(timezone=True), default=_utcnow)
    type = Column(String(24), nullable=False)         # model_turn | tool_call | lifecycle | ...
    payload = Column(JSON, nullable=False)

    __table_args__ = (
        Index("idx_trajectory_events_trace", "trace_id", "step_index"),
    )
