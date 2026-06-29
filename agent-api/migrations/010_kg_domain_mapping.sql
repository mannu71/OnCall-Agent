-- migrations/010_kg_domain_mapping.sql
-- Add business domain mapping support to knowledge graph nodes.
-- Run after 009_knowledge_graph.sql.

ALTER TABLE kg_nodes
ADD COLUMN IF NOT EXISTS domain VARCHAR(128);

COMMENT ON COLUMN kg_nodes.domain IS 
'High-level business domain taxonomy classification (e.g. Payment System, User Auth)';

CREATE INDEX IF NOT EXISTS kg_nodes_repo_domain_idx
ON kg_nodes (repo_name, domain);
