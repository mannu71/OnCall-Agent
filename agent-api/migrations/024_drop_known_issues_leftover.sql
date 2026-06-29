-- 024_drop_known_issues_leftover.sql
-- Consolidate and remove the `known_issues` rename-leftover table.
--
-- `known_issues` was meant to be renamed to `knowledge_entries` (migration 005),
-- but on databases where an empty `knowledge_entries` already existed the RENAME
-- was skipped — leaving the app reading the (empty) `knowledge_entries` ORM table
-- while historical rows sat orphaned and unreadable in `known_issues`.
--
-- This migration is DATA-PRESERVING: if both tables exist, it first copies any
-- `known_issues` rows that are not already present in `knowledge_entries`
-- (matched on title) before dropping the leftover. Embeddings are intentionally
-- NOT copied — the column changed to vector(1024) in migration 019 and is
-- regenerated lazily on next recall/learn. Idempotent and safe to re-run.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'known_issues')
       AND EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_name = 'knowledge_entries')
    THEN
        INSERT INTO knowledge_entries
            (title, description, symptoms, solution, category, source, created_at, updated_at)
        SELECT ki.title, ki.description, ki.symptoms, ki.solution, ki.category,
               ki.source, ki.created_at, ki.updated_at
        FROM known_issues ki
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_entries ke WHERE ke.title = ki.title
        );
    END IF;
END $$;

DROP TABLE IF EXISTS known_issues CASCADE;
