#!/bin/bash
# Run database migration to add use_for_embeddings column

# Load database connection from environment or use defaults
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-kycagent}"
DB_USER="${DB_USER:-kycuser}"

echo "Running migration: add use_for_embeddings to llm_configs"
echo "Database: $DB_NAME on $DB_HOST:$DB_PORT"
echo ""

# Run the migration
PGPASSWORD="${DB_PASSWORD:-kycpassword}" psql \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  -f migrations/add_use_for_embeddings_to_llm_configs.sql

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Migration completed successfully!"
else
    echo ""
    echo "❌ Migration failed. Please check the error above."
    exit 1
fi
