# CloudWatch Log Watch Analyzer

A multi-agent self-corrective RAG system for analyzing CloudWatch logs across multiple log groups.

## Overview

The Log Watch Analyzer is an intelligent log analysis system that uses a multi-agent architecture with self-corrective RAG (Retrieval-Augmented Generation) to:

- Watch multiple CloudWatch log groups simultaneously
- Detect anomalies and patterns in log data
- Correlate logs across services using trace IDs
- Generate intelligent alerts with context
- Learn from known issues and patterns

## Architecture

### Multi-Agent System

The system follows the NVIDIA Nemotron-inspired multi-agent architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR AGENT                       │
│  (Coordinates workflow, manages state, routes queries)      │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ LOG RETRIEVER │   │ PATTERN       │   │ ANOMALY       │
│ AGENT         │   │ ANALYZER      │   │ DETECTOR      │
└───────────────┘   └───────────────┘   └───────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE BASE (pgvector)                │
│  - Log Patterns    - Known Issues    - Baseline Metrics    │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ CORRELATION   │   │ ALERT         │   │ SELF-CORRECT  │
│ AGENT         │   │ GENERATOR     │   │ MECHANISM     │
└───────────────┘   └───────────────┘   └───────────────┘
```

### Components

1. **Orchestrator Agent**: Central coordinator that manages the workflow
2. **Log Retriever Agent**: Fetches logs from CloudWatch using Insights
3. **Pattern Analyzer Agent**: Identifies patterns using RAG
4. **Anomaly Detector Agent**: Compares against baselines
5. **Correlation Agent**: Traces requests across services
6. **Alert Generator Agent**: Creates contextual alerts

## Database Schema

The system uses PostgreSQL with pgvector extension:

```sql
-- Core tables
workflows        - Workflow definitions
executions       - Execution history
schedules        - Scheduled workflows
llm_configs      - LLM provider configurations
mcp_servers      - MCP server configurations

-- Knowledge base tables
log_patterns     - Log patterns with embeddings
known_issues     - Known issues with solutions
baseline_metrics - Normal behavior baselines
analysis_history - Historical analysis results
alerts           - Generated alerts
```

## API Endpoints

### Log Watching

```bash
# Watch multiple log groups
POST /api/v1/log-watch/watch
{
  "log_group_names": ["/aws/lambda/my-function", "/aws/apigateway/my-api"],
  "time_range_minutes": 60,
  "filter_pattern": "[level=ERROR*]"
}

# Analyze patterns
POST /api/v1/log-watch/analyze-patterns
{
  "log_group_names": ["/aws/lambda/my-function"],
  "pattern_types": ["error", "warning"]
}

# Detect anomalies
POST /api/v1/log-watch/detect-anomalies
{
  "log_group_names": ["/aws/lambda/my-function"],
  "sensitivity": "high"
}

# Correlate logs
POST /api/v1/log-watch/correlate
{
  "log_group_names": ["/aws/lambda/api", "/aws/lambda/processor"],
  "trace_id": "1-5f0c7e8d-abcdef1234567890"
}
```

### Alert Management

```bash
# Get alerts
GET /api/v1/log-watch/alerts?status=new&severity=high

# Create alert
POST /api/v1/log-watch/alerts
{
  "log_group": "/aws/lambda/my-function",
  "alert_type": "error_spike",
  "severity": "high",
  "message": "Error rate increased by 300%"
}

# Acknowledge alert
POST /api/v1/log-watch/alerts/{id}/acknowledge

# Resolve alert
POST /api/v1/log-watch/alerts/{id}/resolve
```

### Knowledge Base

```bash
# Add log pattern
POST /api/v1/log-watch/patterns
{
  "name": "DatabaseConnectionError",
  "pattern": "Connection refused.*database",
  "pattern_type": "error",
  "severity": 4
}

# Search patterns
GET /api/v1/log-watch/patterns/search?query=database%20connection%20error

# Add known issue
POST /api/v1/log-watch/known-issues
{
  "title": "Database Connection Timeout",
  "description": "Connection timeout when database is under load",
  "symptoms": ["Connection refused", "Timeout exceeded"],
  "solution": "Increase connection pool size and timeout settings",
  "category": "database"
}

# Set baseline metric
POST /api/v1/log-watch/baselines
{
  "metric_name": "error_rate",
  "log_group": "/aws/lambda/my-function",
  "normal_range_min": 0,
  "normal_range_max": 10,
  "threshold_warning": 20,
  "threshold_critical": 50
}
```

## MCP Tools

The system provides MCP (Model Context Protocol) tools for integration:

### watch_tools.py

- `watch_log_groups()` - Watch multiple log groups
- `analyze_log_patterns()` - Analyze patterns in logs
- `detect_anomalies()` - Detect anomalies vs baseline
- `correlate_logs()` - Correlate logs across services

### alert_tools.py

- `create_alert()` - Create new alert
- `get_alerts()` - Get alerts with filters
- `acknowledge_alert()` - Acknowledge alert
- `resolve_alert()` - Resolve alert
- `get_alert_summary()` - Get alert statistics

## Knowledge Base Service

The knowledge base service (`knowledge_base.py`) provides:

- Vector similarity search using pgvector
- Embedding generation using Amazon Titan
- Pattern matching and storage
- Known issue management
- Baseline metric tracking

## UI Components

The Log Watch UI (`/log-watch`) provides:

1. **Alerts Tab**: View and manage alerts
2. **Watch Logs Tab**: Watch log groups and detect anomalies
3. **Patterns Tab**: Manage log patterns
4. **Baselines Tab**: Configure baseline metrics
5. **Known Issues Tab**: Manage known issues

## Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://kycuser:kycpassword@postgres:5432/kycagent

# AWS
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

### Docker Compose

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: kycagent
      POSTGRES_USER: kycuser
      POSTGRES_PASSWORD: kycpassword
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init-db.sql:/docker-entrypoint-initdb.d/init-db.sql

  agent-api:
    build: ./agent-api
    environment:
      - DATABASE_URL=postgresql://kycuser:kycpassword@postgres:5432/kycagent
    depends_on:
      - postgres
```

## Usage Examples

### Basic Log Watch

```python
from app.mcp.tools.watch_tools import watch_log_groups

result = await watch_log_groups(
    log_group_names=["/aws/lambda/my-function"],
    time_range_minutes=30
)

print(f"Total events: {result['summary']['total_events']}")
```

### Anomaly Detection

```python
from app.mcp.tools.watch_tools import detect_anomalies

result = await detect_anomalies(
    log_group_names=["/aws/lambda/my-function"],
    sensitivity="high"
)

for anomaly in result['anomalies']:
    print(f"Anomaly at {anomaly['timestamp']}: {anomaly['deviation_factor']}x deviation")
```

### Creating Alerts

```python
from app.mcp.tools.alert_tools import create_alert

result = await create_alert(
    log_group="/aws/lambda/my-function",
    alert_type="error_spike",
    severity="high",
    message="Error rate increased by 300%",
    details={"baseline_rate": 10, "current_rate": 40}
)
```

### Searching Knowledge Base

```python
from app.services.knowledge_base import knowledge_base

# Search for similar patterns
patterns = await knowledge_base.search_similar_patterns(
    query="database connection timeout error",
    threshold=0.7
)

# Search known issues
issues = await knowledge_base.search_known_issues(
    query="connection refused to database",
    category="database"
)
```

## Self-Corrective RAG

The system implements self-corrective RAG:

1. **Initial Retrieval**: Query knowledge base for relevant patterns/issues
2. **Relevance Check**: Verify retrieved results are relevant
3. **Self-Correction**: If not relevant, reformulate query and retry
4. **Knowledge Update**: Learn from new patterns and solutions

## Best Practices

1. **Pattern Design**: Create specific patterns with clear severity levels
2. **Baseline Setup**: Establish baselines during normal operation periods
3. **Known Issues**: Document solutions for recurring problems
4. **Alert Tuning**: Adjust sensitivity thresholds based on environment
5. **Regular Review**: Review and update knowledge base regularly

## Troubleshooting

### Database Connection Issues

```bash
# Check database connection
docker exec -it postgres psql -U kycuser -d kycagent -c "SELECT 1"

# Verify pgvector extension
docker exec -it postgres psql -U kycuser -d kycagent -c "SELECT * FROM pg_extension WHERE extname = 'vector'"
```

### AWS Permissions

Ensure the IAM role/user has these permissions:
- `logs:FilterLogEvents`
- `logs:StartQuery`
- `logs:GetQueryResults`
- `bedrock:InvokeModel` (for embeddings)

### Embedding Generation

If embeddings fail, verify:
1. Bedrock is enabled in your AWS account
2. Titan embeddings model is available in your region
3. IAM permissions for Bedrock are correct
