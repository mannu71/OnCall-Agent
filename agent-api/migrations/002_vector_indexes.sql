-- migrations/002_vector_indexes.sql
-- Add ivfflat approximate-nearest-neighbour indexes for all vector columns.
--
-- Without these indexes every similarity search does a full table scan.
-- At 10,000 embeddings that takes ~8 s; at 100,000 it times out.
--
-- Run AFTER 001_initial_schema.sql.
-- Use CONCURRENTLY so production tables are not locked during index build.
-- NOTE: CONCURRENTLY cannot run inside an explicit transaction block.

-- ─────────────────────────────────────────────────────────────────────────────
-- known_issues — primary vector store for past-investigation recall
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_known_issues_embedding
    ON known_issues
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ─────────────────────────────────────────────────────────────────────────────
-- log_patterns — RAG store for log pattern matching
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_log_patterns_embedding
    ON log_patterns
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ─────────────────────────────────────────────────────────────────────────────
-- Composite btree indexes for common query patterns
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_known_issues_category   ON known_issues (category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_known_issues_source     ON known_issues (source);
CREATE INDEX IF NOT EXISTS idx_log_patterns_type_sev   ON log_patterns (pattern_type, severity DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- alerts — fast lookup for active/unresolved alerts
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_alerts_status_created ON alerts (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_log_group      ON alerts (log_group, created_at DESC);
