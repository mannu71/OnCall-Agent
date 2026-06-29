-- 018_semantic_memory.sql
-- Bank-scoped semantic memory.
--
-- Learned, operational memory captured WHILE operating the system (confirmed
-- root causes, symptom→fix, human-authoritative facts) — distinct from the
-- static, code-derived repo_docs (migration 015). Recall is hybrid FTS + vector
-- over the active repo bank ∪ the shared global bank.
--
-- Banks:
--   'repo'   — scoped to one repo_name (per-service memory)
--   'global' — repo_name IS NULL, visible to every investigation (cross-service
--              precedents, org-wide facts)

CREATE TABLE IF NOT EXISTS semantic_memory (
    id               BIGSERIAL    PRIMARY KEY,
    bank             VARCHAR(16)  NOT NULL DEFAULT 'repo',   -- 'repo' | 'global'
    repo_name        VARCHAR(255),                            -- NULL for global bank
    content          TEXT         NOT NULL,
    content_sha256   CHAR(64)     NOT NULL,
    embedding        vector(1024),                            -- Titan V2; NULL when FTS-only
    search_vector    tsvector,
    source           VARCHAR(32)  NOT NULL DEFAULT 'agent',   -- agent | manual | verified
    importance       REAL         NOT NULL DEFAULT 0.5,
    veracity         REAL         NOT NULL DEFAULT 0.5,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_recalled_at TIMESTAMPTZ,
    recall_count     INTEGER      NOT NULL DEFAULT 0
);

-- Dedup key. COALESCE so global rows (repo_name NULL) also dedupe — a bare
-- UNIQUE(bank, repo_name, content_sha256) would treat every NULL as distinct.
CREATE UNIQUE INDEX IF NOT EXISTS uq_semantic_memory_dedup
    ON semantic_memory (bank, COALESCE(repo_name, ''), content_sha256);

-- Vector leg (cosine). ivfflat needs an ops class; lists=100 is fine at this scale.
CREATE INDEX IF NOT EXISTS idx_semantic_memory_embedding
    ON semantic_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- FTS leg.
CREATE INDEX IF NOT EXISTS idx_semantic_memory_search
    ON semantic_memory USING gin (search_vector);

-- Bank scoping lookups.
CREATE INDEX IF NOT EXISTS idx_semantic_memory_bank_repo
    ON semantic_memory (bank, repo_name);
