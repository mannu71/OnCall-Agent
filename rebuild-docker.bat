@echo off
REM Rebuild and restart all services (data-safe -- postgres volume is preserved).

docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Error: Docker daemon is not running.
    exit /b 1
)

echo Building and starting all services...
docker compose up --build -d --force-recreate
if %ERRORLEVEL% NEQ 0 (
    echo Error: Build failed.
    exit /b 1
)

echo.
docker compose ps
echo.
echo Open the app: http://localhost:3000
echo Backend API:  http://localhost:8000
echo Logs:         docker compose logs -f
echo Stop:         docker compose down
echo.
