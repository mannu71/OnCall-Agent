-- 030_governance_and_trajectory.sql
-- Failure->governance conversion ledger + step-level trajectory events.
--
-- Two independent, currently-inert additions (nothing writes to either table
-- until the corresponding feature flag is enabled — see app/config.py
-- governance_convert_threshold / step_events_enabled):
--
-- 1. failure_ledger: recurring tool/run failures, fingerprinted the same way
--    app.core.improvement.analyzer already does (regex digits->'#'), tracked
--    across analyze runs so a fingerprint recurring >= governance_convert_
--    threshold times can be converted into a durable control (eval fixture,
--    policy rule, selftest check) instead of just being patched once. See
--    app.core.improvement.analyzer.GovernanceConverter.
--
-- 2. trajectory_events: typed, step-granular events (one per model turn /
--    tool call) recorded alongside the existing flat executions.trajectory
--    JSON blob — observation/hidden_state/action/outcome/reward/meta per
--    step, with reward.late_bound appendable after the step (supervisor
--    score, eval judge, ...). Offline-RL-export-ready; see
--    app.harness.step_recorder and app.services.trajectory_service.

CREATE TABLE IF NOT EXISTS failure_ledger (
    fingerprint          VARCHAR(80)  PRIMARY KEY,
    first_seen           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    count                INTEGER      NOT NULL DEFAULT 1,
    sample_execution_ids JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status               VARCHAR(16)  NOT NULL DEFAULT 'open',  -- open | converted | dismissed
    control_ref          TEXT
);

CREATE TABLE IF NOT EXISTS trajectory_events (
    event_id   VARCHAR(32)  PRIMARY KEY,
    trace_id   VARCHAR(128) NOT NULL,   -- execution_id (root run) or subagent span
    span_id    VARCHAR(64),             -- NULL = root run; else subagent span id
    step_index INTEGER      NOT NULL,
    ts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    type       VARCHAR(24)  NOT NULL,   -- model_turn | tool_call | lifecycle | ...
    payload    JSONB        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trajectory_events_trace
    ON trajectory_events (trace_id, step_index);
