-- ============================================================================
-- 003_executions_started_at_index.sql  --  Index the execution history listing
-- ============================================================================
-- The execution list (`GET /api/v1/executions`, the Dashboard's 30-second poll)
-- runs `SELECT ... FROM executions ORDER BY started_at DESC LIMIT n` with no
-- WHERE clause. None of the existing indexes can serve that ordering:
--
--   * idx_executions_status (status, started_at DESC)  -- needs a status filter
--   * idx_executions_workflow_name (workflow_name)     -- needs a name filter
--   * idx_executions_chat_session (chat_session_id) WHERE NOT NULL
--
-- so Postgres seq-scans the whole table and top-N sorts it, every poll, growing
-- with history. Two indexes, matching the two shapes the endpoint issues:
--
--   1. the plain newest-first listing;
--   2. the same listing with `exclude_chat=true`, which the Dashboard uses so
--      its 24h/48h stat windows are computed over real workflow runs rather
--      than over whatever survived a client-side filter. A partial index keeps
--      this small — it covers only the rows that listing can return.
--
-- Both are additive and safe to re-run.
--
-- Apply via stdin (MSYS mangles a -f path):
--   docker exec -i kyc-agent-db psql -U kycuser -d kycagent < agent-api/migrations/003_executions_started_at_index.sql
-- ============================================================================

SET search_path TO public;

CREATE INDEX IF NOT EXISTS idx_executions_started_at
    ON public.executions USING btree (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_executions_workflow_runs
    ON public.executions USING btree (started_at DESC)
    WHERE chat_session_id IS NULL;

-- The per-workflow listing (`/workflows/{name}/executions`) filters by name and
-- orders by started_at; the name-only index leaves the sort unsupported.
CREATE INDEX IF NOT EXISTS idx_executions_workflow_name_started
    ON public.executions USING btree (workflow_name, started_at DESC);
