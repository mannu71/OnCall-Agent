-- 032_knowledge_entries_title_unique.sql
-- Fix silent auto-learn KB-write failures.
--
-- Auto-learn's IncidentKBSink upserts knowledge with ON CONFLICT (title), but
-- `knowledge_entries` never had a UNIQUE constraint on `title` — the only
-- constraint is the leftover `known_issues_pkey` primary key (from the
-- known_issues→knowledge_entries rename). Every upsert therefore raised
-- InvalidColumnReferenceError, was swallowed by the sink, and no knowledge was
-- ever persisted. This adds the missing unique constraint so the upsert works.
-- Idempotent and safe to re-run.

-- 1. Remove duplicate titles, keeping the earliest (lowest id) row.
DELETE FROM knowledge_entries a
USING knowledge_entries b
WHERE a.title = b.title AND a.id > b.id;

-- 2. Add the unique constraint if it isn't already present.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'knowledge_entries'::regclass
          AND contype = 'u'
          AND conname = 'knowledge_entries_title_key'
    ) THEN
        ALTER TABLE knowledge_entries
            ADD CONSTRAINT knowledge_entries_title_key UNIQUE (title);
    END IF;
END $$;
