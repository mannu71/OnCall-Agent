-- 012_tool_approvals.sql
-- Server-side audit trail for human-in-the-loop tool approvals.
--
-- Each row records one "ask" tool gate (see
-- app/harness/tool_permissions.py): a pending row is written
-- before the agent blocks on operator approval, then updated with the decision
-- (approved / denied / timeout) when it resolves. This replaces the previous
-- browser-localStorage-only record so approvals are durable and auditable, and
-- visible across page reloads / other operators.

CREATE TABLE IF NOT EXISTS tool_approvals (
    id            SERIAL PRIMARY KEY,
    execution_id  VARCHAR(64)  NOT NULL,
    request_id    VARCHAR(64)  NOT NULL,
    tool_name     VARCHAR(255) NOT NULL,
    args_summary  TEXT,
    decision      VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending|approved|denied|timeout
    decided_by    VARCHAR(255),
    reason        TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ,
    CONSTRAINT uq_tool_approvals_request UNIQUE (execution_id, request_id)
);

CREATE INDEX IF NOT EXISTS ix_tool_approvals_execution
    ON tool_approvals (execution_id);

CREATE INDEX IF NOT EXISTS ix_tool_approvals_decision
    ON tool_approvals (decision);
