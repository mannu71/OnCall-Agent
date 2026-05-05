# OnCall Agent

A desktop application for automating database monitoring workflows using MCP (Model Context Protocol) servers. Built with Electron, React, and Node.js.

## Project Structure

```
kyc-protect-oncall-agent/
├── agent-api/                # FastAPI backend engine & DB orchestrator
│   ├── app/                  # Application source code
│   ├── tests/                # Test suite
│   ├── docker-compose.yml    # Docker services (API + PostgreSQL)
│   └── Dockerfile            # Backend container definition
├── hermes-agent-main/        # Core AI agent engine (Python)
├── ui/                       # Electron + React frontend
│   ├── electron/             # Electron main process
│   ├── src/                  # React frontend source
│   └── dist-electron/        # Built Electron app
└── README.md
```

## How to Run Locally

### Prerequisites

- **Docker** and **Docker Compose**
- **Node.js** v18 or higher
- **npm** v9 or higher
- **Python** 3.12+ (for running agent-api locally without Docker)

### 1. Start the Backend API & Database

The backend services run in Docker containers (FastAPI + PostgreSQL with pgvector).

```bash
cd agent-api
# Build and start the containers in the background
docker-compose up -d --build
```

The API will be available at `http://localhost:8000` (Swagger UI at `/docs`).

### 2. Start the Frontend UI

```bash
cd ui
npm install
npm run dev
```

This starts both the Vite dev server and Electron with hot-reload.

## How to Build EXE

### Build the Application

```bash
cd ui
npm run package
```

This creates:
- **Portable version**: `ui/dist-electron/win-unpacked/OnCall Agent.exe`

### Build Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server with hot-reload |
| `npm run build` | Build React frontend only |
| `npm run package` | Build and package as Electron app (EXE) |

## How to Run the EXE

### Running the Application

1. Navigate to `ui/dist-electron/win-unpacked/`
2. Run `OnCall Agent.exe`

### First-Time Setup

1. **Configure MCP Servers** (Settings page)
   - Click **Add Server**
   - For PostgreSQL:
     - Command: `npx`
     - Args (each on a new line):
       ```
       -y
       @modelcontextprotocol/server-postgres@0.6.2
       postgresql://username:password@hostname:5432/database?sslmode=require
       ```
     - Environment Variables (for AWS RDS SSL):
       ```json
       {
         "NODE_EXTRA_CA_CERTS": "C:\\path\\to\\global-bundle.pem"
       }
       ```
   - Click **Save** - connection will be tested automatically

2. **Create a Workflow** (Workflow page)
   - Drag components to create workflow
   - Connect MCP servers to orchestrator
   - Save the workflow

3. **Schedule Execution** (Scheduler page)
   - Add schedule with workflow, time, and days
   - Enable the schedule

### Required External Tools

- **Node.js & npm**: Required for MCP servers (`npx` command)
- **AWS RDS SSL Certificate**: Download from https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem


### How the ReAct Agent System Works

┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER RUNS AGENT                                    │
│  API Call -> POST /api/v1/workflows/OnCall/execute                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  1️⃣  FastAPI Endpoint (agent-api)                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Loads workflows from DB → finds "OnCall" workflow                  │   │
│  │ • Loads LLM config → gets API keys                                   │   │
│  │ • Initializes Python ReActStrategy & LangGraph                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  2️⃣  MCP Configuration (mcp_config.py)                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Reads tool nodes from workflow definition                          │   │
│  │ • Resolves credentials from secrets manager / environment           │   │
│  │ • Returns MCP server connection parameters                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  3️⃣  MCP Client Manager (mcp_client.py)                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Spawns MCP server processes (postgres, ado, cloudwatch)           │   │
│  │ • Connects via stdio/sse with timeouts                               │   │
│  │ • Discovers tools from each server                                   │   │
│  │ • Sets up circuit breakers and rate limits                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Output:                                                                    │
│  {"event":"mcp.connect.success","server":"postgres","toolCount":1}         │
│  {"event":"mcp.connect.success","server":"ado","toolCount":15}             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  4️⃣  LLM Loader (llm.py)                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Initializes ChatOpenAI/ChatAnthropic/etc using LangChain           │   │
│  │ • Configures context lengths and system prompts                      │   │
│  │ • Sets up token tracking and callbacks                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  5️⃣  LangGraph Orchestrator (workflow.py)                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Creates LangChain tools from MCP tools                            │   │
│  │ • Binds tools to LLM (llm.bind_tools())                             │   │
│  │ • Builds dynamic system prompt                                       │   │
│  │ • Compiles StateGraph (ReAct loop)                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  6️⃣  ReAct LOOP (the magic happens here!)                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ ITERATION 1                                                         │    │
│  │ {"event":"agent.think.start"}                                       │    │
│  │                                                                     │    │
│  │ LLM thinks: "I need to query the database for failed profiles"     │    │
│  │                                                                     │    │
│  │ {"event":"agent.plan.tool_calls","count":1}                        │    │
│  │ {"event":"agent.plan.call","name":"postgres__query"}               │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                         │                                                   │
│                         ▼                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ TOOL EXECUTION                                                      │    │
│  │ {"event":"tool.call.start","tool":"postgres__query","cid":"abc123"}│    │
│  │                                                                     │    │
│  │     mcpClient.call("postgres", "query", {sql: "SELECT..."})        │    │
│  │         ↓                                                           │    │
│  │     Circuit breaker check ✓                                         │    │
│  │     Attempt 1 with 20s timeout                                      │    │
│  │         ↓                                                           │    │
│  │     MCP Server executes query                                       │    │
│  │         ↓                                                           │    │
│  │     Returns: [{id: 123, error: "Timeout"}]                         │    │
│  │                                                                     │    │
│  │ {"event":"tool.call.success","duration":234}                       │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                         │                                                   │
│                         ▼                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ ITERATION 2                                                         │    │
│  │ {"event":"agent.think.start"}                                       │    │
│  │                                                                     │    │
│  │ LLM sees results, thinks: "Found timeout errors, let me check      │    │
│  │ CloudWatch for service health..."                                   │    │
│  │                                                                     │    │
│  │ {"event":"agent.plan.call","name":"cloudwatch__get_active_alarms"} │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                         │                                                   │
│                         ▼                                                   │
│                      ... loop continues ...                                 │
│                         │                                                   │
│                         ▼                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ FINAL ITERATION                                                     │    │
│  │                                                                     │    │
│  │ LLM has enough info, NO tool calls → Route to END                  │    │
│  │                                                                     │    │
│  │ Returns final answer with root cause analysis                       │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  7️⃣  OUTPUT                                                                 │
│  {"event":"workflow.invoke.done","cid":"abc-123"}                          │
│                                                                             │
│  🔥 FINAL ANSWER:                                                           │
│  Root cause: Database connection pool exhausted at 3:15 AM                  │
│  Affected: 3 profiles (IDs: 123, 456, 789)                                 │
│  Recommendation: Increase connection pool size                              │
└─────────────────────────────────────────────────────────────────────────────┘
