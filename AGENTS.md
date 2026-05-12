# AGENTS.md — KYC Protect On-Call Agent

Guidance for AI coding agents (Copilot, Cursor, Claude, etc.) working in this repository.

---

## Repository Overview

A full-stack **on-call intelligence platform** for KYC Protect, consisting of:

| Layer | Location | Tech |
|---|---|---|
| Backend API | `agent-api/` | Python · FastAPI · LangChain · SQLite |
| Frontend UI | `ui/` | React · Vite · Electron · Tailwind · shadcn/ui |
| Graph RAG experiment | `graph-rag-dotnet/` | Python · GraphRAG |

---

## Project Structure

### Backend — `agent-api/`

```
agent-api/
├── app/
│   ├── main.py                     # FastAPI app entry point
│   ├── config.py                   # Env/settings via pydantic-settings
│   ├── api/
│   │   ├── deps.py                 # Shared FastAPI dependencies
│   │   ├── middleware/
│   │   │   └── error_handler.py
│   │   └── v1/
│   │       ├── api.py              # Router registration
│   │       ├── endpoints/          # One file per resource:
│   │       │   ├── certificates.py
│   │       │   ├── executions.py
│   │       │   ├── health.py
│   │       │   ├── insights.py
│   │       │   ├── llm_config.py
│   │       │   ├── log_watch.py
│   │       │   ├── mcp_config.py
│   │       │   ├── model_keys.py
│   │       │   ├── releases.py
│   │       │   ├── settings.py
│   │       │   ├── skills.py
│   │       │   ├── trajectories.py
│   │       │   ├── usage.py
│   │       │   └── workflows.py
│   │       └── schemas/            # Pydantic request/response schemas
│   ├── application/
│   │   ├── events.py               # App lifecycle events
│   │   ├── exceptions.py
│   │   └── logging_config.py
│   ├── core/                       # Cross-cutting infrastructure
│   │   ├── auxiliary_client.py     # LLM provider abstraction
│   │   ├── circuit_breaker.py
│   │   ├── context_compression.py
│   │   ├── context_references.py
│   │   ├── error_classifier.py
│   │   ├── executor.py             # Async task executor
│   │   ├── model_metadata.py
│   │   ├── prompt_caching.py
│   │   ├── rate_limit_tracker.py
│   │   ├── retry.py
│   │   ├── scheduler.py
│   │   ├── tool_registry.py
│   │   ├── memory/
│   │   │   ├── builtin.py          # Built-in memory backends
│   │   │   └── manager.py          # Memory lifecycle management
│   │   ├── skills/
│   │   │   └── manager.py          # Skill loading and invocation
│   │   └── streaming/
│   │       └── callbacks.py        # SSE/streaming LangChain callbacks
│   ├── domain/
│   │   ├── interfaces/             # (empty — ABCs go here)
│   │   └── services/
│   │       └── llm_discovery_service.py
│   ├── infrastructure/
│   │   └── database/
│   │       └── connection.py       # SQLite async connection
│   ├── mcp/                        # Model Context Protocol server
│   │   ├── resources/
│   │   │   └── cloudwatch_logs_resource.py
│   │   └── tools/
│   │       ├── alert_tools.py
│   │       ├── analysis_tools.py
│   │       ├── correlation_tools.py
│   │       ├── search_tools.py
│   │       └── watch_tools.py
│   ├── models/                     # SQLAlchemy + Pydantic data models
│   │   ├── azure_devops.py
│   │   ├── azure_wiki.py
│   │   ├── db_models.py
│   │   ├── error_response.py
│   │   └── workflow.py
│   ├── repositories/
│   │   ├── base.py
│   │   ├── db_repository.py
│   │   ├── execution_repository.py
│   │   ├── mcp_config_repository.py
│   │   └── workflow_repository.py
│   ├── scripts/
│   │   ├── migrate_mcp_config.py
│   │   └── migrate_schedules.py
│   ├── services/                   # Business logic services
│   │   ├── azure_config_manager.py
│   │   ├── azure_devops_client.py
│   │   ├── azure_wiki_client.py
│   │   ├── credential_transformer.py
│   │   ├── credential_validator.py
│   │   ├── knowledge_base.py
│   │   ├── mcp_client_manager.py
│   │   ├── provider_schema_registry.py
│   │   ├── release_manager.py
│   │   ├── sql_orchestrator.py
│   │   ├── trajectory_service.py
│   │   └── visual_workflow_executor.py
│   └── workflow/                   # Agent workflow engine
│       ├── engine.py               # Main workflow execution engine
│       ├── event_adapter.py
│       ├── event_schema.py
│       ├── node_config_generator.py
│       ├── workflow_translator.py
│       ├── mcp/
│       │   ├── manager.py
│       │   └── mcp_langchain_adapter.py
│       ├── strategies/
│       │   ├── base.py
│       │   ├── orchestrator.py     # Multi-agent orchestration
│       │   └── react.py            # ReAct agent strategy
│       └── tools/
│           ├── cloudwatch_agent_tools.py
│           └── cloudwatch_sanitizer.py
├── config/
│   └── azure_devops_credentials.json   # ⚠ NEVER commit real credentials
├── data/certs/
├── migrations/
├── tests/                          # pytest suite
├── docker-compose.yml
├── Dockerfile
├── init-db.sql
├── pyproject.toml
├── requirements.txt
└── run.sh
```

### Frontend — `ui/`

```
ui/
├── src/
│   ├── main.jsx                    # React entry point
│   ├── App.jsx                     # Root component / routing
│   ├── pages/
│   │   ├── Chat.jsx                # Agent chat interface
│   │   ├── Dashboard.jsx
│   │   ├── Releases.jsx            # Release management page
│   │   ├── Scheduler.jsx
│   │   ├── Settings.jsx
│   │   └── workflow.jsx            # Visual workflow builder page
│   ├── components/
│   │   ├── logwatch/
│   │   │   └── LogWatchConfig.jsx
│   │   ├── monitoring/
│   │   │   ├── ExecutionLog.jsx
│   │   │   ├── ExecutionMonitor.jsx
│   │   │   └── StatusBadge.jsx
│   │   ├── releases/
│   │   │   ├── AzureDevOpsSettings.jsx
│   │   │   ├── ConflictDiffViewer.jsx
│   │   │   ├── ConflictResolutionInterface.jsx
│   │   │   ├── CreateReleaseWizard.jsx
│   │   │   ├── Toast.jsx / useToast.js
│   │   │   └── ...error/loading helpers
│   │   ├── scheduler/
│   │   │   ├── AddScheduleDialog.jsx
│   │   │   ├── EditScheduleDialog.jsx
│   │   │   ├── ScheduleCard.jsx
│   │   │   └── ScheduleList.jsx
│   │   ├── settings/
│   │   │   └── ModelKeyDialog.jsx
│   │   ├── sidebar/
│   │   │   └── AppSidebar.jsx
│   │   ├── ui/                     # shadcn/ui primitives
│   │   │   └── (button, card, dialog, input, select, table, …)
│   │   └── workflow/
│   │       ├── WorkflowEditor.jsx  # React Flow canvas
│   │       ├── NodeConfigPanel.jsx
│   │       ├── NodeSidebar.jsx
│   │       ├── AgentAdvancedConfig.jsx
│   │       ├── TrajectoryReplay.jsx
│   │       └── nodes/
│   │           ├── OrchestratorNode.jsx
│   │           └── OutputNode.jsx
│   ├── context/
│   │   ├── SchedulerContext.jsx
│   │   └── WorkflowStatusContext.jsx
│   ├── hooks/
│   │   ├── use-mobile.js
│   │   └── useWorkflowStream.js    # SSE hook for live workflow events
│   ├── services/                   # API clients
│   │   ├── apiClient.js            # General REST calls
│   │   ├── agentApiClient.js       # Workflow/LLM-specific calls
│   │   ├── llmService.js
│   │   ├── mcpService.js
│   │   └── modelKeyService.js
│   └── lib/ / utils/
├── electron/                       # Electron shell for desktop app
├── public/
├── components.json                 # shadcn/ui config
├── tailwind.config.js
└── vite.config.js
```

---

## Architectural Conventions

### Backend

- **Layer order**: `api/endpoints` → `services/` → `repositories/` → `infrastructure/`.  
  Do not import upward (e.g., `services` must not import from `api`).
- **Pydantic everywhere**: all request/response bodies and config use Pydantic v2 models.
- **Async by default**: use `async def` for all endpoints and DB operations.
- **Repository pattern**: database access goes through `repositories/`; raw SQLAlchemy stays out of services.
- **Two API client types in the frontend**: `apiClient.js` handles general CRUD; `agentApiClient.js` handles streaming workflow/LLM calls. Keep them separate.
- **MCP tools** live in `app/mcp/tools/` (server-side MCP exposure) and `app/workflow/tools/` (tools available to the workflow engine). They are distinct.

### Frontend

- All UI primitives come from `src/components/ui/` (shadcn/ui). Do not reach into `node_modules` directly for shadcn components.
- Page-level state lives in `context/`; component-local state in `useState`/`useReducer`.
- Streaming workflow events are consumed via `useWorkflowStream` hook — do not poll; use SSE.

---

## Running the Project

### Backend
```bash
cd agent-api
pip install -r requirements.txt
python -m app.main          # or: ./run.sh
# Docker:
docker compose up --build
```

### Frontend
```bash
cd ui
npm install
npm run dev          # Vite dev server
npm run electron     # Electron desktop
```

### Database migrations
```bash
cd agent-api
python run_migration.py      # or: run-migration.bat (Windows)
```

---

## Testing

```bash
# Backend — from agent-api/
pytest                          # full suite
pytest tests/test_<name>.py    # single file

# Frontend — from ui/
npm test
```

Test files live in `agent-api/tests/`. Fixtures and shared helpers are in `tests/conftest.py`.

---

## Key Files to Know

| File | Purpose |
|---|---|
| `agent-api/app/main.py` | FastAPI app setup, middleware, router registration |
| `agent-api/app/config.py` | All environment variables / secrets (loaded via pydantic-settings) |
| `agent-api/app/workflow/engine.py` | Central workflow execution loop |
| `agent-api/app/workflow/strategies/react.py` | ReAct agent implementation |
| `agent-api/app/core/auxiliary_client.py` | Multi-provider LLM client abstraction |
| `agent-api/app/services/release_manager.py` | Release creation and conflict resolution logic |
| `agent-api/app/mcp/tools/` | MCP tool implementations (CloudWatch, alerts, etc.) |
| `ui/src/services/agentApiClient.js` | Frontend SSE + workflow API calls |
| `ui/src/hooks/useWorkflowStream.js` | React hook for live workflow event streaming |
| `ui/src/components/workflow/WorkflowEditor.jsx` | React Flow visual workflow builder |

---

## Security Notes

- Credentials are stored in `agent-api/config/azure_devops_credentials.json` — this file must **never** be committed with real values. Use environment variables in production.
- The `core/redact.py` module handles log redaction of sensitive fields — ensure it is used whenever logging request/response data.
- API keys for LLM providers are managed via `model_keys` endpoints and stored encrypted; see `app/services/credential_transformer.py`.

---

## What Is Not Here

- The `graph-rag-dotnet/` directory is an experimental GraphRAG prototype — it is **not** part of the main application and should not be referenced from `agent-api/` or `ui/`.
- There is no shared Python package between `agent-api/` and the top-level `app/`, `domain/`, `infrastructure/` folders — those appear to be stale scaffolding artefacts and should be ignored unless you confirm otherwise.
