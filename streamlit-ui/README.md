# OnCall Agent — Streamlit UI

The web UI for the OnCall Agent platform, written in Python with
[Streamlit](https://streamlit.io). It replaces the React app in `../ui` (kept in
the repo until this port is signed off) and talks to the FastAPI backend
(`agent-api`) over the same REST + SSE API, so workflows saved by either UI
open in the other.

## Pages

| Page | What it does |
|------|--------------|
| **Dashboard** | Stat tiles (active/total workflows, 24h failures, running now, with trends), live activity feed, next scheduled run, 7-day token usage, paginated recent runs with a details dialog (CloudWatch / SQL / agent results, token + prompt-cache breakdown). Auto-refreshes every 30s. |
| **Workflows** | Workflow cards with search and filters, run/edit/delete, create from a template, and the **visual workflow editor** (see below). |
| **Scheduler** | Cron schedules on non-agent workflows: add, edit, delete, run now. Times are shown in your browser's timezone and stored as UTC cron. |
| **Chat** | Talk to agent workflows. Live streaming of tokens and tool calls, approve/deny gated tools (e.g. `edit_file` with a diff), stop a run, persisted conversations (resume, pin, delete), and runs survive leaving the page or a browser refresh (`?session=` in the URL). |
| **Codebase Explorer** | Repos indexed by the codegraph engine: branch switch + reindex, git fetch, reindex, delete index, and a 3D/2D graph of the code with node-type/edge-type filters and a node inspector. |
| **Skills** | Create, view, edit, upload and delete markdown `SKILL.md` skills. |
| **Settings** | General (health, global timezone), MCP servers (add/edit/test), models (Bedrock credentials, LLMs, discovery, bulk delete), certificates, and feature flags. |

### Workflow editor

The canvas is [streamlit-flow](https://github.com/dkapur17/streamlit-flow)
(React Flow), so nodes are still dragged around and wired by dragging from one
node's handle to another's.

- **Add nodes** from the *Components* palette on the left.
- **Connect** by dragging between nodes. Each card has one input and one output
  handle, so the editor picks the port pair from the port types (e.g. a
  CloudWatch node → Agent becomes `tool → tools`). Use **Connect to…** in the
  properties panel, or select an edge, to choose a specific port, such as one
  model of a multi-model Language Model node.
- **Subagents**: drop a tool node inside a *Subagent Window* (or pick its
  *Scope* in the properties panel). On save the editor derives the agent's
  `subagents` definition from the windows, as the React editor did.
- **Delete** a node or edge with right-click, or from the properties panel.
- Lint warnings (e.g. auto-learn without a Memory node) appear above the canvas.

The saved format is unchanged: nodes `{id, type, x, y, name, params, parentId?}`
and edges `{source, sourceSlot, target, targetSlot}`.

## Running it

**In Docker** (the default): `docker compose up --build -d` from the repo root
serves it at <http://localhost:43000>. The container reaches the backend at
`http://agent-api:8000`.

**Locally**, against a backend on `localhost:48000`:

```bash
cd streamlit-ui
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py --server.port 45173 --server.runOnSave true
```

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_API_URL` | `http://localhost:48000` | Backend base URL (with or without `/api/v1`). |
| `AGENT_API_KEY` | _(empty)_ | Sent as `X-API-Key` when agent-api runs with `API_AUTH_ENABLED=true`. |
| `AGENT_API_TIMEOUT` | `30` | Default request timeout in seconds. Agent runs, reindex and git checkout have no timeout. |

## Layout

```
streamlit-ui/
├── app.py                 # entry point: navigation + API health badge
├── views/                 # one module per page
├── oncall_ui/
│   ├── api.py             # every agent-api call (requests)
│   ├── runs.py            # background agent runs: execute + SSE progress + HITL
│   ├── sse.py             # SSE parser
│   ├── workflow_model.py  # node catalog, port rules, lint, subagent export
│   ├── canvas.py          # model <-> streamlit-flow bridge
│   ├── schedules.py       # schedule view-model over workflows
│   ├── results.py         # final answer / token extraction
│   ├── timeutils.py       # dates, cron <-> local time
│   ├── ui.py              # cached loaders, flash messages, shared widgets
│   └── data/              # node catalog + workflow templates (exported from ../ui)
└── tests/                 # pytest
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Differences from the React UI

- The **Incidents**, **Alerts** and **Emergency Contact** placeholder pages
  ("under development" in React) are not carried over.
- The Codebase Explorer graph uses Plotly (3D or 2D) instead of the custom
  three.js "galaxy" renderer. In 2D, clicking a node selects it for inspection.
- The workspace name in Settings → General lasts for the browser session only
  (React kept it in `localStorage`).
- Chat has no "edit message" action, because Streamlit's chat input can't be
  pre-filled. Copy and Regenerate are available.
