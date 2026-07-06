# OnCall Agent

A web application for building and running AI-powered on-call investigation workflows. The UI is a React app served via nginx in Docker (or Vite during local dev); the backend is a Python FastAPI service that orchestrates agents, MCP tool servers, scheduled runs, and real-time execution streaming.

## Project structure

```
kyc-protect-oncall-agent/
├── docker-compose.yml      # Full stack: postgres + backend + UI
├── agent-api/              # Python FastAPI backend (workflows, agents, MCP, scheduler)
│   ├── app/                # Application code
│   ├── migrations/         # PostgreSQL schema migrations (run manually)
│   ├── docker-compose.yml  # Backend only: postgres + agent-api
│   ├── run-migration.bat   # Apply migrations (Windows)
│   ├── run-migration.sh    # Apply migrations (Linux/macOS)
│   └── requirements.txt    # Python dependencies
├── ui/                     # React frontend (Docker nginx or Vite dev)
│   ├── src/                # React app (workflow builder, scheduler, settings)
│   ├── Dockerfile          # UI container
│   ├── docker-compose.yml  # UI only (joins backend network)
│   └── package.json
├── setup.ps1               # Automated first-time setup (Windows PowerShell)
├── setup.sh                # Automated first-time setup (Linux / macOS)
├── setup.bat               # Windows shortcut → setup.ps1
└── README.md
```

## Prerequisites

Install these before setting up the project:

| Tool | Version | Purpose |
|------|---------|---------|
| **Node.js** | 18+ | UI dev server and MCP servers (`npx`) |
| **npm** | 9+ | Frontend package management |
| **Python** | 3.12+ | Backend API |
| **Docker Desktop** | Latest | PostgreSQL (pgvector) and optional containerized backend |
| **Git** | Latest | Clone the repository |

Optional but recommended:

| Tool | Purpose |
|------|---------|
| **AWS CLI + credentials** | Bedrock LLM calls, CloudWatch, and other AWS integrations |
| **PostgreSQL client (`psql`)** | Run database migrations locally (or use `docker exec` instead) |
| **Azure DevOps PAT** | Release management features in the UI |

---

## Quick setup (recommended)

After cloning the repo, run the setup script from the repository root. It checks prerequisites, creates `.env` files, starts PostgreSQL in Docker, applies migrations, and installs Python + npm dependencies.

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
| Skip Docker | `-SkipDocker` | `--skip-docker` | Use your own PostgreSQL instance |
| Skip migrations | `-SkipMigrations` | `--skip-migrations` | Skip SQL migrations |
| Skip dependencies | `-SkipDeps` | `--skip-deps` | Skip pip / npm install |

When setup finishes, start the app:

**Full Docker stack:**

```bash
docker build -t codegraph:latest ./codegraph
docker compose up --build
# Open http://localhost:8080
```

**Local development:**

```bash
# Terminal 1 — backend
cd agent-api
# Windows: .\venv\Scripts\activate
# Linux/macOS: source venv/bin/activate
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2 — UI
cd ui
npm run dev
# Open http://localhost:5173
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
cd agent-api
docker-compose up -d postgres
```

This starts a container named `kyc-agent-db` on port **5432** with:

- User: `kycuser`
- Password: `kycpassword`
- Database: `kycagent`

Wait until the container is healthy:

```bash
docker-compose ps
```

**Option B — Local PostgreSQL:** Install PostgreSQL 16+ with pgvector, create the `kycagent` database and `kycuser` role, then point `DATABASE_URL` at your instance (see Step 4).

### Step 3 — Run database migrations

Migrations are plain SQL files in `agent-api/migrations/`. They must be applied **before** first use; the API does not auto-create tables.

**Windows** (from `agent-api/`):

```powershell
.\run-migration.bat
```

**Linux / macOS** (from `agent-api/`):

```bash
chmod +x run-migration.sh
./run-migration.sh
```

**Using Docker when `psql` is not installed locally:**

```powershell
# Windows PowerShell — run from agent-api/
Get-ChildItem migrations\*.sql | Sort-Object Name | ForEach-Object {
  Get-Content $_.FullName | docker exec -i kyc-agent-db psql -U kycuser -d kycagent
}
```

```bash
# Linux / macOS — run from agent-api/
for f in migrations/*.sql; do
  echo "Running $f..."
  docker exec -i kyc-agent-db psql -U kycuser -d kycagent < "$f"
done
```

### Step 4 — Configure the backend (optional)

Create `agent-api/.env` only if you need non-default settings. The defaults work for local development with the Docker database above.

```env
# Database (default matches docker-compose postgres service)
DATABASE_URL=postgresql://kycuser:kycpassword@localhost:5432/kycagent

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

If you use **Docker Compose for the full stack from the repo root**, set `REPOS_HOST_PATH` in a `.env` file at the repo root (or export it in your shell). When using `agent-api/docker-compose.yml` alone, set it in `agent-api/.env` instead.

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
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Or use the helper script on Windows:

```bash
cd agent-api
run.bat dev
```

**Docker (backend + database together):**

```bash
cd agent-api
docker-compose up --build
```

Verify the API is running:

- Health: http://localhost:8000/api/v1/health
- Swagger docs: http://localhost:8000/docs

### Step 8 — Start the UI

**Full Docker stack (recommended for deployment):**

```bash
# From repo root — builds codegraph, then all three containers
docker build -t codegraph:latest ./codegraph
docker compose up --build
```

Open **http://localhost:8080** in your browser. The UI container (`kyc-agent-ui`) proxies `/api` to the backend container (`kyc-agent-api`) over the shared Docker network.

**Local UI development (hot reload):**

In a **separate terminal** (with backend running from Step 7 or Docker):

```bash
cd ui
npm run dev
```

This starts the Vite dev server on **http://localhost:5173**. Vite proxies `/api` to `http://localhost:8000`.

Optional frontend env file `ui/.env`:

```env
VITE_AGENT_API_URL=http://localhost:8000
VITE_API_URL=http://localhost:8000
```

### Step 9 — Verify the installation

1. Open http://localhost:8080 (Docker) or http://localhost:5173 (Vite dev).
2. Confirm **Settings** loads and the backend health check succeeds.
3. Open http://localhost:8000/docs and confirm the API responds.

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

Build the codegraph engine first (required by the agent-api image), then start all services from the **repo root**:

```bash
docker build -t codegraph:latest ./codegraph

# 1. Start database (if not already running)
docker compose up -d postgres

# 2. Apply migrations (see Step 3)

# 3. Start postgres + backend + UI (separate containers)
docker compose up --build
```

Open the app at **http://localhost:8080**.

### Run stacks independently

Backend only (postgres + API):

```bash
cd agent-api
docker compose up --build
```

UI only (requires backend running first to create the `oncall-agent` network):

```bash
cd ui
docker compose up --build
```

For local UI development with a Docker backend only:

```bash
cd agent-api
docker compose up -d postgres agent-api

cd ../ui
npm install
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
| `npm run dev` | Vite dev server on port 5173 (browser) |
| `npm run dev:vite` | Same as `npm run dev` |
| `npm run build` | Build React frontend for production |
| `npm run preview` | Preview production build locally |
| `npm run lint` | Run ESLint |

### Backend (`agent-api/`)

| Command | Description |
|---------|-------------|
| `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` | Dev server with reload |
| `pytest tests/ -v` | Run unit tests |
| `pytest -m eval -v` | Run eval harness (see `agent-api/evals/README.md`) |
| `run.bat start` | Start via Docker (Windows) |
| `run.bat test` | Run tests via venv (Windows) |

---

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| UI cannot reach API | Backend running on port 8000? Check http://localhost:8000/api/v1/health |
| Database errors on startup | Migrations applied? Postgres container healthy? `DATABASE_URL` correct? |
| MCP server connection fails | Node.js installed? `npx` works? Connection string and SSL certs correct? |
| Bedrock / AWS errors | `AWS_PROFILE` or credentials configured? `PROVIDER_TRANSPORT=bedrock` set? |
| Code analyzer cannot see repos | `REPOS_BASE_PATH` (local) or docker-compose volume mount points at your repos |
| UI shows API errors in Docker | Check `docker compose logs ui agent-api`; confirm http://localhost:8080/api/v1/health |

---

## Documentation

- [Agent API README](agent-api/README.md) — API details, migrations, and Docker deployment
- [Eval harness](agent-api/evals/README.md) — `pytest -m eval` regression gate
- [UI developer guide](ui/AGENTS.md) — frontend architecture and conventions
