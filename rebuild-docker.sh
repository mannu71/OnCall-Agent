#!/bin/bash
# Rebuild Docker image with new implementation (preserves data)
# This script rebuilds the agent-api container while keeping all data intact

set -e  # Exit on error

echo "========================================"
echo "Rebuilding Docker Image (Data Safe)"
echo "========================================"
echo ""

# Change to agent-api directory
cd agent-api

echo "[1/5] Stopping agent-api container (keeping database running)..."
docker-compose stop agent-api || echo "Warning: Failed to stop agent-api container. It may not be running."
echo ""

echo "[2/5] Removing old agent-api container..."
docker-compose rm -f agent-api || echo "Warning: Failed to remove agent-api container. It may not exist."
echo ""

echo "[3/5] Building new Docker image with updated code..."
docker-compose build agent-api
echo ""

echo "[4/5] Starting agent-api container with new image..."
docker-compose up -d agent-api
echo ""

echo "[5/5] Checking container status..."
sleep 3
docker-compose ps
echo ""

echo "========================================"
echo "Rebuild Complete!"
echo "========================================"
echo ""
echo "Data Status:"
echo "  - Database: PRESERVED (postgres_data volume)"
echo "  - Workflows: PRESERVED (./data directory)"
echo "  - Storage: PRESERVED (./data/storage)"
echo "  - Logs: PRESERVED (./data/logs)"
echo ""
echo "View logs: docker-compose logs -f agent-api"
echo "Stop all: docker-compose down"
echo ""

cd ..
