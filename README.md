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
