-- migrations/002_add_token_columns.sql
-- Add token usage tracking columns to executions table.
-- Run after 001_initial_schema.sql.

ALTER TABLE executions ADD COLUMN IF NOT EXISTS input_tokens  INTEGER DEFAULT 0;
ALTER TABLE executions ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;
ALTER TABLE executions ADD COLUMN IF NOT EXISTS total_tokens  INTEGER DEFAULT 0;
