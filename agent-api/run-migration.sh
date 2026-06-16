#!/bin/bash
# Apply all database migrations in order.
# Run this once on a fresh database, and again whenever a new migration is added.
#
# Usage:
#   ./run-migration.sh
#
# Environment variables (all have defaults):
#   DB_HOST       default: localhost
#   DB_PORT       default: 5432
#   DB_NAME       default: kycagent
#   DB_USER       default: kycuser
#   DB_PASSWORD   default: kycpassword

set -euo pipefail

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-kycagent}"
DB_USER="${DB_USER:-kycuser}"
DB_PASSWORD="${DB_PASSWORD:-kycpassword}"

PSQL="PGPASSWORD=${DB_PASSWORD} psql -h ${DB_HOST} -p ${DB_PORT} -U ${DB_USER} -d ${DB_NAME}"

echo "Applying migrations to ${DB_NAME} on ${DB_HOST}:${DB_PORT}"
echo "============================================================"

MIGRATIONS=(
  "migrations/001_initial_schema.sql"
  "migrations/002_add_token_columns.sql"
  "migrations/002_vector_indexes.sql"
  "migrations/003_code_intelligence.sql"
  "migrations/004_atropos.sql"
  "migrations/005_code_analyzer_v1.sql"
  "migrations/006_code_crawler_v1.sql"
  "migrations/007_drop_v1_code_analyzer.sql"
  "migrations/008_workflow_indexing_status.sql"
  "migrations/009_knowledge_graph.sql"
  "migrations/010_kg_domain_mapping.sql"
  "migrations/011_app_settings.sql"
  "migrations/012_tool_approvals.sql"
  "migrations/013_background_jobs.sql"
  "migrations/014_gateway_assignments.sql"
  "migrations/015_project_intelligence.sql"
)

for migration in "${MIGRATIONS[@]}"; do
  if [ -f "$migration" ]; then
    echo ""
    echo "→ $migration"
    eval "$PSQL -f '$migration'"
    echo "  ✅ done"
  else
    echo ""
    echo "  ⚠️  $migration not found — skipping"
  fi
done

echo ""
echo "============================================================"
echo "✅ All migrations applied."
