# Agent API

Agent API is a lightweight workflow automation engine that runs scheduled jobs and streams their execution progress in real time.

It lets you define workflows in YAML, schedule them using cron rules, execute tasks automatically in the background, and monitor progress instantly — all inside a single Docker container with no external dependencies.

---

## Features

* **Cron-based workflow scheduling** — Schedule workflows using familiar cron expressions
* **YAML-defined workflows** — Simple, readable workflow definitions
* **Background task execution** — Workflows run independently in the background
* **Real-time progress streaming** — Monitor execution live via SSE (no polling required)
* **Persistent storage without a database** — YAML-based storage with no external dependencies
* **Single-container Docker deployment** — Everything runs in one lightweight container
* **Multiple concurrent workflows** — Execute multiple workflows simultaneously
* **Strong schema validation** — Pydantic-powered data validation

---

## Tech Stack

* **FastAPI** — Modern Python API framework with automatic OpenAPI docs and streaming endpoints
* **APScheduler** — Robust background scheduler with cron support
* **Pydantic** — Data validation and settings management using Python type annotations
* **Server-Sent Events (SSE)** — Real-time progress streaming without WebSocket complexity
* **YAML (PyYAML)** — Human-readable workflow storage
* **Docker** — Containerized deployment for consistency and portability

---

## Project Structure

```
agent-api/
├── app/              # Application code
│   ├── main.py       # FastAPI application entry point
│   ├── models/       # Pydantic schemas
│   ├── routes/       # API endpoints
│   ├── scheduler/    # APScheduler configuration
│   └── services/     # Business logic
├── storage/          # YAML workflows + logs
│   ├── workflows/    # Workflow definitions
│   └── logs/         # Execution history
├── docker/           # Docker configuration
│   └── Dockerfile    # Container definition
├── requirements.txt  # Python dependencies
├── .dockerignore     # Docker build exclusions
└── README.md         # This file
```

---

## How It Works

1. **Docker starts** the Agent API container
2. **FastAPI boots** and initializes the scheduler
3. **Workflows are loaded** from YAML and validated against Pydantic schemas
4. **APScheduler registers** cron jobs for each workflow
5. **Workflows execute** in the background at scheduled times
6. **Execution events stream** live to clients via Server-Sent Events
7. **History and logs** are persisted to the storage directory

---

## Quick Start

### Prerequisites

* Docker and Docker Compose
* (Optional) Python 3.11+ for local development

### Using Docker

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd agent-api
   ```

2. Build and run the container:
   ```bash
   docker-compose up --build
   ```

3. Access the API:
   * API: `http://localhost:8000`
   * Interactive docs: `http://localhost:8000/docs`
   * Alternative docs: `http://localhost:8000/redoc`

### Local Development

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

---

## Usage

### Creating a Workflow

Create a YAML file in `storage/workflows/`:

```yaml
name: example-workflow
description: An example workflow that runs every hour
schedule: "0 * * * *"  # Cron expression (every hour)
enabled: true
tasks:
  - name: task-1
    type: shell
    command: echo "Hello from task 1"
  - name: task-2
    type: python
    script: |
      print("Hello from task 2")
      # Your Python code here
```

### API Endpoints

#### List All Workflows
```bash
GET /workflows
```

#### Get Workflow Details
```bash
GET /workflows/{workflow_id}
```

#### Create/Update Workflow
```bash
POST /workflows
Content-Type: application/json

{
  "name": "my-workflow",
  "description": "My workflow description",
  "schedule": "*/5 * * * *",
  "enabled": true,
  "tasks": [...]
}
```

#### Delete Workflow
```bash
DELETE /workflows/{workflow_id}
```

#### Execute Workflow Manually
```bash
POST /workflows/{workflow_id}/execute
```

#### Stream Workflow Execution
```bash
GET /workflows/{workflow_id}/stream
Accept: text/event-stream
```

Example using curl:
```bash
curl -N http://localhost:8000/workflows/example-workflow/stream
```

#### Get Execution History
```bash
GET /workflows/{workflow_id}/history
```

---

## Cron Schedule Format

Agent API uses standard cron expressions with 5 fields:

```
* * * * *
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, both 0 and 7 are Sunday)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)
```

### Examples

* `*/5 * * * *` — Every 5 minutes
* `0 * * * *` — Every hour
* `0 9 * * *` — Daily at 9:00 AM
* `0 9 * * 1` — Every Monday at 9:00 AM
* `0 0 1 * *` — First day of every month at midnight

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=false

# Storage Configuration
STORAGE_PATH=./storage

# Scheduler Configuration
SCHEDULER_TIMEZONE=UTC
MAX_CONCURRENT_WORKFLOWS=5

# Logging
LOG_LEVEL=INFO
```

### Docker Environment

Customize the Docker deployment in `docker-compose.yml`:

```yaml
version: '3.8'
services:
  agent-api:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./storage:/app/storage
    environment:
      - API_HOST=0.0.0.0
      - API_PORT=8000
      - LOG_LEVEL=INFO
    restart: unless-stopped
```

---

## Monitoring

### Real-time Execution Monitoring

Connect to the SSE stream to monitor workflow execution in real time:

```javascript
const eventSource = new EventSource('http://localhost:8000/workflows/my-workflow/stream');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Progress:', data);
};

eventSource.addEventListener('complete', (event) => {
  console.log('Workflow complete:', event.data);
  eventSource.close();
});

eventSource.addEventListener('error', (event) => {
  console.error('Workflow error:', event.data);
  eventSource.close();
});
```

### Logs

Execution logs are stored in `storage/logs/` with the following structure:

```
storage/logs/
├── {workflow_id}/
│   ├── {execution_timestamp}.log
│   └── {execution_timestamp}.json
```

---

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────┐
│              FastAPI App                    │
├─────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │  Routes  │  │ Services │  │  Models  │ │
│  └──────────┘  └──────────┘  └──────────┘ │
└──────────┬──────────────────────────────────┘
           │
           ├──────────► APScheduler
           │            (Background Jobs)
           │
           ├──────────► SSE Streams
           │            (Real-time Updates)
           │
           └──────────► YAML Storage
                        (Workflows & Logs)
```

### Request Flow

1. Client sends workflow creation request
2. FastAPI validates request using Pydantic
3. Workflow is saved to YAML storage
4. APScheduler registers cron job
5. At scheduled time, workflow executes
6. Progress events are broadcast via SSE
7. Results are logged to storage

---

## Development

### Running Tests

```bash
pytest tests/
```

### Code Quality

```bash
# Format code
black app/

# Lint code
flake8 app/

# Type checking
mypy app/
```

### Adding New Task Types

Extend the task executor in `app/services/executor.py`:

```python
class TaskExecutor:
    def execute(self, task: Task) -> TaskResult:
        if task.type == "shell":
            return self._execute_shell(task)
        elif task.type == "python":
            return self._execute_python(task)
        elif task.type == "your_custom_type":
            return self._execute_custom(task)
        else:
            raise ValueError(f"Unknown task type: {task.type}")
```

---

## Troubleshooting

### Common Issues

**Workflows not executing**
* Check that `enabled: true` in the workflow YAML
* Verify the cron expression is valid
* Check scheduler logs for errors

**SSE stream not connecting**
* Ensure the client supports Server-Sent Events
* Check for CORS issues if accessing from a browser
* Verify the workflow ID is correct

**Storage errors**
* Ensure the storage directory has proper permissions
* Check available disk space
* Verify YAML syntax is valid

### Debug Mode

Enable debug logging:

```bash
export LOG_LEVEL=DEBUG
uvicorn app.main:app --reload
```

---

## Deployment

### Production Considerations

* Use a reverse proxy (nginx, Traefik) for SSL/TLS
* Mount `storage/` to a persistent volume
* Set appropriate resource limits in Docker
* Configure log rotation for `storage/logs/`
* Use environment-specific configuration files
* Implement monitoring and alerting
* Regular backups of workflow definitions

### Example nginx Configuration

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # SSE-specific settings
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
    }
}
```

---

## Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

[Specify your license here]

---

## Support

For issues, questions, or contributions:

* Open an issue on GitHub
* Check existing documentation
* Review the API docs at `/docs`

---

## Roadmap

Future enhancements under consideration:

* Web UI for workflow management
* Workflow templates and marketplace
* Advanced task dependencies and conditions
* Notification integrations (email, Slack, etc.)
* Metrics and analytics dashboard
* Multi-node clustering support
* Plugin system for custom integrations

---

## Acknowledgments

Built with modern Python tools and inspired by workflow automation best practices.
