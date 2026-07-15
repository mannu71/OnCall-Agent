-- 034_drop_code_crawler.sql
-- Drop the legacy Python "Code Crawler" tables. codegraph (the surviving
-- code-intelligence backend) keeps its own per-project SQLite store under
-- ~/.cache/codegraph and uses NONE of these tables.
--
-- NOT dropped (still in use):
--   * llm_cache            — backs app.core.llm.call_llm's prompt cache
--   * semantic_memory      — the memory subsystem (migration 018/019)
--   * knowledge_entries    — auto-learn KB (migration 032)
--   * log_patterns         — CloudWatch pattern store (migration 019)
--   * workflows.indexing_status — the codegraph indexer sets it too (migration 008)
--
-- Migrations are applied manually (not auto-run on startup):
--   docker exec -i <postgres-container> psql -U <user> -d <db> \
--       < agent-api/migrations/034_drop_code_crawler.sql
-- Apply AFTER the new image is deployed (old code reads these; new code never does).

BEGIN;

-- The FTS trigger/function on kg_nodes (migration 009) — drop before the table.
DROP TRIGGER  IF EXISTS kg_nodes_fts_upd ON kg_nodes;
DROP FUNCTION IF EXISTS kg_nodes_fts_trigger();

-- Knowledge-graph tables (migrations 009/010) + project intelligence (015) +
-- repo abstractions / flow runs (006). CASCADE clears dependent FKs/indexes.
DROP TABLE IF EXISTS
    kg_unresolved_refs,
    kg_edges,
    kg_files,
    kg_nodes,
    repo_docs,
    repo_abstractions,
    flow_runs
CASCADE;

COMMIT;
