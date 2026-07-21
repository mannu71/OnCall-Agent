-- ============================================================================
-- 002_fts_only_retrieval.sql  --  Drop the Bedrock text-embedding stack
-- ============================================================================
-- Knowledge/memory retrieval moved from Bedrock Titan embeddings (pgvector) to
-- Postgres full-text search over the OKF knowledge bundle + the semantic_memory
-- ``kb`` bank. This migration removes the now-dead embedding storage:
--
--   * semantic_memory.embedding column + its ivfflat index (recall is FTS-only;
--     the weighted search_vector/GIN index stays).
--   * knowledge_entries + log_patterns tables — consolidated into the OKF bundle
--     (data/knowledge/). Run scripts/migrate_knowledge_entries_to_okf.py FIRST to
--     port existing rows into OKF docs; the kb bank is rebuilt from the bundle at
--     startup (reconcile_kb_index).
--   * llm_configs.use_for_embeddings — no embedding model is configured anymore.
--
-- The ONNX code-search embeddings (app/core/code_semantic, SQLite-backed) and the
-- codegraph engine are UNRELATED and untouched. The pgvector extension is left
-- installed (harmless; nothing else depends on it).
--
-- Apply via stdin (MSYS mangles a -f path):
--   docker exec -i kyc-agent-db psql -U kycuser -d kycagent < agent-api/migrations/002_fts_only_retrieval.sql
-- Take a backup first — the DROPs are irreversible:
--   docker exec kyc-agent-db pg_dump -U kycuser kycagent > backup_pre_002.sql
-- ============================================================================

BEGIN;

-- Recall is FTS-only: drop the vector column + its ANN index.
DROP INDEX IF EXISTS public.idx_semantic_memory_embedding;
ALTER TABLE public.semantic_memory DROP COLUMN IF EXISTS embedding;

-- Consolidated into the OKF bundle; drop the legacy embedding-only tables.
DROP TABLE IF EXISTS public.knowledge_entries CASCADE;
DROP TABLE IF EXISTS public.log_patterns CASCADE;

-- No embedding model is configured anymore.
ALTER TABLE public.llm_configs DROP COLUMN IF EXISTS use_for_embeddings;

-- Force a clean rebuild of the kb bank from the bundle on next startup.
DELETE FROM public.semantic_memory WHERE bank = 'kb';

-- Track this migration (mirrors the 001 baseline stamp convention).
INSERT INTO public.schema_migrations (filename)
VALUES ('002_fts_only_retrieval.sql')
ON CONFLICT DO NOTHING;

COMMIT;
