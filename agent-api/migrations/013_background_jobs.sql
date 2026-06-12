-- 013_background_jobs.sql
-- Durable record of background jobs (currently repo indexing).
--
-- Replaces "fire-and-forget asyncio task + indexing_status flag on the workflow
-- row" as the source of truth for background work: each job has a lifecycle
-- (queued → running → completed/failed), live progress + heartbeat, and a
-- ``claimed_by`` column so a future multi-worker deployment can claim jobs with
-- ``SELECT ... FOR UPDATE SKIP LOCKED`` without double-running them.

CREATE TABLE IF NOT EXISTS background_jobs (
    id           SERIAL PRIMARY KEY,
    job_type     VARCHAR(50)  NOT NULL,            -- e.g. 'repo_index'
    target       VARCHAR(255) NOT NULL,            -- e.g. workflow name
    status       VARCHAR(20)  NOT NULL DEFAULT 'queued', -- queued|running|completed|failed
    progress     INTEGER      NOT NULL DEFAULT 0,  -- units done
    total        INTEGER      NOT NULL DEFAULT 0,  -- units total (0 = unknown)
    detail       TEXT,                             -- current step / last message
    error        TEXT,
    payload      JSONB,                            -- job inputs (repos, model_id, …)
    claimed_by   VARCHAR(64),                      -- worker/process id (multi-replica)
    heartbeat_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_background_jobs_status ON background_jobs (status);
CREATE INDEX IF NOT EXISTS ix_background_jobs_type_target ON background_jobs (job_type, target);
