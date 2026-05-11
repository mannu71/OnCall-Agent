@echo off
REM Run database migration to add use_for_embeddings column

REM Load database connection from environment or use defaults
IF "%DB_HOST%"=="" SET DB_HOST=localhost
IF "%DB_PORT%"=="" SET DB_PORT=5432
IF "%DB_NAME%"=="" SET DB_NAME=kycagent
IF "%DB_USER%"=="" SET DB_USER=kycuser
IF "%DB_PASSWORD%"=="" SET DB_PASSWORD=kycpassword

echo Running migration: add use_for_embeddings to llm_configs
echo Database: %DB_NAME% on %DB_HOST%:%DB_PORT%
echo.

REM Run the migration
SET PGPASSWORD=%DB_PASSWORD%
psql -h %DB_HOST% -p %DB_PORT% -U %DB_USER% -d %DB_NAME% -f migrations\add_use_for_embeddings_to_llm_configs.sql

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo Migration completed successfully!
) ELSE (
    echo.
    echo Migration failed. Please check the error above.
    exit /b 1
)
