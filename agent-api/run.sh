#!/bin/bash

# Agent API Launcher Script

set -e

COMMAND=${1:-help}

case $COMMAND in
  start)
    echo "Starting Agent API..."
    docker-compose up -d
    echo "Agent API started!"
    echo "Access the API at: http://localhost:8000"
    echo "View docs at: http://localhost:8000/docs"
    ;;
  
  stop)
    echo "Stopping Agent API..."
    docker-compose down
    echo "Agent API stopped!"
    ;;
  
  restart)
    echo "Restarting Agent API..."
    docker-compose restart
    echo "Agent API restarted!"
    ;;
  
  logs)
    docker-compose logs -f
    ;;
  
  build)
    echo "Building Agent API..."
    docker-compose build
    echo "Build complete!"
    ;;
  
  dev)
    echo "Starting Agent API in development mode..."
    if [ ! -d "venv" ]; then
      echo "Creating virtual environment..."
      python -m venv venv
    fi
    
    source venv/bin/activate
    
    if [ ! -f "venv/.installed" ]; then
      echo "Installing dependencies..."
      pip install -r requirements.txt
      touch venv/.installed
    fi
    
    echo "Starting application..."
    python -m app.main
    ;;
  
  test)
    echo "Running tests..."
    if [ ! -d "venv" ]; then
      echo "Error: Virtual environment not found. Run './run.sh dev' first."
      exit 1
    fi
    
    source venv/bin/activate
    pytest tests/ -v
    ;;
  
  status)
    curl -s http://localhost:8000/status | jq
    ;;
  
  health)
    curl -s http://localhost:8000/health | jq
    ;;
  
  workflows)
    curl -s http://localhost:8000/workflows | jq
    ;;
  
  *)
    echo "Agent API - Workflow Automation Engine"
    echo ""
    echo "Usage: ./run.sh [command]"
    echo ""
    echo "Commands:"
    echo "  start      - Start the API using Docker"
    echo "  stop       - Stop the API"
    echo "  restart    - Restart the API"
    echo "  logs       - View API logs"
    echo "  build      - Build Docker image"
    echo "  dev        - Run in development mode (local)"
    echo "  test       - Run tests"
    echo "  status     - Get API status"
    echo "  health     - Get health check"
    echo "  workflows  - List all workflows"
    echo "  help       - Show this help message"
    echo ""
    ;;
esac
