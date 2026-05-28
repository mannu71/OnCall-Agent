-- migrations/006_code_crawler_v1.sql
-- Code Crawler v1.0 schema.
-- Run after 005_code_analyzer_v1.sql.
--
-- Adds three tables for the crawler pipeline:
--   • repo_abstractions  — cached LLM overview per indexed repo
--   • flow_runs          — per-invocation trace + timing for every crawler flow
--   • llm_cache          — prompt-level Postgres cache (keyed by sha256 hash)
--
-- NOTE: memory_summaries was already created in 005_code_analyzer_v1.sql.
--       It is intentionally omitted here.
--
-- All statements are idempotent (IF NOT EXISTS).

-- ─────────────────────────────────────────────────────────────────────────────
-- repo_abstractions
-- One row per indexed repository. The ``overview`` JSONB holds the
-- LLM-extracted abstraction map used by all crawler query flows.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS repo_abstractions (
    repo_name        VARCHAR(255) PRIMARY KEY,
    overview         JSONB         NOT NULL,
    -- overview shape:
    --   {
    --     "abstractions": [{"name": str, "summary": str, "file_indices": [int]}],
    --     "relationships": [{"from": str, "to": str, "label": str}],
    --     "file_map": {"abstraction_name": ["relative/path.py"]},
    --     "mermaid": "graph TD\n  ..."
    --   }
    files_indexed    INTEGER       NOT NULL,
    files_sha256     CHAR(64)      NOT NULL,
    model_id         VARCHAR(255),
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    generated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_repo_abstractions_generated
    ON repo_abstractions (generated_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- flow_runs
-- One row per crawler tool invocation. The ``trace`` JSONB contains the
-- per-node timing and token usage for replay and eval.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS flow_runs (
    id           BIGSERIAL     PRIMARY KEY,
    flow_name    VARCHAR(64)   NOT NULL,
    session_id   VARCHAR(64),
    repo_name    VARCHAR(255),
    inputs       JSONB         NOT NULL,
    response     JSONB,
    trace        JSONB         NOT NULL DEFAULT '[]',
    -- trace shape:
    --   [{"node": str, "ms": int, "ok": bool,
    --     "llm_calls": int, "tokens_in": int, "tokens_out": int,
    --     "cached": bool}]
    success      BOOLEAN       NOT NULL DEFAULT FALSE,
    error        TEXT,
    started_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    duration_ms  INTEGER
);

CREATE INDEX IF NOT EXISTS ix_flow_runs_session
    ON flow_runs (session_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_flow_runs_flow
    ON flow_runs (flow_name, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_flow_runs_repo
    ON flow_runs (repo_name, started_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- llm_cache
-- Prompt-level cache. Key is sha256(model_id || '\0' || prompt_text) so
-- cache entries are isolated per model.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS llm_cache (
    prompt_sha256  CHAR(64)      PRIMARY KEY,
    model_id       VARCHAR(255)  NOT NULL,
    response       TEXT          NOT NULL,
    tokens_in      INTEGER,
    tokens_out     INTEGER,
    cached_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_hit_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    hits           INTEGER       NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_llm_cache_model_cached
    ON llm_cache (model_id, cached_at DESC);
