-- migrations/007_drop_v1_code_analyzer.sql
-- Drop the legacy v1 code-analyzer tables.
-- Run after 006_code_crawler_v1.sql.
--
-- Removes five tables that were created by 005_code_analyzer_v1.sql and
-- backed the SCIP + embedding + BM25 + trigram + reranker pipeline that has
-- been replaced by the crawler (006).
--
-- Each DROP uses CASCADE so dependent views / foreign keys (if any) are
-- removed automatically.  IF EXISTS makes the script safe to re-run.
--
-- Tables removed:
--   • code_chunks      — tokenised text chunks with embedding vectors
--   • code_trigrams    — GIN trigram index data
--   • code_symbols     — SCIP symbol definitions + metadata
--   • code_references  — SCIP cross-file references
--   • code_calls       — call-graph edges extracted by SCIP / tree-sitter
--
-- memory_summaries is intentionally NOT dropped — it is still used by the
-- compaction manager (app/core/memory/) and is orthogonal to code retrieval.

BEGIN;

DROP TABLE IF EXISTS code_chunks     CASCADE;
DROP TABLE IF EXISTS code_trigrams   CASCADE;
DROP TABLE IF EXISTS code_symbols    CASCADE;
DROP TABLE IF EXISTS code_references CASCADE;
DROP TABLE IF EXISTS code_calls      CASCADE;

COMMIT;
