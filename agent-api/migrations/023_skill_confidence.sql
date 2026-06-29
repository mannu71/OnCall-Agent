-- 023_skill_confidence.sql
-- Self-evolving skills upgrade.
--
-- Adds a confidence score and an audit verdict to distilled skills so the
-- system can gate low-confidence skills as drafts (hidden from the agent until
-- proven) and let the curator demote/promote skills over time.
--
--   confidence    — 0..1, set by the distillation LLM. Below the configured
--                   threshold a freshly distilled skill is saved as 'draft'
--                   instead of 'active', so it is excluded from recall until
--                   audited or proven by use.
--   audit_verdict — last curator decision recorded against the skill
--                   (e.g. 'kept' | 'demoted' | 'promoted' | 'archived').

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS confidence    REAL,
    ADD COLUMN IF NOT EXISTS audit_verdict VARCHAR(16);

-- Backfill existing rows to a neutral-high confidence so they are unaffected
-- (they were already 'active' under the old auto-activate behaviour).
UPDATE skills SET confidence = 0.7 WHERE confidence IS NULL;
