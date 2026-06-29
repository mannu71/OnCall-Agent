-- 019_embeddings_titan_v2.sql
-- Switch all embedding columns from Titan V1 (1536-dim) to Titan Text
-- Embeddings V2 (amazon.titan-embed-text-v2:0, 1024-dim).
--
-- V1 (titan-embed-text-v1) is NOT enabled in this Bedrock account, so the
-- embedding tables were never populated — every embedding insert/search
-- silently degraded. V2 IS enabled and outputs 256/512/1024 dims; we standardise
-- on 1024 (best quality).
--
-- All three embedding tables are empty, so we DROP+ADD the column (which cleanly
-- cascades the old dimension-bound ivfflat indexes) and recreate the index at the
-- new dimension. No data is lost. If a deployment somehow has V1 embeddings, they
-- would be invalid at 1024 anyway and must be re-embedded.

-- semantic_memory (migration 018) ------------------------------------------------
ALTER TABLE semantic_memory DROP COLUMN IF EXISTS embedding;
ALTER TABLE semantic_memory ADD COLUMN embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_semantic_memory_embedding
    ON semantic_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- knowledge_entries --------------------------------------------------------------
ALTER TABLE knowledge_entries DROP COLUMN IF EXISTS embedding;
ALTER TABLE knowledge_entries ADD COLUMN embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_embedding
    ON knowledge_entries USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- log_patterns -------------------------------------------------------------------
ALTER TABLE log_patterns DROP COLUMN IF EXISTS embedding;
ALTER TABLE log_patterns ADD COLUMN embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_log_patterns_embedding
    ON log_patterns USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
