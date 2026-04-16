-- Initialize pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================
-- WORKFLOWS & EXECUTION TABLES
-- ============================================

-- Workflows table
CREATE TABLE IF NOT EXISTS workflows (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    nodes JSONB NOT NULL,
    edges JSONB NOT NULL,
    viewport JSONB,
    enabled BOOLEAN DEFAULT true,
    schedule VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Workflow executions table
CREATE TABLE IF NOT EXISTS executions (
    id SERIAL PRIMARY KEY,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE CASCADE,
    workflow_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    input JSONB,
    output JSONB,
    error TEXT,
    logs TEXT
);

-- ============================================
-- LLM CONFIGURATION TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS llm_configs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    provider VARCHAR(100) NOT NULL,
    model VARCHAR(255) NOT NULL,
    endpoint VARCHAR(500),
    base_url VARCHAR(500),
    temperature FLOAT DEFAULT 0.7,
    max_tokens INTEGER DEFAULT 4096,
    region VARCHAR(50) DEFAULT 'us-east-1',
    icon VARCHAR(10),
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- MCP SERVER CONFIGURATION TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS mcp_servers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    command VARCHAR(500) NOT NULL,
    args JSONB,
    env JSONB,
    enabled BOOLEAN DEFAULT true,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- LOG ANALYSIS TABLES (for RAG)
-- ============================================

-- Log patterns table for storing known log patterns
CREATE TABLE IF NOT EXISTS log_patterns (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    pattern TEXT NOT NULL,
    pattern_type VARCHAR(50) NOT NULL,
    severity INTEGER DEFAULT 1,
    description TEXT,
    embedding vector(1536),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Known issues table for storing known issues and solutions
CREATE TABLE IF NOT EXISTS known_issues (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    symptoms TEXT[],
    solution TEXT,
    category VARCHAR(100),
    embedding vector(1536),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Baseline metrics table for storing normal behavior baselines
CREATE TABLE IF NOT EXISTS baseline_metrics (
    id SERIAL PRIMARY KEY,
    metric_name VARCHAR(255) NOT NULL,
    log_group VARCHAR(255) NOT NULL,
    normal_range_min FLOAT,
    normal_range_max FLOAT,
    threshold_warning FLOAT,
    threshold_critical FLOAT,
    time_window VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(metric_name, log_group)
);

-- Analysis history table for tracking past analyses
CREATE TABLE IF NOT EXISTS analysis_history (
    id SERIAL PRIMARY KEY,
    log_group VARCHAR(255) NOT NULL,
    analysis_type VARCHAR(100) NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    summary TEXT,
    anomalies_found INTEGER DEFAULT 0,
    patterns_matched INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Alerts table for storing generated alerts
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    log_group VARCHAR(255) NOT NULL,
    alert_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    status VARCHAR(20) DEFAULT 'new',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolved_by VARCHAR(255)
);

-- ============================================
-- INDEXES FOR VECTOR SIMILARITY SEARCH
-- ============================================

CREATE INDEX IF NOT EXISTS log_patterns_embedding_idx ON log_patterns USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS known_issues_embedding_idx ON known_issues USING ivfflat (embedding vector_cosine_ops);

-- ============================================
-- INDEXES FOR FASTER QUERIES
-- ============================================

CREATE INDEX IF NOT EXISTS workflows_name_idx ON workflows(name);
CREATE INDEX IF NOT EXISTS workflows_enabled_idx ON workflows(enabled);
CREATE INDEX IF NOT EXISTS workflows_schedule_idx ON workflows(schedule) WHERE schedule IS NOT NULL;
CREATE INDEX IF NOT EXISTS executions_workflow_id_idx ON executions(workflow_id);
CREATE INDEX IF NOT EXISTS executions_status_idx ON executions(status);
CREATE INDEX IF NOT EXISTS executions_started_at_idx ON executions(started_at);
CREATE INDEX IF NOT EXISTS log_patterns_type_idx ON log_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS known_issues_category_idx ON known_issues(category);
CREATE INDEX IF NOT EXISTS alerts_status_idx ON alerts(status);
CREATE INDEX IF NOT EXISTS alerts_created_at_idx ON alerts(created_at);
CREATE INDEX IF NOT EXISTS analysis_history_log_group_idx ON analysis_history(log_group);

-- ============================================
-- SCHEMA MIGRATIONS (idempotent ALTER TABLE statements)
-- ============================================

-- Phase 2.1: source provenance on known_issues (manual | agent | verified)
ALTER TABLE known_issues ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'manual';

-- Phase 3.1: full message trajectory on executions for analysis and training
ALTER TABLE executions ADD COLUMN IF NOT EXISTS trajectory JSONB;

-- Index for filtering agent-created entries
CREATE INDEX IF NOT EXISTS known_issues_source_idx ON known_issues(source);
