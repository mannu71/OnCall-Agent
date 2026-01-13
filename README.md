# OnCall Agent

A desktop application for automating database monitoring workflows using MCP (Model Context Protocol) servers. Built with Electron, React, and Node.js.

## Project Structure

```
kyc-protect-oncall-agent/
├── agent/                    # Backend agent for workflow execution
│   ├── data/config/          # Configuration files
│   │   ├── mcp-servers.json  # MCP server configurations
│   │   ├── workflows.json    # Workflow definitions
│   │   ├── schedules.json    # Schedule configurations
│   │   └── sql/              # SQL workflow files
│   └── src/                  # Agent source code
├── ui/                       # Electron + React frontend
│   ├── electron/             # Electron main process
│   ├── src/                  # React frontend source
│   └── dist-electron/        # Built Electron app
└── README.md
```

## How to Run Locally

### Prerequisites

- **Node.js** v18 or higher
- **npm** v9 or higher

### Installation

```bash
# Clone the repository
git clone https://dev.azure.com/creditsafe/Compliance/_git/kyc-protect-oncall-agent
cd kyc-protect-oncall-agent

# Install agent dependencies
cd agent
npm install

# Install UI dependencies
cd ../ui
npm install
```

### Run in Development Mode

```bash
cd ui
npm run dev
```

This starts both Vite dev server and Electron with hot-reload.

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
│  node src/agents/run.js "OnCall" "Why did profiles fail today?"             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  1️⃣  run.js                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Loads workflows.json → finds "OnCall" workflow                     │   │
│  │ • Loads llm-config.json → merges API keys                           │   │
│  │ • Calls buildDynamicWorkflow(agentJSON)                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  2️⃣  build-mcp-config.js                                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Reads tool nodes from workflow JSON                                │   │
│  │ • Resolves ${input:ado_org} from mcp-servers.json                   │   │
│  │ • Resolves ${env:AWS_PROFILE} from environment                      │   │
│  │ • Returns MCP server configs                                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  3️⃣  multiserver-mcp-client.js                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Spawns MCP server processes (postgres, ado, cloudwatch)           │   │
│  │ • Connects with 15s timeout                                          │   │
│  │ • Discovers tools from each server                                   │   │
│  │ • Sets up circuit breakers per server                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Output:                                                                    │
│  {"event":"mcp.connect.success","server":"postgres","toolCount":1}         │
│  {"event":"mcp.connect.success","server":"ado","toolCount":15}             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  4️⃣  llm-loader.js                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Reads LLM config from workflow (provider, model, apiKey)          │   │
│  │ • Validates model exists                                             │   │
│  │ • Clamps temperature to 0-1                                          │   │
│  │ • Creates ChatOpenAI/ChatAnthropic/etc instance                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  5️⃣  generic-multi-step-workflow.js - SETUP                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ • Creates LangChain tools from MCP tools                            │   │
│  │ • Binds tools to LLM (llm.bindTools())                              │   │
│  │ • Builds system prompt with available tools                         │   │
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
