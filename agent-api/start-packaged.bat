@echo off
REM Start script for FastAPI backend in packaged Electron app
REM This script is called by the Electron main process

cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    exit /b 1
)

REM Start the FastAPI server
echo Starting FastAPI server on port 8000...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
