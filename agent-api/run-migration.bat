@echo off
REM Apply all database migrations in order.
REM Run this once on a fresh database, and again whenever a new migration is added.
REM
REM Usage:  run-migration.bat
REM
REM Environment variables (all have defaults):
REM   DB_HOST       default: localhost
REM   DB_PORT       default: 5432
REM   DB_NAME       default: kycagent
REM   DB_USER       default: kycuser
REM   DB_PASSWORD   default: kycpassword

IF "%DB_HOST%"==""     SET DB_HOST=localhost
IF "%DB_PORT%"==""     SET DB_PORT=5432
IF "%DB_NAME%"==""     SET DB_NAME=kycagent
IF "%DB_USER%"==""     SET DB_USER=kycuser
IF "%DB_PASSWORD%"=="" SET DB_PASSWORD=kycpassword

SET PGPASSWORD=%DB_PASSWORD%

echo Applying migrations to %DB_NAME% on %DB_HOST%:%DB_PORT%
echo ============================================================

CALL :run_migration "migrations\001_initial_schema.sql"
CALL :run_migration "migrations\002_add_token_columns.sql"
CALL :run_migration "migrations\002_vector_indexes.sql"
CALL :run_migration "migrations\003_code_intelligence.sql"
CALL :run_migration "migrations\004_atropos.sql"
CALL :run_migration "migrations\005_code_analyzer_v1.sql"
CALL :run_migration "migrations\006_code_crawler_v1.sql"
CALL :run_migration "migrations\007_drop_v1_code_analyzer.sql"
CALL :run_migration "migrations\008_workflow_indexing_status.sql"
CALL :run_migration "migrations\009_knowledge_graph.sql"
CALL :run_migration "migrations\010_kg_domain_mapping.sql"
CALL :run_migration "migrations\011_app_settings.sql"

echo.
echo ============================================================
echo All migrations applied.
EXIT /B 0

:run_migration
IF NOT EXIST %1 (
    echo.
    echo   WARNING: %1 not found - skipping
    EXIT /B 0
)
echo.
echo ^-^> %1
psql -h %DB_HOST% -p %DB_PORT% -U %DB_USER% -d %DB_NAME% -f %1
IF %ERRORLEVEL% NEQ 0 (
    echo   FAILED: %1
    EXIT /B 1
)
echo   done
EXIT /B 0
