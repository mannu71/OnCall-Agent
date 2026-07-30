# OnCall Agent

A web application for building and running AI-powered on-call investigation workflows. The UI is a React app served via nginx in Docker (or Vite during local dev); the backend is a Python FastAPI service that orchestrates agents, MCP tool servers, scheduled runs, and real-time execution streaming.

## Project structure

```
kyc-protect-oncall-agent/
├── docker-compose.yml      # The single full-stack definition: postgres + backend + headroom + UI
├── agent-api/              # Python FastAPI backend (workflows, agents, MCP, scheduler)
│   ├── app/                # Application code (see "Repository layout" below)
│   ├── migrations/         # PostgreSQL schema: 001_schema.sql, 002_fts_only_retrieval.sql
│   └── requirements.txt    # Python dependencies
├── ui/                     # React frontend (Docker nginx or Vite dev)
│   ├── src/                # React app (workflow builder, scheduler, settings)
│   ├── Dockerfile          # UI container
│   └── package.json
├── docs/                   # Architecture and harness docs
├── setup.ps1               # One-command setup (Windows PowerShell)
├── setup.sh                # One-command setup (Linux / macOS)
├── setup.bat               # Windows shortcut → setup.ps1
└── README.md
```

The root `docker-compose.yml` is the **only** compose file — it builds and runs
the whole stack (postgres, agent-api, headroom, ui). There are no per-service
compose files.

## Prerequisites

**To run the app, you need Docker and Git. That is the whole list.** The UI and
the backend are each compiled inside their own container, so no Node or Python
is installed on your machine.

| Tool | Version | Why |
|------|---------|-----|
| **Docker Engine** | 23+ (or Docker Desktop) | Builds and runs the whole stack |
| **Docker Compose** | **v2** (`docker compose`, with a space) | See the note below |
| **Git** | Latest | Clone the repository |

> **Compose v2 is required, not just preferred.** `agent-api/Dockerfile` is a
> BuildKit Dockerfile — it uses `RUN --mount=type=cache` to keep rebuilds fast.
> The legacy `docker-compose` v1 binary drives the old builder, which cannot
> parse that syntax, so it fails part-way through the build with an error that
> does not mention the real cause. Check yours with `docker compose version`; if
> that command is not found but `docker-compose --version` reports 1.x, install
> Compose v2: <https://docs.docker.com/compose/install/>.
>
> Not sure? Run `./setup.sh --check` (Linux/macOS) — it diagnoses Docker, the
> daemon, your Compose version and host-port conflicts, and changes nothing.

Only needed if you want **local hot-reload development** (`--dev` / `-DevSetup`);
skip them otherwise:

| Tool | Version | Why |
|------|---------|-----|
| **Node.js** | 18+ | Vite dev server, and `npx`-based MCP servers |
| **npm** | 9+ | Frontend package management |
| **Python** | 3.12+ | Running the backend outside its container |

Optional but recommended:

| Tool | Purpose |
|------|---------|
| **AWS CLI + credentials** | Bedrock LLM calls, CloudWatch, and other AWS integrations |
| **PostgreSQL client (`psql`)** | Run database migrations locally (or use `docker exec` instead) |
| **Azure DevOps PAT** | Release management features in the UI |

---

## Quick setup (recommended)

After cloning the repo, run the setup script from the repository root — one command takes a fresh clone to a running stack. It checks prerequisites, creates `.env` files, starts PostgreSQL in Docker, applies the migration, builds and starts the full container stack, and waits for the API health check. A local Python venv and UI `npm install` are only done when you pass `-DevSetup` / `--dev` (a container-only user needs neither).

**Windows:**

```powershell
git clone https://dev.azure.com/creditsafe/Compliance/_git/kyc-protect-oncall-agent
cd kyc-protect-oncall-agent
.\setup.bat
```

Or directly with PowerShell:

```powershell
.\setup.ps1
```

**Linux / macOS:**

```bash
git clone https://dev.azure.com/creditsafe/Compliance/_git/kyc-protect-oncall-agent
cd kyc-protect-oncall-agent
chmod +x setup.sh
./setup.sh
```

### Setup script options

| Flag | Windows | Linux / macOS | Description |
|------|---------|---------------|-------------|
| Diagnose only | _(n/a)_ | `--check` | Check Docker, Compose version and host ports, then exit. Changes nothing — run this first if setup fails. |
| Skip Docker | `-SkipDocker` | `--skip-docker` | Use your own PostgreSQL instance (apply `migrations/001_schema.sql` yourself) |
| Skip migrations | `-SkipMigrations` | `--skip-migrations` | Skip the SQL migration |
| Dev setup | `-DevSetup` | `--dev` | Also set up local dev: create the Python venv, `pip install`, and UI `npm install`. Off by default. |
| Reset containers | `-Reset` | _(n/a)_ | Recreate containers from scratch (down + up --build). **Database is preserved.** |
| Wipe database | `-WipeData` | _(n/a)_ | DESTRUCTIVE — delete the Postgres volume so the DB starts empty (prompts for typed confirmation). The only option that erases data. |

When the script finishes, the full Docker stack is already running at **http://localhost:43000**. To run it again later (or after `-Reset`), from the repo root:

```bash
docker compose up --build -d
# Open http://localhost:43000
```

**Local development** (requires `-DevSetup` / `--dev` first, to create the venv and install UI deps):

```bash
# Terminal 1 — backend
cd agent-api
# Windows: .\venv\Scripts\activate
# Linux/macOS: source venv/bin/activate
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000

# Terminal 2 — UI
cd ui
npm run dev
# Open http://localhost:45173
```

---

## Manual setup (step by step)

Use this if you prefer to run each step yourself or need to troubleshoot the automated script.

### Step 1 — Clone the repository

```bash
git clone https://dev.azure.com/creditsafe/Compliance/_git/kyc-protect-oncall-agent
cd kyc-protect-oncall-agent
```

### Step 2 — Start PostgreSQL

The backend stores workflows, executions, code intelligence, and related data in PostgreSQL with the **pgvector** extension.

**Option A — Docker (recommended):**

```bash
# From the repo root
docker compose up -d postgres
```

This starts a container named `kyc-agent-db` (host port **45432**) with:

- User: `kycuser`
- Password: `kycpassword`
- Database: `kycagent`

Wait until the container is healthy:

```bash
docker compose ps
```

**Option B — Local PostgreSQL:** Install PostgreSQL 16+ with pgvector, create the `kycagent` database and `kycuser` role, then point `DATABASE_URL` at your instance (see Step 4).

### Step 3 — Apply the database schema

The schema is a single squashed baseline, `agent-api/migrations/001_schema.sql`. It must be applied **before** first use; the API does not auto-create tables. `setup.ps1` / `setup.sh` apply it for you via the Docker migration runner and track it in a `schema_migrations` table (re-runs are a no-op, and a pre-existing database is stamped without re-executing). To apply it by hand:

```bash
# From agent-api/ — pipe the file into psql in the DB container
docker exec -i kyc-agent-db psql -v ON_ERROR_STOP=1 -U kycuser -d kycagent < migrations/001_schema.sql
```

Later schema changes are numbered files in the same directory — currently `002_fts_only_retrieval.sql` (drops the embedding stack; see its header for the required pre-step) — applied by re-running setup, or by piping each file in the same way.

### Step 4 — Configure the backend (optional)

Create `agent-api/.env` only if you need non-default settings. The defaults work for local development with the Docker database above.

```env
# Database (host port matches the docker-compose postgres mapping, 45432)
DATABASE_URL=postgresql://kycuser:kycpassword@localhost:45432/kycagent

# LLM provider: anthropic | bedrock | openai
PROVIDER_TRANSPORT=bedrock
AWS_REGION=eu-west-1
AWS_PROFILE=your-aws-profile

# Code analyzer — local folder containing repos to index
REPOS_BASE_PATH=C:/path/to/your/repos

# Logging
LOG_LEVEL=INFO
```

For **AWS Bedrock**, configure credentials before starting the API:

```bash
aws configure --profile your-aws-profile
# or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the environment
```

Set `REPOS_HOST_PATH` in a `.env` file at the repo root (or export it in your shell) so the full stack can mount your repos.

### Step 5 — Install backend dependencies

**Option A — Virtual environment (recommended for local dev):**

```bash
cd agent-api
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

**Option B — Via UI postinstall** (installs into the active Python environment):

```bash
cd ui
npm install
```

The `postinstall` script runs `pip install -r requirements.txt` in `agent-api/`.

### Step 6 — Install frontend dependencies

```bash
cd ui
npm install
```

### Step 7 — Start the backend API

**Local development (recommended while working on the UI):**

```bash
cd agent-api

# With venv activated
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000
```

**Docker (backend + database together):**

Compose runs from the **repo root** — `docker-compose.yml` lives there and the
build context is the root, so `agent-api/` is not a valid working directory for it.

```bash
docker compose up --build -d agent-api
```

Verify the API is running:

- Health: http://localhost:48000/api/v1/health
- Swagger docs: http://localhost:48000/docs

### Step 8 — Start the UI

**Full Docker stack (recommended for deployment):**

```bash
# From repo root — builds and starts all containers
docker compose up --build
```

Open **http://localhost:43000** in your browser. The UI container (`kyc-agent-ui`) proxies `/api` to the backend container (`kyc-agent-api`) over the shared Docker network. (The code-intel engine is built inside `agent-api/Dockerfile`; there is no separate build step.)

**Local UI development (hot reload):**

In a **separate terminal** (with backend running from Step 7 or Docker):

```bash
cd ui
npm run dev
```

This starts the Vite dev server on **http://localhost:45173**. Vite proxies `/api` to `http://localhost:48000`.

Optional frontend env file `ui/.env`:

```env
VITE_AGENT_API_URL=http://localhost:48000
VITE_API_URL=http://localhost:48000
```

### Step 9 — Verify the installation

1. Open http://localhost:43000 (Docker) or http://localhost:45173 (Vite dev).
2. Confirm **Settings** loads and the backend health check succeeds.
3. Open http://localhost:48000/docs and confirm the API responds.

---

## First-time application configuration

After the stack is running, configure the app through the UI:

### 1. Configure MCP servers (Settings)

MCP servers provide tools (PostgreSQL, Azure DevOps, CloudWatch, etc.) to agents.

1. Go to **Settings → MCP Servers**.
2. Click **Add Server**.
3. Example PostgreSQL server:
   - **Command:** `npx`
   - **Args** (one per line):
     ```
     -y
     @modelcontextprotocol/server-postgres@0.6.2
     postgresql://username:password@hostname:5432/database?sslmode=require
     ```
   - **Environment variables** (for AWS RDS SSL):
     ```json
     {
       "NODE_EXTRA_CA_CERTS": "C:\\path\\to\\global-bundle.pem"
     }
     ```
4. Click **Save** — the connection is tested automatically.

Download the AWS RDS CA bundle if needed: https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem

### 2. Configure LLM / model keys (Settings)

Add API keys or confirm AWS Bedrock access for the models your workflows use.

### 3. Create a workflow (Workflow page)

1. Drag nodes onto the canvas and connect them.
2. Attach MCP servers and agent configuration to tool nodes.
3. Save the workflow.

### 4. Schedule or run (Scheduler / Workflow)

- **Run now** from the workflow page for ad-hoc investigations.
- **Scheduler** page: add cron schedules for recurring runs.

---

## Run with Docker (full stack)

The root `docker-compose.yml` is the single stack definition. From the **repo root**:

```bash
# 1. Start database (if not already running)
docker compose up -d postgres

# 2. Apply the schema (see Step 3) — setup.ps1 / setup.sh do this for you

# 3. Build and start postgres + backend + headroom + UI
docker compose up --build
```

Open the app at **http://localhost:43000**. The code-intel engine is built inside `agent-api/Dockerfile` — there is no separate `docker build` step.

For local UI development against the Docker backend:

```bash
# From repo root — start just the backend services
docker compose up -d postgres agent-api

# Then the Vite dev server (needs `--dev` deps installed first)
cd ui
npm run dev
```

Useful Docker commands (from repo root):

| Command | Description |
|---------|-------------|
| `docker compose logs -f agent-api` | Follow backend logs |
| `docker compose logs -f ui` | Follow UI/nginx logs |
| `docker compose down` | Stop all containers |
| `../rebuild-docker.bat` | Rebuild backend + UI without losing DB data (Windows) |
| `../rebuild-docker.sh` | Same on Linux/macOS |

---

## Development commands

### UI (`ui/`)

| Command | Description |
|---------|-------------|
| `npm run dev` | Vite dev server on port 45173 (browser) |
| `npm run dev:vite` | Same as `npm run dev` |
| `npm run build` | Build React frontend for production |
| `npm run preview` | Preview production build locally |
| `npm run lint` | Run ESLint |

### Backend (`agent-api/`)

| Command | Description |
|---------|-------------|
| `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 48000` | Dev server with reload |
| `pytest tests/ -v` | Run unit tests |
| `pytest -m eval -v` | Run eval harness (see `agent-api/evals/README.md`) |
| `python -m evals.harness_selftest` | Deterministic harness regression checks |

---

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| UI cannot reach API | Backend running on port 48000? Check http://localhost:48000/api/v1/health |
| Database errors on startup | Migrations applied? Postgres container healthy? `DATABASE_URL` correct? |
| MCP server connection fails | Node.js installed? `npx` works? Connection string and SSL certs correct? |
| Bedrock / AWS errors | `AWS_PROFILE` or credentials configured? `PROVIDER_TRANSPORT=bedrock` set? |
| Code analyzer cannot see repos | `REPOS_BASE_PATH` (local) or docker-compose volume mount points at your repos |
| UI shows API errors in Docker | Check `docker compose logs ui agent-api`; confirm http://localhost:43000/api/v1/health |
| Docker build fails at `npm ci` / pip | Corporate proxy? See **Setup on a locked-down / corporate machine** below |
| A host port is already in use | Override `POSTGRES_HOST_PORT` / `API_HOST_PORT` / `UI_HOST_PORT` / `HEADROOM_HOST_PORT` in the root `.env` |

---

## Host ports

The stack publishes on uncommon host ports so it doesn't collide with anything already running on your machine. The container-internal ports never change; only the host side is remapped.

| Service | URL | Override in root `.env` |
|---------|-----|-------------------------|
| UI | http://localhost:43000 | `UI_HOST_PORT` |
| API | http://localhost:48000 | `API_HOST_PORT` |
| Postgres | localhost:45432 | `POSTGRES_HOST_PORT` |
| headroom (loopback) | 127.0.0.1:48787 | `HEADROOM_HOST_PORT` |
| Vite dev server | http://localhost:45173 | `PORT` env |

If a default is taken, set the matching variable in the repo-root `.env` (copy `.env.example` first) and re-run — no file edits needed. `setup.bat` / `setup.sh` check these ports up front and tell you exactly which variable to set.

---

## Setup on a locked-down / corporate machine

Two things commonly bite fresh setups behind a corporate network:

1. **Checkout** — use the `feature/v3-optimized-version` branch. `main` predates the UI Dockerfile and committed lockfile.
2. **Docker build fails at `npm ci` or `pip install`** — almost always the corporate proxy / SSL inspection blocking the registries from inside the build. To see the real error (it's printed *above* the `exit code: 1` line, which the summary hides):

   ```bash
   docker compose build ui --progress=plain --no-cache
   ```

   - `ETIMEDOUT` / `ECONNREFUSED` / `EAI_AGAIN` → proxy not reachable from the build. Set `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` in the root `.env` (they're forwarded to the build), or configure Docker Desktop → Settings → Resources → Proxies. If your org runs an internal npm mirror, set `NPM_REGISTRY` too.
   - `SELF_SIGNED_CERT` / `UNABLE_TO_VERIFY_LEAF_SIGNATURE` → SSL-inspection cert. The UI build already sets `strict-ssl false` and trusts the inspected cert; if it still fails, confirm the proxy vars above are set so npm reaches the registry at all.
   - `EUSAGE: ... lock file ... not in sync` → your checkout has local edits to `ui/package.json` or a stale `ui/package-lock.json`. Run `git status ui/` and discard the drift.

---

## Repository layout

Top level:

| Path | What it is |
|------|-----------|
| `agent-api/` | Python FastAPI backend (see the package map below) |
| `ui/` | React frontend (workflow builder, scheduler, settings) |
| `docs/` | Architecture and agent-harness documentation |
| `docker-compose.yml` | The single full-stack definition |
| `setup.ps1` / `setup.sh` | One-command setup |

`agent-api/app/` packages:

| Package | Responsibility |
|---------|----------------|
| `api/` | FastAPI routes and dependencies |
| `harness/` | The long-running agent harness (turn loop, context, tools, planning) |
| `workflow/` | Workflow execution — strategies, node catalog, and `graph_engine/` (the DAG engine) |
| `services/` | Application services (log watch, visual workflow executor, Azure config, …) |
| `mcp/` | MCP client/tooling integration |
| `models/`, `schemas/` | ORM models and Pydantic schemas |
| `core/` | Cross-cutting building blocks, grouped into intent-named subpackages |

`agent-api/app/core/` subpackages:

| Subpackage | Contents |
|-----------|----------|
| `context/` | Context-window machinery: compaction, tool-output sizing, references |
| `llm/` | LLM call utility, model metadata/router/throttle, prompt caching, token calibration |
| `aws/` | AWS credentials + CloudWatch cache/rate-limit + trace ids |
| `concurrency/` | map/reduce, parallel fan-out, thread pools, locks |
| `resilience/` | retry, circuit breaker, rate-limit tracking |
| `runtime/` | scheduler, executor, heartbeat, events, timezone, TTL cache |
| `observability/` | telemetry, API logging, log filtering, notifications |
| `quality/` | answer supervision, grading, grounding, intent |
| `memory/` | durable-fact extraction + memory curator |
| `privacy/`, `streaming/`, `tools/`, `improvement/`, `transport/`, `policy/`, `sandbox/`, `skills/`, `vfs/`, `knowledge/`, `governance/`, `supervision/`, `code_semantic/` | Focused capability packages |

Foundational modules (`database`, `exceptions`, `dependencies`, `logging`, `feature_flags`, `security`) stay at the `core/` top level. Subpackages have docstring-only `__init__` — import submodules directly (e.g. `from app.core.llm.call_llm import ...`).

## Documentation

- [Agent API README](agent-api/README.md) — API details, the migration file, and Docker deployment
- [Eval harness](agent-api/evals/README.md) — `pytest -m eval` regression gate
- [UI developer guide](ui/AGENTS.md) — frontend architecture and conventions
