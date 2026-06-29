-- migrations/005_code_analyzer_v1.sql
-- Code Analyzer v1.0 schema additions.
-- Run after 004_atropos.sql.
--
-- Covers:
--   • knowledge_entries (renamed from known_issues) + skills  — runtime models
--   • use_for_embeddings column on llm_configs               — Settings embedder flag
--   • code_chunks v1 columns                                 — body_sha256, BM25 vector, tree-sitter fields
--   • code_symbols / code_references                         — SCIP layer
--   • code_trigrams                                          — GIN trigram inverted index
--   • memory_summaries                                       — per-session compaction store
--
-- All statements are idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- Run CONCURRENTLY indexes OUTSIDE a transaction block.

-- ─────────────────────────────────────────────────────────────────────────────
-- knowledge_entries  (renamed from known_issues in 001_initial_schema.sql)
-- ─────────────────────────────────────────────────────────────────────────────
-- Rename the legacy table if it still exists under the old name.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'known_issues'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'knowledge_entries'
    ) THEN
        ALTER TABLE known_issues RENAME TO knowledge_entries;
    END IF;
END;
$$;

-- Create fresh if neither the old nor new name exists yet.
CREATE TABLE IF NOT EXISTS knowledge_entries (
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

CREATE INDEX IF NOT EXISTS ix_knowledge_entries_source   ON knowledge_entries (source);
CREATE INDEX IF NOT EXISTS ix_knowledge_entries_category ON knowledge_entries (category, created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- skills  — structured, reusable resolution procedures
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id               SERIAL PRIMARY KEY,
    name             VARCHAR(255) UNIQUE NOT NULL,
    title            VARCHAR(255) NOT NULL,
    description      TEXT,
    trigger_patterns JSONB,
    steps            JSONB NOT NULL DEFAULT '[]'::jsonb,
    workflow_name    VARCHAR(255),
    source           VARCHAR(50) DEFAULT 'distilled',
    status           VARCHAR(50) DEFAULT 'active',
    success_count    INTEGER DEFAULT 0,
    recall_count     INTEGER DEFAULT 0,
    last_used_at     TIMESTAMPTZ,
    promoted_from_id INTEGER,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- llm_configs — embedder-selection flag
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE llm_configs
    ADD COLUMN IF NOT EXISTS use_for_embeddings BOOLEAN DEFAULT FALSE;

-- ─────────────────────────────────────────────────────────────────────────────
-- code_chunks v1 — extend 003_code_intelligence.sql table with v1 columns
-- ─────────────────────────────────────────────────────────────────────────────
-- skip-if-unchanged gate
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS body_sha256      VARCHAR(64);
-- Postgres full-text BM25 tsvector
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS search_vector    TSVECTOR;
-- tree-sitter / AST enrichment
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS parent_class     VARCHAR(255);
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS decorators       JSONB DEFAULT '[]'::jsonb;
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS param_arity      INTEGER DEFAULT 0;
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS complexity       INTEGER DEFAULT 1;
-- opaque entity id used for body-fetch handle construction
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS entity_id        VARCHAR(16);
-- two-phase indexing marker: 'raw' | 'enriched'
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS enrichment_phase VARCHAR(10) DEFAULT 'raw';

CREATE INDEX IF NOT EXISTS ix_code_chunks_body_sha256 ON code_chunks (body_sha256);
CREATE INDEX IF NOT EXISTS ix_code_chunks_search_vec  ON code_chunks USING GIN (search_vector);

-- NOTE on embedding dimension:
-- 003_code_intelligence.sql created code_chunks.embedding as vector(1536).
-- Titan v2 (new recommended default) uses 1024 dimensions.
-- Fresh installs: change the vector(1536) in 003 to vector(1024) before first run.
-- Existing deployments that want to migrate 1536→1024:
--   1. SET embedding = NULL for all rows.
--   2. ALTER TABLE code_chunks ALTER COLUMN embedding TYPE vector(1024);
--   3. Re-index all repos from the Settings page.

-- ─────────────────────────────────────────────────────────────────────────────
-- code_symbols  — SCIP compiler-grade symbol metadata
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS code_symbols (
    id          BIGSERIAL PRIMARY KEY,
    repo_name   VARCHAR(255) NOT NULL,
    symbol_id   VARCHAR(1024) NOT NULL,
    kind        VARCHAR(50),
    file_path   VARCHAR(1024) NOT NULL,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER,
    signature   TEXT,
    language    VARCHAR(50),
    indexed_at  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT  uq_code_symbols_repo_symbol UNIQUE (repo_name, symbol_id)
);

CREATE INDEX IF NOT EXISTS ix_code_symbols_file ON code_symbols (repo_name, file_path);

-- ─────────────────────────────────────────────────────────────────────────────
-- code_references  — definition / reference / import occurrences
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS code_references (
    id            BIGSERIAL PRIMARY KEY,
    repo_name     VARCHAR(255) NOT NULL,
    symbol_id     VARCHAR(1024) NOT NULL,
    file_path     VARCHAR(1024) NOT NULL,
    line          INTEGER NOT NULL,
    role          VARCHAR(20),        -- 'definition' | 'reference' | 'import'
    caller_symbol VARCHAR(1024)
);

CREATE INDEX IF NOT EXISTS ix_code_refs_lookup ON code_references (repo_name, symbol_id, role);
CREATE INDEX IF NOT EXISTS ix_code_refs_caller ON code_references (caller_symbol)
    WHERE caller_symbol IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- code_trigrams  — GIN trigram inverted index (substring/regex retrieval)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS code_trigrams (
    chunk_id  BIGINT PRIMARY KEY,
    repo_name VARCHAR(255) NOT NULL,
    trigrams  BYTEA[] NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_code_trigrams_repo ON code_trigrams (repo_name);
-- GIN over the bytea[] array — the posting-list lookup hot path.
CREATE INDEX IF NOT EXISTS ix_code_trigrams_gin  ON code_trigrams USING GIN (trigrams array_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- memory_summaries  — ContextCompactionManager per-session persistence
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memory_summaries (
    session_id VARCHAR(64) PRIMARY KEY,
    summary    JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
