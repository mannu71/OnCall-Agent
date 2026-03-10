# Agent API

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)

## Project Summary

Agent API is a complete, production-ready workflow automation engine. All core features have been implemented:

### ✅ Completed Features

- **FastAPI Application** - Full REST API with automatic OpenAPI documentation
- **Workflow Management** - CRUD operations for YAML-based workflows
- **APScheduler Integration** - Cron-based background job scheduling
- **Task Execution** - Shell and Python task types with timeout and retry support
- **Real-time Streaming** - Server-Sent Events (SSE) for live execution monitoring
- **Persistent Storage** - File-based storage for workflows and execution logs
- **Docker Support** - Single-container deployment with docker-compose
- **Comprehensive Testing** - Unit tests for core components
- **Example Workflows** - Ready-to-use workflow templates
- **Complete Documentation** - README, Quick Start, and API examples

### 📁 Project Structure

```
agent-api/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Settings configuration
│   ├── models/
│   │   └── workflow.py         # Pydantic models
│   ├── routes/
│   │   ├── workflows.py        # Workflow endpoints
│   │   └── health.py           # Health check endpoints
│   └── services/
│       ├── storage.py          # YAML storage service
│       ├── scheduler.py        # APScheduler integration
│       └── executor.py         # Task executor
├── storage/
│   ├── workflows/              # Workflow definitions (YAML)
│   │   ├── hello-world.yaml
│   │   ├── python-example.yaml
│   │   └── daily-backup.yaml
│   └── logs/                   # Execution logs
├── tests/                      # Unit tests
├── docker/                     # Docker configuration
├── Dockerfile                  # Container definition
├── docker-compose.yml          # Docker compose config
├── requirements.txt            # Python dependencies
├── run.sh / run.bat           # Helper scripts
├── README.md                   # Main documentation
├── QUICKSTART.md              # Getting started guide
└── API_EXAMPLES.md            # API usage examples
```

---

## Quick Start

### Using Docker (Recommended)

```bash
# Start the API
docker-compose up -d

# View logs
docker-compose logs -f

# Access the API
open http://localhost:8000/docs
```

Or use the helper script:

```bash
# Linux/Mac
./run.sh start

# Windows
run.bat start
```

### Local Development

```bash
# Linux/Mac
./run.sh dev

# Windows
run.bat dev
```

---

## Usage Examples

### Create a Workflow

```bash
curl -X POST http://localhost:8000/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello-world",
    "description": "Simple test workflow",
    "schedule": "*/5 * * * *",
    "enabled": true,
    "tasks": [{
      "name": "greet",
      "type": "shell",
      "command": "echo \"Hello from Agent API!\""
    }]
  }'
```

### Execute Workflow

```bash
curl -X POST http://localhost:8000/workflows/hello-world/execute
```

### Stream Execution

```bash
curl -N http://localhost:8000/workflows/hello-world/stream
```

### View History

```bash
curl http://localhost:8000/workflows/hello-world/history | jq
```

See [API_EXAMPLES.md](API_EXAMPLES.md) for more examples.

---

## API Endpoints

### Workflows

- `GET /workflows` - List all workflows
- `GET /workflows/{name}` - Get workflow details
- `POST /workflows` - Create workflow
- `PUT /workflows/{name}` - Update workflow
- `DELETE /workflows/{name}` - Delete workflow
- `POST /workflows/{name}/execute` - Execute workflow
- `GET /workflows/{name}/stream` - Stream execution events (SSE)
- `GET /workflows/{name}/history` - Get execution history

### Health

- `GET /health` - Health check
- `GET /status` - Detailed system status

### Documentation

- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation

---

## Testing

Run the test suite:

```bash
# Using helper script
./run.sh test

# Or directly
pytest tests/ -v
```

---

## Configuration

Environment variables (`.env` file):

```env
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=false
STORAGE_PATH=./storage
SCHEDULER_TIMEZONE=UTC
MAX_CONCURRENT_WORKFLOWS=5
LOG_LEVEL=INFO
```

---

## Example Workflows

Three example workflows are included:

1. **hello-world.yaml** - Simple shell commands (runs every 5 minutes)
2. **python-example.yaml** - Python script execution (disabled by default)
3. **daily-backup.yaml** - Multi-task backup workflow (disabled by default)

Edit `enabled: true` in any workflow to activate it.

---

## Architecture

The application follows a clean architecture pattern:

- **FastAPI** - HTTP layer and API routing
- **Pydantic** - Data validation and serialization
- **APScheduler** - Background job scheduling
- **YAML Storage** - Simple, human-readable persistence
- **SSE** - Real-time event streaming

```
Client Request
    ↓
FastAPI Routes
    ↓
Pydantic Models (validation)
    ↓
Services Layer (business logic)
    ↓
Storage / Scheduler / Executor
    ↓
YAML Files / Background Jobs
```

---

## Development

### Adding a New Task Type

Extend `TaskExecutor` in `app/services/executor.py`:

```python
async def _execute_custom_task(self, task: Task):
    # Your custom task execution logic
    pass
```

Update `TaskType` enum in `app/models/workflow.py`.

### Running Tests

```bash
pytest tests/ -v --cov=app
```

### Code Quality

```bash
black app/
flake8 app/
mypy app/
```

---

## Deployment

### Production Considerations

1. Use a reverse proxy (nginx/Traefik) for SSL/TLS
2. Mount `storage/` to a persistent volume
3. Set resource limits in Docker
4. Configure log rotation
5. Use production-grade WSGI server settings
6. Implement monitoring and alerting
7. Regular backups of workflow definitions

### Docker Production

```yaml
# docker-compose.prod.yml
version: '3.8'
services:
  agent-api:
    build: .
    restart: always
    volumes:
      - ./storage:/app/storage:rw
    environment:
      - LOG_LEVEL=WARNING
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
```

---

## Troubleshooting

### Workflows Not Executing

- Check `enabled: true` in YAML
- Verify cron expression is valid
- Review scheduler logs

### SSE Stream Issues

- Ensure client supports Server-Sent Events
- Check for CORS issues
- Verify workflow name is correct

### Storage Errors

- Check directory permissions
- Verify disk space
- Validate YAML syntax

Enable debug logging:

```bash
export LOG_LEVEL=DEBUG
```

---

## Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - Quick start guide
- **[API_EXAMPLES.md](API_EXAMPLES.md)** - Complete API examples
- **[README.md](README.md)** - This file (overview and setup)
- **/docs** - Interactive API documentation (when running)

---

## Tech Stack

- **FastAPI** - Modern Python web framework
- **Pydantic** - Data validation using Python types
- **APScheduler** - Advanced Python Scheduler
- **PyYAML** - YAML parser and emitter
- **SSE-Starlette** - Server-Sent Events support
- **pytest** - Testing framework
- **Docker** - Containerization

---

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

---

## License

[Specify your license here - e.g., MIT, Apache 2.0, etc.]

---

## Acknowledgments

Built with modern Python tools and inspired by workflow automation best practices. Special thanks to the FastAPI, APScheduler, and Python communities.

---

## Support

- Open an issue on GitHub
- Check the documentation
- Review API docs at `/docs`

---

## What's Next?

Potential future enhancements:

- Web UI for workflow management
- Advanced task dependencies
- Notification integrations (email, Slack)
- Metrics dashboard
- Multi-node clustering
- Plugin system

---

**Agent API is ready for use!** 🚀

Start creating workflows and automating your tasks today.
