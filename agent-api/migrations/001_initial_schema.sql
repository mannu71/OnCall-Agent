-- migrations/001_initial_schema.sql
-- Base schema for all application tables.
-- Run once on a fresh PostgreSQL database.
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────────────────────
-- Workflows
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workflows (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    nodes       JSONB NOT NULL,
    edges       JSONB NOT NULL,
    viewport    JSONB,
    enabled     BOOLEAN DEFAULT TRUE,
    schedule    VARCHAR(100),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Execution history
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS executions (
    id           SERIAL PRIMARY KEY,
    workflow_id  INTEGER,
    workflow_name VARCHAR(255) NOT NULL,
    status       VARCHAR(50) NOT NULL DEFAULT 'pending',
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms  INTEGER,
    input        JSONB,
    output       JSONB,
    error        TEXT,
    logs         JSONB,
    trajectory   JSONB
);

CREATE INDEX IF NOT EXISTS idx_executions_workflow_name ON executions (workflow_name);
CREATE INDEX IF NOT EXISTS idx_executions_status        ON executions (status, started_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- LLM configurations
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS llm_configs (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) UNIQUE NOT NULL,
    provider    VARCHAR(100) NOT NULL,
    model       VARCHAR(255) NOT NULL,
    endpoint    VARCHAR(500),
    base_url    VARCHAR(500),
    temperature FLOAT DEFAULT 0.7,
    max_tokens  INTEGER DEFAULT 4096,
    region      VARCHAR(50) DEFAULT 'us-east-1',
    icon        VARCHAR(10),
    description TEXT,
    aws_profile VARCHAR(100),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- API keys
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS model_keys (
    id                SERIAL PRIMARY KEY,
    provider          VARCHAR(100) UNIQUE NOT NULL,
    api_key           VARCHAR(500),
    secret_key        VARCHAR(500),
    endpoint          VARCHAR(500),
    region            VARCHAR(50),
    access_key_id     VARCHAR(500),
    secret_access_key VARCHAR(500),
    session_token     VARCHAR(500),
    description       TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- MCP server configurations
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) UNIQUE NOT NULL,
    command     VARCHAR(500) NOT NULL,
    args        JSONB,
    env         JSONB,
    enabled     BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- RAG / vector tables
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS log_patterns (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(255) NOT NULL,
    pattern      TEXT NOT NULL,
    pattern_type VARCHAR(50) NOT NULL,
    severity     INTEGER DEFAULT 1,
    description  TEXT,
    embedding    vector(1536),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS known_issues (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    symptoms    JSONB,
    solution    TEXT,
    category    VARCHAR(100),
    source      VARCHAR(50) DEFAULT 'manual',
    embedding   vector(1536),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Metrics and alerts
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS baseline_metrics (
    id                  SERIAL PRIMARY KEY,
    metric_name         VARCHAR(255) NOT NULL,
    log_group           VARCHAR(255) NOT NULL,
    normal_range_min    FLOAT,
    normal_range_max    FLOAT,
    threshold_warning   FLOAT,
    threshold_critical  FLOAT,
    time_window         VARCHAR(50),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS analysis_history (
    id              SERIAL PRIMARY KEY,
    log_group       VARCHAR(255) NOT NULL,
    analysis_type   VARCHAR(100) NOT NULL,
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ NOT NULL,
    summary         TEXT,
    anomalies_found INTEGER DEFAULT 0,
    patterns_matched INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alerts (
    id          SERIAL PRIMARY KEY,
    log_group   VARCHAR(255) NOT NULL,
    alert_type  VARCHAR(100) NOT NULL,
    severity    VARCHAR(20) NOT NULL,
    message     TEXT NOT NULL,
    details     JSONB,
    status      VARCHAR(20) DEFAULT 'new',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(255)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Pattern memory (self-learning loop)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pattern_memory (
    id                SERIAL PRIMARY KEY,
    error_signature   VARCHAR(500) UNIQUE NOT NULL,  -- deterministic hash / key phrase
    resolution        TEXT NOT NULL,
    confidence_score  FLOAT NOT NULL DEFAULT 0.6,
    occurrence_count  INTEGER NOT NULL DEFAULT 1,
    last_seen         TIMESTAMPTZ DEFAULT NOW(),
    curator_status    VARCHAR(20) DEFAULT 'active',  -- 'active' | 'stale' | 'archive'
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pattern_memory_signature    ON pattern_memory (error_signature);
CREATE INDEX IF NOT EXISTS idx_pattern_memory_confidence   ON pattern_memory (confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_pattern_memory_curator      ON pattern_memory (curator_status, last_seen);
