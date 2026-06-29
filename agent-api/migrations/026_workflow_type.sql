-- Add type column to workflows table (standard | agent)
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS type VARCHAR(20) NOT NULL DEFAULT 'workflow';

-- Backfill: rows that have an agent node but no schedule/scheduler node are agentic processes
UPDATE workflows
SET type = 'agent'
WHERE type = 'workflow'
  AND nodes IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM jsonb_array_elements(nodes::jsonb) n
      WHERE n->>'type' = 'agent'
  )
  AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(nodes::jsonb) n
      WHERE n->>'type' IN ('schedule', 'scheduler')
  );
