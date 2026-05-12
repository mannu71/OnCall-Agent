# AGENTS.md — agent-api (Backend)
# Claude Code reads this at the start of every session.
# Keep this file updated as the codebase evolves.
# Last updated: manually maintain this date — YYYY-MM-DD

---

## What this repo is

AI Investigation Platform backend. Orchestrates multiple AI agents that
analyse AWS CloudWatch logs, query databases via MCP, and analyse Azure
DevOps codebases. Synthesises root cause and ranked suggestions. Gets
smarter with every resolved investigation via a self-learning loop.

---

## Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.11 | |
| Web framework | FastAPI | Async throughout |
| Agent orchestration | LangGraph | See workflow/ — pinned version |
| LLM | Anthropic Claude (primary) | via core/auxiliary_client.py |
| Database | SQLite (dev) → PostgreSQL (prod) | infrastructure/database/connection.py |
| Vector store | pgvector (planned) | Not yet wired |
| MCP protocol | Custom MCP server | mcp/ directory |
| Azure DevOps | services/azure_devops_client.py | PAT auth |
| AWS | boto3 / CloudWatch | workflow/tools/cloudwatch_agent_tools.py |
| Settings | pydantic-settings | app/config.py |
| Migrations | Custom SQL scripts | migrations/ + scripts/ |
| Testing | pytest | tests/ |
| Containerisation | Docker + docker-compose | Both present |

---

## Directory map — read before touching any file

```
app/
  main.py                     ← FastAPI app, lifespan, router mounting
  config.py                   ← ALL env vars live here via pydantic-settings
  api/
    deps.py                   ← Shared FastAPI dependencies (DB session, auth)
    middleware/
      error_handler.py        ← Global exception → HTTP response mapping
    v1/
      api.py                  ← Router registration — add new routers here
      endpoints/              ← One file per resource group
        certificates.py
        executions.py         ← Workflow execution triggers + status
        health.py
        insights.py
        llm_config.py         ← LLM provider configuration
        log_watch.py          ← CloudWatch log watching config
        mcp_config.py         ← MCP server configuration
        model_keys.py         ← API key management
        releases.py           ← Azure DevOps release management
        settings.py
        skills.py             ← Agent skill management
        trajectories.py       ← Investigation trajectory storage
        usage.py
        workflows.py          ← Workflow CRUD + execution
      schemas/                ← Pydantic request/response models per endpoint

  application/
    events.py                 ← FastAPI startup/shutdown lifecycle hooks
    exceptions.py             ← Custom exception classes
    logging_config.py         ← Structured logging setup

  core/                       ← Cross-cutting infrastructure — rarely change
    auxiliary_client.py       ← LLM provider abstraction (Anthropic, etc.)
    circuit_breaker.py        ← Circuit breaker for external calls
    context_compression.py    ← Context window compression for long sessions
    context_references.py     ← Context reference management
    error_classifier.py       ← Error type classification
    executor.py               ← Async task executor
    model_metadata.py         ← Model capability metadata
    prompt_caching.py         ← Prompt caching layer
    rate_limit_tracker.py     ← API rate limit tracking
    retry.py                  ← Retry logic with backoff
    scheduler.py              ← Cron/scheduled task runner
    tool_registry.py          ← Tool registration and discovery
    memory/
      builtin.py              ← Built-in memory backends
      manager.py              ← Memory lifecycle management
    skills/
      manager.py              ← Skill loading and invocation
    streaming/
      callbacks.py            ← SSE/streaming LangChain callbacks

  domain/
    interfaces/               ← Abstract base classes go here (currently empty)
    services/
      llm_discovery_service.py ← LLM provider discovery

  infrastructure/
    database/
      connection.py           ← SQLite async connection (dev) — swap for PG in prod

  mcp/                        ← MCP server implementation
    resources/
      cloudwatch_logs_resource.py ← CloudWatch log resource for MCP
    tools/
      alert_tools.py          ← Alert analysis tools
      analysis_tools.py       ← Log analysis tools
      correlation_tools.py    ← Cross-signal correlation
      search_tools.py         ← Search tools
      watch_tools.py          ← Log watch tools

  models/                     ← SQLAlchemy ORM + Pydantic data models
    azure_devops.py           ← Azure DevOps data models
    azure_wiki.py             ← Wiki data models
    db_models.py              ← Core DB table definitions — change with migration
    error_response.py         ← Standard error response shape
    workflow.py               ← Workflow and execution models

  repositories/               ← Data access layer — all DB queries go here
    base.py                   ← Base repository with common CRUD
    db_repository.py          ← Generic DB repository
    execution_repository.py   ← Execution state persistence
    mcp_config_repository.py  ← MCP configuration persistence
    workflow_repository.py    ← Workflow definition persistence

  scripts/                    ← One-time migration scripts
    migrate_mcp_config.py
    migrate_schedules.py

  services/                   ← Business logic — orchestrates repositories
    azure_config_manager.py   ← Azure credential management
    azure_devops_client.py    ← Azure DevOps REST API client (PAT auth)
    azure_wiki_client.py      ← Azure Wiki client
    credential_transformer.py ← Credential format conversion
    credential_validator.py   ← Credential validation
    knowledge_base.py         ← Knowledge base management
    mcp_client_manager.py     ← MCP client lifecycle
    provider_schema_registry.py ← LLM provider schema registry
    release_manager.py        ← Release workflow orchestration
    sql_orchestrator.py       ← SQL query orchestration
    trajectory_service.py     ← Investigation trajectory management
    visual_workflow_executor.py ← ReactFlow → agent execution bridge

  workflow/                   ← Agent workflow engine — core of the platform
    engine.py                 ← MAIN entry point for all agent executions
    event_adapter.py          ← Workflow events → SSE events
    event_schema.py           ← Event type definitions
    node_config_generator.py  ← Dynamic node configuration
    workflow_translator.py    ← ReactFlow JSON → agent graph translation
    mcp/
      manager.py              ← MCP server lifecycle in workflow context
      mcp_langchain_adapter.py ← MCP tools → LangChain tools adapter
    strategies/
      base.py                 ← Base agent strategy ABC
      orchestrator.py         ← Multi-agent orchestration strategy
      react.py                ← ReAct single-agent strategy
    tools/
      cloudwatch_agent_tools.py  ← CloudWatch @tool wrappers for agents
      cloudwatch_sanitizer.py    ← Log sanitisation before LLM injection
```

---

## Critical rules — Claude Code must never break these

1. **NEVER hardcode secrets** — all config via `app/config.py` pydantic-settings,
   all values from environment variables. `config/azure_devops_credentials.json`
   is for local dev only — NEVER commit real credentials.

2. **NEVER bypass the repository layer** — endpoints call services,
   services call repositories, repositories call the DB. Never query
   the DB directly from an endpoint or service.

3. **NEVER change `models/db_models.py`** without a corresponding
   migration in `migrations/`. Schema changes without migrations break
   production deployments.

4. **NEVER add new packages** without updating `requirements.txt`
   with a pinned exact version.

5. **NEVER modify `workflow/engine.py`** without reading it completely
   first and showing the full diff before applying. This is the most
   critical file in the repo.

6. **NEVER read entire files into agent context** — use targeted retrieval.
   The code intelligence MCP server (to be built) handles this.

7. **ALWAYS apply `security.py` guards** (to be created) on any new
   outbound HTTP call or file system access.

8. **ALWAYS show diff before applying** any change to an existing
   working file.

9. **ALWAYS run tests after changes** — `pytest tests/ -v`

10. **The MCP servers use stdio transport** — do not change this.

---

## What is working — do not refactor without explicit instruction

- `workflow/engine.py` — main workflow execution engine, working
- `workflow/strategies/react.py` — ReAct agent strategy, working
- `workflow/strategies/orchestrator.py` — multi-agent orchestration, working
- `workflow/workflow_translator.py` — ReactFlow JSON → agent graph, working
- `workflow/mcp/mcp_langchain_adapter.py` — MCP → LangChain bridge, working
- `services/azure_devops_client.py` — Azure DevOps integration, working
- `services/visual_workflow_executor.py` — ReactFlow execution bridge, working
- `core/context_compression.py` — context compression, working
- `core/streaming/callbacks.py` — SSE streaming, working
- `mcp/tools/` — all CloudWatch MCP tools, working
- All `repositories/` — data access layer, working
- All `api/v1/endpoints/` — REST endpoints, working
- Docker + docker-compose — containerisation, working

---

## Known gaps — production readiness work in progress

### Critical (do first)
- [ ] `src/security.py` — SSRF guard, path jail, injection scan — NOT YET CREATED
- [ ] `infrastructure/database/connection.py` — SQLite only, needs PostgreSQL
      for production (pgvector requires PostgreSQL)
- [ ] LangGraph checkpointer — confirm type (MemorySaver vs PostgresSaver)
- [ ] Secrets management — confirm no secrets in code or Docker image
- [ ] `config/azure_devops_credentials.json` — must never reach production

### High
- [ ] pgvector — extension + ivfflat indexes not yet created
- [ ] `pattern_memory` table — confirm upsert vs insert safety
- [ ] Learn node transaction safety — multi-table writes must be atomic
- [ ] SSRF guard not wired into MCP tools that make outbound calls

### Medium
- [ ] Code intelligence layer — AST indexer + code MCP server (planned)
- [ ] Azure DevOps webhook endpoint — incremental repo re-indexing on push
- [ ] Autonomous Curator background job — pattern_memory health maintenance
- [ ] Atropos trajectory format — structured format for future RL fine-tuning

### Low (Phase 2)
- [ ] ProviderTransport ABC — wrap auxiliary_client.py behind interface
- [ ] Per-tool HITL permission model — always-allow / ask / deny per tool
- [ ] Multi-investigation parallel coordination
- [ ] PocketFlow dynamic graph — for user-built ReactFlow workflows

---

## Environment variables — all defined in app/config.py

```
# LLM
ANTHROPIC_API_KEY           required
PROVIDER_TRANSPORT          "anthropic" | "bedrock" (default: anthropic)

# AWS
AWS_REGION                  default: us-east-1
AWS_ACCESS_KEY_ID           leave blank if using IAM role
AWS_SECRET_ACCESS_KEY       leave blank if using IAM role
BEDROCK_REGION              default: us-east-1

# Database
DATABASE_URL                SQLite path (dev) or PostgreSQL URL (prod)
                            format: postgresql+asyncpg://user:pass@host/db

# LangSmith (tracing — free tier)
LANGSMITH_API_KEY           get from smith.langchain.com
LANGSMITH_PROJECT           default: investigation-platform

# Azure DevOps
AZURE_DEVOPS_ORG            your Azure DevOps organisation name
AZURE_DEVOPS_PROJECT        your project name
AZURE_DEVOPS_PAT            Personal Access Token — NEVER commit

# MCP
MCP_SERVER_HOST             default: localhost
MCP_SERVER_PORT             default: 8001

# App
API_HOST                    default: 0.0.0.0
API_PORT                    default: 8000
LOG_LEVEL                   default: INFO
CORS_ORIGINS                comma-separated origins
```

---

## LangGraph specifics

- Agent strategies in `workflow/strategies/` — base.py, react.py, orchestrator.py
- Graph compilation happens in `workflow/engine.py`
- **Checkpointer type: confirm by reading engine.py** — must be AsyncPostgresSaver
  for production (not MemorySaver)
- HITL mechanism: confirm interrupt() is used in synthesis/approval nodes
- Parallel dispatch: confirm Send() is used for multi-agent in orchestrator.py
- State schema: confirm InvestigationState is a TypedDict with Annotated reducers
  for parallel agent findings — not a raw dict

---

## MCP architecture

- MCP server lives in `mcp/` — tools and resources
- MCP client managed by `services/mcp_client_manager.py`
- MCP → LangChain bridge: `workflow/mcp/mcp_langchain_adapter.py`
- CloudWatch tools: `workflow/tools/cloudwatch_agent_tools.py`
- All MCP tool outbound calls MUST go through `security.py` SSRF guard
  once that file is created

---

## Data flow — how a workflow execution works

```
1. POST /api/v1/workflows/{id}/execute
   └── api/v1/endpoints/workflows.py

2. services/visual_workflow_executor.py
   └── translates ReactFlow JSON → workflow config

3. workflow/workflow_translator.py
   └── workflow config → LangGraph graph definition

4. workflow/engine.py
   └── compiles + runs the LangGraph graph

5. workflow/strategies/ (react.py or orchestrator.py)
   └── agent execution strategy

6. workflow/mcp/mcp_langchain_adapter.py
   └── MCP tools available to agents

7. core/streaming/callbacks.py
   └── streams events back via SSE

8. workflow/event_adapter.py
   └── internal events → SSE event schema

9. services/trajectory_service.py
   └── records execution trajectory
```

---

## Reference repos (refer, never copy-paste verbatim)

These repos were analysed as part of architecture design. Refer to them
to understand patterns, then implement fresh in this codebase.

| Repo | What to refer to |
|---|---|
| `github.com/NousResearch/hermes-agent` | Security controls, ProviderTransport, Curator pattern |
| `github.com/agentscope-ai/agentscope` | MCP callable function pattern, memory compression |
| `github.com/openswarm-ai/openswarm` | HITL panel UX, per-tool permissions, cost tracking UI |
| `github.com/langchain-ai/langgraph` | Official docs — Send(), interrupt(), AsyncPostgresSaver |

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_security.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing

# Type checking
mypy app/ --ignore-missing-imports

# Linting
ruff check app/
```

---

## How to run locally

```bash
# Backend
cp .env.example .env          # fill in values
docker-compose up             # starts app + SQLite

# Or without Docker
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

---

## Session rules for Claude Code

1. Read this file first, every session
2. Read the specific file(s) relevant to the task before changing anything
3. Show the full diff before applying any change to existing code
4. Change ONE file at a time — never multi-file changes in one step
5. Run `pytest tests/ -v` after every change
6. Update the "Known gaps" section above when a gap is closed
7. If a change requires a DB schema change, write the Alembic/SQL
   migration before changing the model
8. Never install a new package without asking first
