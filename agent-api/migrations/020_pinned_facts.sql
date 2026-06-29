-- 020_pinned_facts.sql
-- Pinned-facts memory tier.
--
-- Reuses the existing semantic_memory store with a new bank value, 'pinned'.
-- Unlike 'repo'/'global' rows (similarity-gated recall), pinned rows are
-- ALWAYS injected into the agent's context every turn — the "pinned
-- facts" tier — subject to a per-turn token budget enforced in the app layer.
--
-- The `bank` column is an unconstrained VARCHAR(16), so 'pinned' needs no schema
-- change. This migration only adds a partial index so the always-on pinned
-- lookup (ORDER BY importance*veracity) stays cheap as the table grows.

CREATE INDEX IF NOT EXISTS idx_semantic_memory_pinned
    ON semantic_memory (repo_name, (importance * veracity) DESC)
    WHERE bank = 'pinned';
