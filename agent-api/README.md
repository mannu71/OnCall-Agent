# Agent API

Backend for the KYC Protect on-call agent: visual workflow execution, MCP tool
integration, code-intelligence indexing, and real-time SSE monitoring.

---

## What it does

- **Visual workflows** — node graphs (agents, CloudWatch, code analyzer, routers)
  executed by `VisualWorkflowExecutor` and the handler registry under
  `app/workflow/executor/handlers/`.
- **ReAct investigations** — LangGraph agents with supervisor quality gates,
  HITL pauses, steer notes, and knowledge-base recall.
- **Code intelligence** — crawler `indexFlow` builds a knowledge graph
  (tree-sitter) plus LLM-generated repo overviews; semantic search and symbol
  lookup power the code-analyzer nodes.
- **MCP tools** — database, CloudWatch, and custom MCP servers wired through
  `MCPClientManager`.
- **Scheduling** — APScheduler runs cron workflows; heartbeat monitor maps
  CloudWatch alarms to investigation workflows.
- **Persistence** — PostgreSQL for workflows, executions, skills, KG tables,
  and vector embeddings (pgvector).

Legacy **YAML task lists** (shell / python / nested workflow) still run through
`TaskExecutor` for backward compatibility. Python scripts execute in an isolated
subprocess, not via in-process `exec()`.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| API | FastAPI, Pydantic Settings |
| Workflows | Visual executor + strategy pattern (ReAct, batch, router) |
| Agents | LangGraph, LangChain (Bedrock / OpenAI / Anthropic / …) |
| Data | PostgreSQL + asyncpg, SQL migrations in `migrations/` |
| Vectors | pgvector via `EmbeddingService` |
| Code crawl | tree-sitter KG + PocketFlow-style `AsyncFlow` DAG |
| Streaming | Server-Sent Events (executions, scheduler, log watch) |
| UI pairing | React editor in `../ui` (Langflow-style node editor) |

---

## Project layout

```
agent-api/
├── app/
│   ├── api/v1/endpoints/     # REST + SSE routes
│   ├── core/                 # scheduler, heartbeat, executor, settings consumers
│   ├── crawler/              # index/search flows and nodes
│   ├── engine/crawler_engine/# AsyncFlow / AsyncNode framework
│   ├── infrastructure/persistence/  # canonical repositories
│   ├── services/             # visual executor, crawler, log watch, KB
│   └── workflow/             # strategies, handlers, routing, LLM config
├── migrations/               # numbered SQL (run in order)
├── tests/                    # pytest suite
└── pyproject.toml
```

---

## Quick start

### Prerequisites

- Python 3.11+
- PostgreSQL with pgvector (or use repo `docker-compose`)

### Local run

```bash
cd agent-api
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Set DATABASE_URL and related env vars (see app/config.py / .env.example)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Database migrations

Apply SQL files in order:

```bash
for f in migrations/*.sql; do
  psql "$DATABASE_URL" -f "$f"
done
```

`init_db()` verifies connectivity only — it does **not** call `create_all`.

---

## Configuration

Settings live in `app/config.py` (Pydantic `Settings`, env-prefix friendly).
Common variables:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REPOS_BASE_PATH` | Root directory for on-disk code repos |
| `MAX_CONCURRENT_WORKFLOWS` | Global workflow semaphore |
| `PARALLEL_FLOW_CONCURRENCY` | Batch / BFS node parallelism |
| `INDEX_PARSE_CONCURRENCY` | Parallel tree-sitter parse cap during index |
| `SUPERVISOR_TOKEN_BUDGET` | Cap LLM tokens across supervisor retries |
| `AWS_REGION` / Bedrock keys | LLM and CloudWatch access |

See `app/config.py` for the full list.

---

## Workflow execution paths

```
API / scheduler / heartbeat
        │
        ▼
  workflow/routing.py  ── is_visual_workflow?
        │
        ├─ yes → VisualWorkflowExecutor → handler registry → strategies
        └─ no  → TaskExecutor (legacy YAML tasks)
```

Visual workflows stream events over SSE (`/executions/{id}/stream`). Agent
nodes receive an `ExecutionPort` for trace IDs, steer notes, and HITL events
without coupling strategies to the executor singleton.

---

## Code indexing (`indexFlow`)

Pipeline (see `app/crawler/flows/index_flow.py`):

1. **FetchRepo** — walks the repo; stores paths only (not full file bodies).
2. **KG nodes** — incremental SHA diff, parallel AST parse, graph persist.
3. **LLM nodes** — abstractions + relationships (best-effort; KG works even if
   LLM refuses).
4. **PersistOverview** — writes `repo_abstractions`.

Trigger via crawler API or `CrawlerService.index_repo()`.

---

## Tests

```bash
cd agent-api
pytest tests/ app/core/memory app/core/streaming -q
```

The suite covers routing, persistence, performance remediations, repo discovery,
execution port, index streaming, and handler registry behaviour.

---

## Security notes

- Repo paths are jailed under `REPOS_BASE_PATH` (`app/core/security.py`).
- Legacy Python workflow tasks run in a **subprocess** with timeout; they do
  not share the API process memory space.
- MCP and AWS credentials should be supplied via env / secrets manager, not
  committed to the repo.

---

## Related docs

- Root README: `../README.md`
- UI: `../ui/README.md`
- Memory subsystem: `app/core/memory/README.md`
- Streaming callbacks: `app/core/streaming/README.md`
