@echo off
REM Rebuild Docker images with new implementation (preserves data)
REM Rebuilds backend and UI containers separately via the root compose file

echo ========================================
echo Rebuilding Docker Images (Data Safe)
echo ========================================
echo.

echo [0/6] Building codegraph engine image...
docker build -t codegraph:latest ./codegraph
if %ERRORLEVEL% NEQ 0 (
    echo Error: Failed to build codegraph engine image!
    exit /b 1
)
echo.

echo [1/6] Stopping backend and UI containers (keeping database running)...
docker compose stop agent-api ui
if %ERRORLEVEL% NEQ 0 (
    echo Warning: Failed to stop containers. They may not be running.
)
echo.

echo [2/6] Removing old backend and UI containers...
docker compose rm -f agent-api ui
if %ERRORLEVEL% NEQ 0 (
    echo Warning: Failed to remove containers. They may not exist.
)
echo.

echo [3/6] Building new Docker images...
docker compose build agent-api ui
if %ERRORLEVEL% NEQ 0 (
    echo Error: Failed to build Docker images!
    exit /b 1
)
echo.

echo [4/6] Starting backend container...
docker compose up -d agent-api
if %ERRORLEVEL% NEQ 0 (
    echo Error: Failed to start backend container!
    exit /b 1
)
echo.

echo [5/6] Starting UI container...
docker compose up -d ui
if %ERRORLEVEL% NEQ 0 (
    echo Error: Failed to start UI container!
    exit /b 1
)
echo.

echo [6/6] Checking container status...
timeout /t 3 /nobreak >nul
docker compose ps
echo.

echo ========================================
echo Rebuild Complete!
echo ========================================
echo.
echo Containers:
echo   - kyc-agent-db   (postgres)
echo   - kyc-agent-api  (backend, :8000)
echo   - kyc-agent-ui   (frontend, :8080)
echo.
echo Open the app: http://localhost:8080
echo View backend logs: docker compose logs -f agent-api
echo View UI logs: docker compose logs -f ui
echo Stop all: docker compose down
echo.
