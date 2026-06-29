-- migrations/008_workflow_indexing_status.sql
-- Add indexing_status to workflows table.
-- Run after 007_drop_v1_code_analyzer.sql.
--
-- When a workflow with a codeAnalyzer node is saved, the backend fires a
-- background indexFlow job and sets:
--   enabled         = FALSE   (prevents execution while indexing)
--   indexing_status = 'indexing'
--
-- On job completion the backend sets:
--   enabled         = TRUE
--   indexing_status = NULL    (success) or 'indexing_failed: [repo]' (partial error)

ALTER TABLE workflows
  ADD COLUMN IF NOT EXISTS indexing_status VARCHAR(50) DEFAULT NULL;
