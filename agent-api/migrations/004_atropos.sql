-- migrations/004_atropos.sql
-- Atropos trajectory table for structured RL fine-tuning data.
-- Writes happen OUTSIDE the main learn transaction so a logging
-- failure never fails the investigation.

CREATE TABLE IF NOT EXISTS atropos_trajectories (
    id                SERIAL PRIMARY KEY,
    investigation_id  VARCHAR(255) UNIQUE NOT NULL,
    workflow_name     VARCHAR(255),
    execution_id      VARCHAR(255),

    -- Inputs
    inputs            JSONB,   -- { log_group, time_window, service_name, trigger }

    -- Per-agent tool call traces
    agent_traces      JSONB,   -- { cloudwatch: [...], db: [...], code: [...] }

    -- Synthesis result
    synthesis         JSONB,   -- { root_cause, confidence_score, suggestions }

    -- HITL outcome
    outcome           JSONB,   -- { engineer_approved, engineer_notes }

    -- Cost
    total_cost_usd    FLOAT,

    recorded_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_atropos_investigation ON atropos_trajectories (investigation_id);
CREATE INDEX IF NOT EXISTS idx_atropos_workflow       ON atropos_trajectories (workflow_name, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_atropos_approved       ON atropos_trajectories ((outcome->>'engineer_approved'), recorded_at DESC);
