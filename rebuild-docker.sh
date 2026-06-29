#!/bin/bash
# Rebuild and restart all services (data-safe — postgres volume is preserved).
set -e

if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker daemon is not running."
    exit 1
fi

echo "Building and starting all services..."
docker compose up --build -d --force-recreate

echo ""
docker compose ps
echo ""
echo "Open the app: http://localhost:3000"
echo "Backend API:  http://localhost:8000"
echo "Logs:         docker compose logs -f"
echo "Stop:         docker compose down"
echo ""
