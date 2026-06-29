-- migrations/003_code_intelligence.sql
-- Code intelligence layer tables.
-- Stores AST-extracted code chunks + call graph edges + repo registry.
-- Run after 002_vector_indexes.sql.

-- ─────────────────────────────────────────────────────────────────────────────
-- code_repos — registry of indexed repositories
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS code_repos (
    id           SERIAL PRIMARY KEY,
    repo_name    VARCHAR(255) UNIQUE NOT NULL,
    clone_url    VARCHAR(1000),
    local_path   VARCHAR(1000),
    language     VARCHAR(50),           -- 'python' | 'typescript' | 'mixed'
    last_indexed TIMESTAMPTZ,
    commit_hash  VARCHAR(64),
    status       VARCHAR(20) DEFAULT 'pending',  -- 'pending' | 'indexed' | 'error'
    error_msg    TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- code_chunks — individual function/class bodies with embeddings
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS code_chunks (
    id          SERIAL PRIMARY KEY,
    repo_name   VARCHAR(255) NOT NULL,
    file_path   VARCHAR(1000) NOT NULL,
    name        VARCHAR(255) NOT NULL,       -- function/class name
    chunk_type  VARCHAR(50) NOT NULL,        -- 'function' | 'class' | 'method'
    signature   TEXT,                        -- def foo(x: int) -> str
    body        TEXT NOT NULL,
    docstring   TEXT,
    language    VARCHAR(50) NOT NULL,        -- 'python' | 'typescript'
    line_start  INTEGER,
    line_end    INTEGER,
    embedding   vector(1536),
    indexed_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (repo_name, file_path, name, chunk_type)
);

-- Approximate-nearest-neighbour index for semantic search
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_code_chunks_embedding
    ON code_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Btree indexes for exact lookups
CREATE INDEX IF NOT EXISTS idx_code_chunks_repo_name  ON code_chunks (repo_name);
CREATE INDEX IF NOT EXISTS idx_code_chunks_name       ON code_chunks (name, repo_name);
CREATE INDEX IF NOT EXISTS idx_code_chunks_file       ON code_chunks (file_path, repo_name);

-- ─────────────────────────────────────────────────────────────────────────────
-- code_calls — static call graph edges (caller → callee)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS code_calls (
    id           SERIAL PRIMARY KEY,
    caller_chunk INTEGER REFERENCES code_chunks(id) ON DELETE CASCADE,
    callee_name  VARCHAR(255) NOT NULL,   -- may not exist in the index (external libs)
    callee_chunk INTEGER REFERENCES code_chunks(id) ON DELETE SET NULL,
    call_count   INTEGER DEFAULT 1,
    repo_name    VARCHAR(255) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_code_calls_caller  ON code_calls (caller_chunk);
CREATE INDEX IF NOT EXISTS idx_code_calls_callee  ON code_calls (callee_name, repo_name);
