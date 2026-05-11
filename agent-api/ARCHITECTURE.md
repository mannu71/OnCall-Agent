# Agent API Architecture

## Overview

Agent API is an **AI-powered workflow automation engine** that executes agentic workflows using LangGraph and the ReAct pattern. While the README describes it as a "lightweight workflow automation engine with cron scheduling," the actual implementation is far more sophisticated—it's a full-featured AI agent orchestration platform with advanced capabilities.

## Core Architecture

### Tech Stack

- **FastAPI** - Modern async Python web framework
- **LangGraph** - AI agent orchestration using ReAct pattern
- **LangChain** - LLM integration (OpenAI, Anthropic, AWS Bedrock)
- **PostgreSQL + pgvector** - Vector database for semantic search
- **APScheduler** - Background job scheduling
- **MCP (Model Context Protocol)** - Extensible tool integration
- **Pydantic** - Data validation and settings management

### Key Components

#### 1. Workflow Engine (`app/workflow/engine.py`)

- Strategy pattern for different workflow types
- Supports orchestrator and ReAct strategies
- Timeout protection and metrics collection
- Intent classification for routing

**Key Features:**
- Two-pass strategy selection (node-type matching, then intent classification)
- Execution timeout protection (default 5 minutes)
- Metrics collection for monitoring
- Graceful error handling with detailed error context

#### 2. ReAct Strategy (`app/workflow/strategies/react.py`)

AI-driven investigative workflows using the ReAct (Reasoning + Acting) pattern.

**Capabilities:**
- LangGraph-based agent execution
- Tool calling with MCP integration
- Context compression for long conversations
- Memory management for cross-session recall
- CloudWatch integration for AWS log analysis
- Context reference preprocessing (`@file`, `@folder`, `@url`, etc.)

**Execution Flow:**
```
User Query
    ↓
Pre-execution Recall (Memory Manager)
    ↓
Context Reference Expansion (@file, @folder, etc.)
    ↓
Tool Setup (MCP + CloudWatch)
    ↓
LLM + Agent Build
    ↓
ReAct Loop (Thought → Action → Observation)
    ↓
Post-execution Learning (Knowledge Base)
    ↓
Trajectory Storage
    ↓
Memory Sync
    ↓
Final Answer
```

#### 3. Memory System (`app/core/memory/`)

Pluggable memory architecture for cross-session knowledge retention.

**Components:**
- **MemoryProvider Protocol** - Interface for memory backends
- **MemoryManager** - Orchestrates multiple providers
- **BuiltinMemoryProvider** - Default knowledge base integration

**Features:**
- System prompt injection from all providers
- Prefetch relevant context before each turn
- Sync completed turns to all providers
- Tool call routing to correct provider
- Lifecycle hooks (turn start/end, compression events)

**Constraints:**
- Always registers built-in provider first
- Maximum one external provider (prevents schema bloat)

#### 4. Context Management

##### Context Compression (`app/core/context_compression.py`)

Intelligent message pruning when approaching token limits.

**Strategy:**
- Protects system prompts and first N messages
- Protects last 4 messages (recent context)
- Compresses middle messages using auxiliary LLM
- Triggered at configurable threshold (default 50% of context length)

##### Context References (`app/core/context_references.py`)

Preprocesses special references in user queries:
- `@file:path/to/file` - Inject file contents
- `@folder:path/to/dir` - Inject directory structure
- `@url:https://...` - Fetch and inject web content
- `@diff` - Inject git diff
- `@staged` - Inject staged changes
- `@git` - Inject git status

**Safety:**
- Token limit enforcement
- Path traversal protection
- Allowed root validation
- Graceful degradation on errors

##### Prompt Caching (`app/core/prompt_caching.py`)

Optimizes repeated context by marking cacheable sections.

#### 5. Tool Integration

##### MCP Client Manager (`app/workflow/mcp/manager.py`)

Manages connections to external MCP servers.

**Features:**
- Dynamic server connection/disconnection
- Connection pooling
- Error handling and reconnection logic

##### MCP LangChain Adapter (`app/workflow/mcp/mcp_langchain_adapter.py`)

Bridges MCP tools to LangChain's tool interface.

**Capabilities:**
- Converts MCP tool schemas to LangChain format
- Handles tool execution and result formatting
- Error propagation and logging

##### CloudWatch Tools (`app/workflow/tools/cloudwatch_agent_tools.py`)

AWS log analysis capabilities:
- Query log groups
- Search log streams
- Pattern detection
- Anomaly identification

##### Tool Registry (`app/core/tool_registry.py`)

Centralized tool management and discovery.

#### 6. Error Handling & Resilience

##### Error Classifier (`app/core/error_classifier.py`)

Categorizes errors for intelligent retry logic:
- **Rate limit errors** - Exponential backoff
- **Authentication errors** - No retry
- **Context overflow** - Trigger compression
- **Transient errors** - Retry with jitter
- **Permanent errors** - Fail fast

##### Retry Logic (`app/core/retry.py`)

Exponential backoff with jitter:
- Configurable max attempts
- Exponential delay calculation
- Random jitter to prevent thundering herd
- Error classification integration

##### Rate Limit Tracking (`app/core/rate_limit_tracker.py`)

Monitors API usage from response headers:
- Tracks remaining requests/tokens
- Warns at configurable threshold (default 80%)
- Supports multiple providers (OpenAI, Anthropic, OpenRouter)

#### 7. Skills System (`app/core/skills/`)

Reusable agent capabilities packaged as skills.

**Structure:**
```
skills/
├── skill-name/
│   ├── SKILL.md          # Documentation and instructions
│   ├── agents/           # Custom agent configs
│   ├── rules/            # Validation rules
│   └── evals/            # Test cases
```

**Features:**
- Dynamic skill loading
- Skill validation
- Command execution
- Integration with agent workflows

## API Endpoints

### Core Endpoints (`app/api/v1/endpoints/`)

| Endpoint | Purpose | Key Features |
|----------|---------|--------------|
| `/health` | Health checks | Database connectivity, system status |
| `/workflows` | CRUD for workflow definitions | Create, read, update, delete workflows |
| `/executions` | Execute workflows | Streaming results via SSE, execution history |
| `/mcp_config` | Configure MCP servers | Add/remove/test MCP connections |
| `/certificates` | Manage SSL certificates | Upload custom CA certificates |
| `/llm_config` | Configure LLM providers | Model selection, provider settings |
| `/model_keys` | Manage API keys | Encrypted storage, key rotation |
| `/insights` | Knowledge base queries | Search known issues, patterns |
| `/skills` | Manage agent skills | List, activate, execute skills |
| `/trajectories` | Execution replay | Debug past executions, token analysis |
| `/usage` | Token usage tracking | Cost monitoring, usage analytics |
| `/log_watch` | Real-time log monitoring | Stream logs from executions |

### Streaming Architecture

**Server-Sent Events (SSE):**
- Token-by-token streaming from LLM
- Tool call previews
- Progress updates
- Error notifications

**Benefits:**
- No polling required
- Unidirectional (server → client)
- Simpler than WebSockets
- Built-in reconnection

## Advanced Features

### 1. Intelligent Context Management

**Problem:** LLMs have token limits that can be exceeded in long conversations.

**Solution:**
- Automatic compression when approaching limits
- Protects critical context (system prompts, recent messages)
- Uses auxiliary LLM for summarization
- Preserves semantic meaning

**Configuration:**
```python
context_compression_enabled: bool = True
context_threshold_percent: float = 0.50  # Trigger at 50%
context_protect_first_n: int = 3         # Protect first 3 messages
```

### 2. Cross-Session Memory

**Problem:** Each execution starts with no knowledge of past investigations.

**Solution:**
- Vector-based semantic search in knowledge base
- Recalls relevant past investigations before execution
- Auto-learns from successful resolutions
- Persists patterns and solutions

**Workflow:**
1. User submits query
2. System searches knowledge base for similar past issues
3. Injects relevant context into query
4. Agent executes with enhanced context
5. System persists new learnings

### 3. Trajectory Recording

**Problem:** Debugging agent behavior is difficult without execution traces.

**Solution:**
- Saves complete execution traces
- Records all messages, tool calls, and results
- Tracks token usage and performance metrics
- Enables replay and analysis

**Use Cases:**
- Debug unexpected behavior
- Analyze token usage patterns
- Optimize prompts and tools
- Audit agent decisions

### 4. Multi-Provider LLM Support

**Supported Providers:**
- **OpenAI** - GPT-4, GPT-3.5
- **Anthropic** - Claude 3 (Opus, Sonnet, Haiku)
- **AWS Bedrock** - Claude via AWS
- **OpenRouter** - Aggregated model access

**Auxiliary Client:**
- Separate LLM for side tasks (summarization, compression)
- Prevents main model rate limit exhaustion
- Cost optimization (use cheaper model for auxiliary tasks)

### 5. CloudWatch Integration

**Capabilities:**
- Direct AWS log analysis
- Pattern detection in log streams
- Anomaly identification
- Time-range queries

**Integration:**
- Injected as tools when `cloudwatchAnalyzer` node present
- AWS credential resolution (profile or environment)
- Results passed to agent as context

### 6. Real-Time Streaming

**Implementation:**
- Server-Sent Events (SSE) for live progress
- Token-by-token streaming from LLM
- Tool call previews before execution
- Progress indicators

**Client Integration:**
```javascript
const eventSource = new EventSource('/api/v1/executions/{id}/stream');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // Handle token, tool_call, or progress event
};
```

## Database Schema

### Core Tables

**Workflows**
- Workflow definitions (nodes, edges, configuration)
- Scheduling information (cron expressions)
- Enabled/disabled state

**Executions**
- Execution history and results
- Status tracking (pending, running, completed, failed)
- Duration and performance metrics

**KnownIssues** (with vector embeddings)
- Title, description, symptoms, solution
- Category and source
- Vector embeddings for semantic search

**LogPatterns** (with vector embeddings)
- Pattern name, regex, description
- Severity and pattern type
- Vector embeddings for similarity search

**CloudWatchAnalyses**
- Log group, analysis type, time range
- Summary, anomalies, patterns matched
- Linked to executions

**Skills**
- Skill name, description, content
- Commands and configuration
- Activation status

**Trajectories**
- Execution traces (messages, tool calls)
- Token usage breakdown
- Model and provider information

**ModelKeys**
- Encrypted API keys for LLM providers
- Provider name and key name
- Creation and update timestamps

## Configuration

### Settings (`app/config.py`)

```python
class Settings(BaseSettings):
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    # Database
    database_url: str = "postgresql://..."
    
    # CORS
    cors_origins: List[str] = ["*"]
    
    # Context compression
    context_compression_enabled: bool = True
    context_threshold_percent: float = 0.50
    context_protect_first_n: int = 3
    
    # Rate limiting
    rate_limit_tracking_enabled: bool = True
    rate_limit_warning_threshold: float = 0.80
    
    # Auxiliary LLM
    auxiliary_provider: str = "auto"
    auxiliary_model: str = ""
    
    # Directories
    skills_dir: str = "data/skills"
    trajectories_dir: str = "data/trajectories"
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"
```

## Testing

### Test Structure

```
tests/
├── unit/                    # Unit tests for individual components
├── integration/             # Integration tests for workflows
├── api/                     # API endpoint tests
├── core/                    # Core functionality tests
├── workflow/                # Workflow engine tests
└── property/                # Property-based tests
```

### Property-Based Testing

Uses **Hypothesis** for property-based testing:
- `test_context_compression_properties.py` - Context compression invariants
- `test_error_classifier_properties.py` - Error classification consistency
- `test_retry_properties.py` - Retry logic correctness
- `test_tool_registry_properties.py` - Tool registry behavior

### Test Coverage

- Error classifier (billing, generic, provider-specific, status codes)
- Context compression (threshold, protection, summarization)
- Retry logic (exponential backoff, jitter, max attempts)
- Model metadata (context length, token limits)
- Prompt caching (cache markers, optimization)
- Skills API (CRUD, command execution)
- Trajectory service (storage, retrieval, replay)

## Use Cases

### 1. Investigative Queries

**Example:** "Why did profiles fail today?"

**Flow:**
1. User submits query
2. System recalls similar past issues
3. Agent searches logs, metrics, databases
4. Agent identifies root cause
5. System persists findings for future recall

### 2. Root Cause Analysis

**Example:** "What's causing high CPU usage?"

**Flow:**
1. Agent queries CloudWatch metrics
2. Agent analyzes log patterns
3. Agent correlates events across services
4. Agent identifies culprit process/service
5. Agent suggests remediation

### 3. Log Analysis

**Example:** "Find errors in CloudWatch logs for the last hour"

**Flow:**
1. Agent queries CloudWatch with time range
2. Agent filters for error patterns
3. Agent groups similar errors
4. Agent identifies trends and anomalies
5. Agent summarizes findings

### 4. Automated Troubleshooting

**Example:** "Debug the payment service"

**Flow:**
1. Agent checks service health endpoints
2. Agent queries recent deployments
3. Agent analyzes error logs
4. Agent tests database connectivity
5. Agent provides diagnostic report

### 5. Knowledge Accumulation

**Example:** Recurring issues become known issues

**Flow:**
1. Agent resolves issue
2. System detects resolution keywords
3. System persists to knowledge base
4. Future similar queries recall this solution
5. Faster resolution over time

## Key Differentiators

### 1. Agentic Workflows
- AI decides tool execution order
- Dynamic reasoning based on observations
- No predefined execution paths

### 2. Memory Persistence
- Cross-session learning
- Vector-based semantic search
- Automatic knowledge accumulation

### 3. Context-Aware
- Handles long conversations intelligently
- Automatic compression when needed
- Preserves critical context

### 4. Production-Ready
- Comprehensive error handling
- Retry logic with exponential backoff
- Rate limit tracking and warnings
- Encrypted API key storage

### 5. Extensible
- MCP protocol for custom tools
- Pluggable memory providers
- Skills system for reusable capabilities
- Multiple LLM provider support

### 6. Observable
- Trajectory recording for debugging
- Token usage tracking
- Real-time streaming
- Execution metrics

## Deployment

### Docker Deployment

```bash
docker-compose up --build
```

### Environment Variables

```env
# API Configuration
API_HOST=0.0.0.0
API_PORT=8000

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/db

# LLM Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# AWS (for Bedrock/CloudWatch)
AWS_PROFILE=default
AWS_REGION=us-east-1

# Features
CONTEXT_COMPRESSION_ENABLED=true
RATE_LIMIT_TRACKING_ENABLED=true

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### Production Considerations

1. **Reverse Proxy** - Use nginx/Traefik for SSL/TLS
2. **Database** - PostgreSQL with pgvector extension
3. **Secrets** - Use environment variables or secrets manager
4. **Monitoring** - CloudWatch, Prometheus, or Datadog
5. **Backups** - Regular database backups
6. **Scaling** - Horizontal scaling with load balancer
7. **Rate Limits** - Configure per-provider rate limits

## Future Enhancements

### Planned Features

1. **Multi-Agent Collaboration** - Multiple agents working together
2. **Workflow Templates** - Pre-built workflows for common tasks
3. **Plugin System** - Third-party integrations
4. **Web UI** - Visual workflow builder
5. **Metrics Dashboard** - Real-time analytics
6. **Multi-Tenancy** - Isolated workspaces per user/team
7. **Workflow Versioning** - Track changes over time
8. **A/B Testing** - Compare different agent configurations

## Contributing

### Development Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/

# Run with hot reload
uvicorn app.main:app --reload
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

## License

[Specify your license here]

## Support

For issues, questions, or contributions:
- Open an issue on GitHub
- Check existing documentation
- Review the API docs at `/docs`
