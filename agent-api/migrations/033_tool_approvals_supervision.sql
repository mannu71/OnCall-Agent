-- 033_tool_approvals_supervision.sql
-- Action Supervisor columns on the tool-approval audit trail.
--
-- The Action Supervisor (app/core/supervision/action_supervisor.py) reviews
-- every intercepted write-class action before it executes. These columns record
-- its risk classification and advisory/decisive verdict alongside the human
-- decision already stored on the row, so the supervisor's calls stay auditable
-- and distinguishable from the operator's (decided_by).
--
-- Apply manually (migrations are not auto-run):
--   psql "$DATABASE_URL" -f migrations/033_tool_approvals_supervision.sql

ALTER TABLE tool_approvals
    ADD COLUMN IF NOT EXISTS risk_tier            VARCHAR(10),   -- low|high (null = unclassified)
    ADD COLUMN IF NOT EXISTS supervisor_verdict   VARCHAR(20),   -- approve|deny|escalate
    ADD COLUMN IF NOT EXISTS supervisor_reasoning TEXT;

CREATE INDEX IF NOT EXISTS ix_tool_approvals_risk_tier
    ON tool_approvals (risk_tier);
