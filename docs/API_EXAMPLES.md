# API Examples

This document provides practical examples for interacting with the Agent API.

## Table of Contents

- [Creating Workflows](#creating-workflows)
- [Updating Workflows](#updating-workflows)
- [Executing Workflows](#executing-workflows)
- [Streaming Events](#streaming-events)
- [Viewing History](#viewing-history)
- [Using Python Client](#using-python-client)
- [Using JavaScript Client](#using-javascript-client)

---

## Creating Workflows

### Example 1: Simple Shell Command Workflow

```bash
curl -X POST http://localhost:8000/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "disk-usage-check",
    "description": "Check disk usage every hour",
    "schedule": "0 * * * *",
    "enabled": true,
    "tasks": [
      {
        "name": "check-disk",
        "type": "shell",
        "command": "df -h",
        "timeout": 30
      }
    ]
  }'
```

### Example 2: Python Script Workflow

```bash
curl -X POST http://localhost:8000/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "data-processor",
    "description": "Process data every 15 minutes",
    "schedule": "*/15 * * * *",
    "enabled": true,
    "tasks": [
      {
        "name": "process-data",
        "type": "python",
        "script": "import datetime\nprint(f\"Processing at {datetime.datetime.now()}\")\nprint(\"Data processed successfully\")",
        "timeout": 60,
        "retry_count": 2,
        "retry_delay": 10
      }
    ]
  }'
```

### Example 3: Multi-Task Workflow

```bash
curl -X POST http://localhost:8000/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "monitoring-workflow",
    "description": "Monitor system and send report",
    "schedule": "0 */6 * * *",
    "enabled": true,
    "tasks": [
      {
        "name": "collect-metrics",
        "type": "shell",
        "command": "top -bn1 | head -n 20",
        "timeout": 30
      },
      {
        "name": "analyze-metrics",
        "type": "python",
        "script": "print(\"Analyzing metrics...\")\nprint(\"Analysis complete\")",
        "timeout": 60
      },
      {
        "name": "generate-report",
        "type": "shell",
        "command": "echo \"Report generated at $(date)\"",
        "timeout": 30
      }
    ],
    "timeout": 300
  }'
```

---

## Updating Workflows

### Update Schedule

```bash
curl -X PUT http://localhost:8000/workflows/disk-usage-check \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": "0 */2 * * *"
  }'
```

### Disable Workflow

```bash
curl -X PUT http://localhost:8000/workflows/disk-usage-check \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": false
  }'
```

### Update Tasks

```bash
curl -X PUT http://localhost:8000/workflows/disk-usage-check \
  -H "Content-Type: application/json" \
  -d '{
    "tasks": [
      {
        "name": "check-disk",
        "type": "shell",
        "command": "df -h && du -sh /*",
        "timeout": 60,
        "retry_count": 1
      }
    ]
  }'
```

---

## Executing Workflows

### Manual Execution

```bash
curl -X POST http://localhost:8000/workflows/disk-usage-check/execute
```

### Execute and Get Response

```bash
curl -X POST http://localhost:8000/workflows/disk-usage-check/execute | jq
```

---

## Streaming Events

### Using curl

```bash
curl -N http://localhost:8000/workflows/disk-usage-check/stream
```

### Using curl with Event Parsing

```bash
curl -N http://localhost:8000/workflows/disk-usage-check/stream | \
  while IFS= read -r line; do
    echo "$(date): $line"
  done
```

---

## Viewing History

### Get Recent Executions

```bash
curl http://localhost:8000/workflows/disk-usage-check/history | jq
```

### Get Last 10 Executions

```bash
curl "http://localhost:8000/workflows/disk-usage-check/history?limit=10" | jq
```

### Get Specific Execution

```bash
# Get execution ID from history
EXECUTION_ID="<execution-id-here>"
curl http://localhost:8000/workflows/disk-usage-check/history/$EXECUTION_ID | jq
```

---

## Using Python Client

### Basic Client

```python
import requests
import json

BASE_URL = "http://localhost:8000"

class AgentAPIClient:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url
    
    def list_workflows(self):
        response = requests.get(f"{self.base_url}/workflows")
        return response.json()
    
    def get_workflow(self, name):
        response = requests.get(f"{self.base_url}/workflows/{name}")
        return response.json()
    
    def create_workflow(self, workflow_data):
        response = requests.post(
            f"{self.base_url}/workflows",
            json=workflow_data
        )
        return response.json()
    
    def execute_workflow(self, name):
        response = requests.post(f"{self.base_url}/workflows/{name}/execute")
        return response.json()
    
    def get_history(self, name, limit=50):
        response = requests.get(
            f"{self.base_url}/workflows/{name}/history",
            params={"limit": limit}
        )
        return response.json()

# Usage
client = AgentAPIClient()

# List workflows
workflows = client.list_workflows()
print(f"Found {len(workflows)} workflows")

# Execute workflow
result = client.execute_workflow("disk-usage-check")
print(f"Execution started: {result}")

# Get history
history = client.get_history("disk-usage-check", limit=10)
print(f"Last 10 executions:")
for execution in history:
    print(f"  - {execution['execution_id']}: {execution['status']}")
```

### Streaming Client

```python
import requests
import json

def stream_workflow_execution(workflow_name):
    url = f"http://localhost:8000/workflows/{workflow_name}/stream"
    
    with requests.get(url, stream=True) as response:
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                
                # Parse SSE format
                if line_str.startswith('event:'):
                    event_type = line_str.split(':', 1)[1].strip()
                elif line_str.startswith('data:'):
                    data = line_str.split(':', 1)[1].strip()
                    event_data = json.loads(data)
                    
                    print(f"Event: {event_data.get('event_type')}")
                    print(f"Data: {json.dumps(event_data.get('data'), indent=2)}")
                    print("-" * 50)
                    
                    # Stop on workflow complete
                    if event_data.get('event_type') == 'workflow_complete':
                        break

# Usage
stream_workflow_execution("disk-usage-check")
```

---

## Using JavaScript Client

### Basic Client (Node.js)

```javascript
const axios = require('axios');

const BASE_URL = 'http://localhost:8000';

class AgentAPIClient {
  constructor(baseUrl = BASE_URL) {
    this.baseUrl = baseUrl;
    this.client = axios.create({ baseURL: baseUrl });
  }

  async listWorkflows() {
    const response = await this.client.get('/workflows');
    return response.data;
  }

  async getWorkflow(name) {
    const response = await this.client.get(`/workflows/${name}`);
    return response.data;
  }

  async createWorkflow(workflowData) {
    const response = await this.client.post('/workflows', workflowData);
    return response.data;
  }

  async executeWorkflow(name) {
    const response = await this.client.post(`/workflows/${name}/execute`);
    return response.data;
  }

  async getHistory(name, limit = 50) {
    const response = await this.client.get(`/workflows/${name}/history`, {
      params: { limit }
    });
    return response.data;
  }
}

// Usage
(async () => {
  const client = new AgentAPIClient();

  // List workflows
  const workflows = await client.listWorkflows();
  console.log(`Found ${workflows.length} workflows`);

  // Execute workflow
  const result = await client.executeWorkflow('disk-usage-check');
  console.log('Execution started:', result);

  // Get history
  const history = await client.getHistory('disk-usage-check', 10);
  console.log('Last 10 executions:');
  history.forEach(execution => {
    console.log(`  - ${execution.execution_id}: ${execution.status}`);
  });
})();
```

### Browser Client with SSE

```javascript
class WorkflowStreamClient {
  constructor(workflowName, baseUrl = 'http://localhost:8000') {
    this.workflowName = workflowName;
    this.baseUrl = baseUrl;
    this.eventSource = null;
  }

  connect(onEvent, onComplete, onError) {
    const url = `${this.baseUrl}/workflows/${this.workflowName}/stream`;
    this.eventSource = new EventSource(url);

    this.eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      onEvent(data);
    };

    this.eventSource.addEventListener('workflow_complete', (event) => {
      const data = JSON.parse(event.data);
      onComplete(data);
      this.disconnect();
    });

    this.eventSource.onerror = (error) => {
      onError(error);
      this.disconnect();
    };
  }

  disconnect() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}

// Usage
const client = new WorkflowStreamClient('disk-usage-check');

client.connect(
  // On event
  (data) => {
    console.log('Event:', data.event_type);
    console.log('Data:', data.data);
  },
  // On complete
  (data) => {
    console.log('Workflow complete!');
    console.log('Final status:', data.data.status);
  },
  // On error
  (error) => {
    console.error('Stream error:', error);
  }
);
```

---

## Health Check

```bash
# Basic health check
curl http://localhost:8000/health | jq

# Detailed status
curl http://localhost:8000/status | jq
```

---

## Complete Workflow Lifecycle Example

```bash
#!/bin/bash

WORKFLOW_NAME="test-workflow"

# 1. Create workflow
echo "Creating workflow..."
curl -X POST http://localhost:8000/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "'$WORKFLOW_NAME'",
    "description": "Test workflow",
    "schedule": "* * * * *",
    "enabled": false,
    "tasks": [{
      "name": "test-task",
      "type": "shell",
      "command": "echo \"Test execution at $(date)\"",
      "timeout": 30
    }]
  }'

# 2. Get workflow details
echo -e "\n\nGetting workflow..."
curl http://localhost:8000/workflows/$WORKFLOW_NAME | jq

# 3. Execute workflow
echo -e "\n\nExecuting workflow..."
curl -X POST http://localhost:8000/workflows/$WORKFLOW_NAME/execute

# 4. Wait a bit
sleep 2

# 5. Get execution history
echo -e "\n\nGetting history..."
curl http://localhost:8000/workflows/$WORKFLOW_NAME/history | jq

# 6. Delete workflow
echo -e "\n\nDeleting workflow..."
curl -X DELETE http://localhost:8000/workflows/$WORKFLOW_NAME

echo -e "\n\nDone!"
```

This will create, execute, and clean up a test workflow.
