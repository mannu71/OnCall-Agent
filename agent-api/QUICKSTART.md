# Agent API - Quick Start Guide

## Installation

### Using Docker (Recommended)

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd agent-api
   ```

2. Build and start the container:
   ```bash
   docker-compose up -d
   ```

3. Check the logs:
   ```bash
   docker-compose logs -f
   ```

4. Access the API:
   - API: http://localhost:8000
   - Docs: http://localhost:8000/docs

### Local Development

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create storage directories:
   ```bash
   mkdir -p storage/workflows storage/logs
   ```

4. Copy example environment:
   ```bash
   cp .env.example .env
   ```

5. Run the application:
   ```bash
   python -m app.main
   ```

## First Workflow

Create a file `storage/workflows/my-first-workflow.yaml`:

```yaml
name: my-first-workflow
description: My first workflow
schedule: "*/5 * * * *"  # Every 5 minutes
enabled: true
tasks:
  - name: hello-task
    type: shell
    command: echo "Hello from my first workflow!"
    timeout: 30
```

Restart the application to load the workflow.

## Testing the API

### List all workflows
```bash
curl http://localhost:8000/workflows
```

### Get workflow details
```bash
curl http://localhost:8000/workflows/my-first-workflow
```

### Execute workflow manually
```bash
curl -X POST http://localhost:8000/workflows/my-first-workflow/execute
```

### Stream execution events
```bash
curl -N http://localhost:8000/workflows/my-first-workflow/stream
```

### View execution history
```bash
curl http://localhost:8000/workflows/my-first-workflow/history
```

## Next Steps

1. Explore the example workflows in `storage/workflows/`
2. Read the full README.md for detailed documentation
3. Check out the API documentation at http://localhost:8000/docs
4. Create custom workflows for your use case

## Common Issues

**Workflow not running?**
- Check that `enabled: true` in the YAML
- Verify the cron expression is valid
- Check logs: `docker-compose logs -f` or application output

**Permission errors?**
- Ensure the storage directory has write permissions
- On Docker, check volume mounts in docker-compose.yml

**Port conflict?**
- Change the port in docker-compose.yml or .env file
- Default port is 8000

## Support

For more help:
- Read the full README.md
- Check API docs at /docs
- Review example workflows
- Open an issue on GitHub
