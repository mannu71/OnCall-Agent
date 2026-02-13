@echo off
REM Agent API Launcher Script for Windows

SET COMMAND=%1

IF "%COMMAND%"=="" SET COMMAND=help

IF "%COMMAND%"=="start" (
    echo Starting Agent API...
    docker-compose up -d
    echo Agent API started!
    echo Access the API at: http://localhost:8000
    echo View docs at: http://localhost:8000/docs
    GOTO :EOF
)

IF "%COMMAND%"=="stop" (
    echo Stopping Agent API...
    docker-compose down
    echo Agent API stopped!
    GOTO :EOF
)

IF "%COMMAND%"=="restart" (
    echo Restarting Agent API...
    docker-compose restart
    echo Agent API restarted!
    GOTO :EOF
)

IF "%COMMAND%"=="logs" (
    docker-compose logs -f
    GOTO :EOF
)

IF "%COMMAND%"=="build" (
    echo Building Agent API...
    docker-compose build
    echo Build complete!
    GOTO :EOF
)

IF "%COMMAND%"=="dev" (
    echo Starting Agent API in development mode...
    
    IF NOT EXIST "venv" (
        echo Creating virtual environment...
        python -m venv venv
    )
    
    call venv\Scripts\activate.bat
    
    IF NOT EXIST "venv\.installed" (
        echo Installing dependencies...
        pip install -r requirements.txt
        type nul > venv\.installed
    )
    
    echo Starting application...
    python -m app.main
    GOTO :EOF
)

IF "%COMMAND%"=="test" (
    echo Running tests...
    
    IF NOT EXIST "venv" (
        echo Error: Virtual environment not found. Run 'run.bat dev' first.
        EXIT /B 1
    )
    
    call venv\Scripts\activate.bat
    pytest tests\ -v
    GOTO :EOF
)

IF "%COMMAND%"=="status" (
    curl -s http://localhost:8000/status
    GOTO :EOF
)

IF "%COMMAND%"=="health" (
    curl -s http://localhost:8000/health
    GOTO :EOF
)

IF "%COMMAND%"=="workflows" (
    curl -s http://localhost:8000/workflows
    GOTO :EOF
)

:help
echo Agent API - Workflow Automation Engine
echo.
echo Usage: run.bat [command]
echo.
echo Commands:
echo   start      - Start the API using Docker
echo   stop       - Stop the API
echo   restart    - Restart the API
echo   logs       - View API logs
echo   build      - Build Docker image
echo   dev        - Run in development mode (local)
echo   test       - Run tests
echo   status     - Get API status
echo   health     - Get health check
echo   workflows  - List all workflows
echo   help       - Show this help message
echo.
