-- 017_model_keys_multikey.sql
-- Multi-credential support for the Bedrock fallback-chain router (Phase 1).
--
-- Previously model_keys allowed exactly one row per provider (UNIQUE(provider)),
-- so there was no way to register multiple Bedrock credentials to rotate across
-- when one is throttled. This migration:
--   * drops the single-row-per-provider constraint,
--   * adds key_label / priority / enabled,
--   * makes (provider, key_label) the new uniqueness key,
--   * backfills existing rows with key_label='default'.
--
-- get_by_provider() keeps returning the highest-priority enabled key, so all
-- existing single-credential callers are unaffected.

ALTER TABLE model_keys
    ADD COLUMN IF NOT EXISTS key_label VARCHAR(100) NOT NULL DEFAULT 'default';

ALTER TABLE model_keys
    ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 100;

ALTER TABLE model_keys
    ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- Drop the old per-provider uniqueness (column-level UNIQUE => *_provider_key,
-- but an operator may also have created a bare unique index). Defensive on both.
ALTER TABLE model_keys DROP CONSTRAINT IF EXISTS model_keys_provider_key;
DROP INDEX IF EXISTS model_keys_provider_key;

-- New uniqueness: one row per (provider, key_label).
ALTER TABLE model_keys
    ADD CONSTRAINT model_keys_provider_label_key UNIQUE (provider, key_label);

-- Selector lookups order by (provider, enabled, priority).
CREATE INDEX IF NOT EXISTS idx_model_keys_provider_priority
    ON model_keys (provider, enabled, priority);
