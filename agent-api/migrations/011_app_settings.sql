-- Migration 011: application-wide key/value settings store.
--
-- Backs runtime-editable settings (e.g. the global timezone configured from
-- the Settings page). Keep this a small, generic key/value table so future
-- scalar app settings can reuse it without further migrations.

CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(128) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Seed the global timezone with UTC so reads have a deterministic default
-- before the operator first changes it. ON CONFLICT keeps re-runs idempotent.
INSERT INTO app_settings (key, value, updated_at)
VALUES ('global_timezone', 'UTC', now())
ON CONFLICT (key) DO NOTHING;
