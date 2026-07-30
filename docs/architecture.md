# Architecture & flow

How the platform is built, and what happens between "engineer types a question" and
"evidence-backed answer streams back".

> Companion docs: **[tool-calling.md](tool-calling.md)** for tool-selection accuracy,
> **[features.md](features.md)** for the full feature inventory.

---

## 1. System topology

Three containers wired by the single [`docker-compose.yml`](../docker-compose.yml) at the
repo root, plus two things that run *inside* the API container: the native **codegraph**
C engine (driven in-process over stdio) and any number of **MCP server** subprocesses.

```mermaid
flowchart TB
    subgraph client["Operator"]
        UI["<b>kyc-agent-ui</b><br/>React + nginx<br/>Workflow canvas · Chat · Explorer<br/>Skills · Scheduler · Settings"]
    end

    subgraph api["<b>kyc-agent-api</b> — FastAPI"]
        REST["REST /api/v1 + SSE"]
        WF["Workflow engine<br/><i>what runs</i>"]
        HAR["Agent harness<br/><i>how an agent node runs</i>"]
        CAP["Capabilities<br/><i>the actual work</i>"]
        CG["codegraph C engine<br/>(in-process, stdio)"]
        MCPS["MCP subprocesses<br/>ADO · AWS · Postgres · …"]
    end

    subgraph store["<b>kyc-agent-db</b> — pgvector/pg16"]
        PG[("Relational state<br/>Vector embeddings<br/>LangGraph checkpoints + KV store<br/>Job queue + leader lock")]
    end

    subgraph ext["External"]
        BR["AWS Bedrock<br/>(the only LLM provider)"]
        CW["AWS CloudWatch<br/>logs · metrics · alarms"]
        ADO["Azure DevOps<br/>repos · wiki · work items"]
        REPO[/"Host repositories<br/>(bind-mounted RW)"/]
    end

    UI <-->|"REST + SSE"| REST
    REST --> WF --> HAR --> CAP
    CAP --> CG --> REPO
    CAP --> MCPS --> ADO
    CAP --> CW
    HAR --> BR
    WF <--> PG
    HAR <--> PG
    CAP <--> PG
```

| Unit | Container | Role |
|------|-----------|------|
| `ui` | `kyc-agent-ui` | Operator console — workflow canvas, chat, codebase explorer, skills, scheduler, settings |
| `agent-api` | `kyc-agent-api` | API, workflow engine, agent harness, code intelligence, MCP client |
| `postgres` | `kyc-agent-db` | Relational state, `pgvector` embeddings, LangGraph checkpoints + KV store, coordination |

Backend code is **baked into the image** — no hot reload. Python changes require
`docker compose up -d --build agent-api`.

Host repositories are mounted **read-write** so codegraph can index them and the
approval-gated `edit_file` tool can apply fixes. Every edit still passes the permission
gate.

### Postgres does quadruple duty

- **Relational state** — workflows, executions, chat sessions, agent profiles, model/MCP
  role config, tool-approval records, trajectory events.
- **Vector + FTS memory** — `pgvector` for semantic code search; Postgres FTS for the
  OKF knowledge bundle and the pinned/semantic memory banks.
- **Agent runtime** — LangGraph checkpointer (HITL pause/resume, partial-state recovery)
  and the `store` KV table (durable tool results).
- **Coordination** — `SKIP LOCKED` job claims + a pg advisory **leader lock** so the
  scheduler runs single-leader across replicas.

---

## 2. The three altitudes

The repo enforces a strict separation. Knowing which altitude you're at tells you which
module owns a behaviour.

```mermaid
flowchart LR
    A["<b>Orchestration</b><br/>app/workflow<br/>app/services/visual_workflow_executor.py<br/><br/><i>WHAT runs, in what order</i>"]
    B["<b>Agency</b><br/>app/harness<br/>app/workflow/strategies/react<br/><br/><i>HOW one agent node runs</i>"]
    C["<b>Capability</b><br/>app/workflow/tools · app/mcp<br/>app/services · app/core<br/><br/><i>The actual work</i>"]
    A --> B --> C
```

| Layer | Path | Responsibility |
|-------|------|----------------|
| API v1 | `app/api/v1/endpoints` | `workflows`, `executions`, `sessions`, `jobs`, `gateway`, `tools`, `skills`, `codegraph`, `trajectories`, `settings`, `log_watch`, `improvement`, `wiki`, … |
| Workflow engine | `app/workflow`, `app/services/visual_workflow_executor.py` | Node-graph resolution, DAG scheduling, per-node handlers |
| Agent harness | `app/harness` | Spec → prompt → action space → LangGraph agent → bounded run |
| Strategy | `app/workflow/strategies/react` | The 3-phase run: `preflight` → `executor` → `finalizer` |
| Code intelligence | `app/services/codegraph_indexer.py`, `app/core/code_semantic` | Native codegraph indexing + ONNX semantic code search |
| Core runtime | `app/core` | Cross-cutting, grouped by intent: `context/`, `llm/`, `aws/`, `tools/`, `quality/`, `policy/`, `privacy/`, `memory/`, `skills/`, `resilience/`, `concurrency/`, `observability/`, `runtime/`, `vfs/`, `sandbox/`, `supervision/`, `governance/`, `transport/` |

---

## 3. End-to-end flow

One question, start to finish.

```mermaid
sequenceDiagram
    autonumber
    participant U as Operator (UI)
    participant API as FastAPI + SSE
    participant WFE as VisualWorkflowExecutor
    participant PF as react/preflight
    participant EX as react/executor
    participant SUP as supervisor_loop
    participant AG as LangGraph ReAct agent
    participant T as Tools
    participant BR as Bedrock
    participant FN as react/finalizer

    U->>API: POST /workflows/{id}/execute (or chat turn)
    API->>WFE: resolve node graph
    WFE->>WFE: build DAG, BFS by edge order, concurrent frontier
    Note over WFE: non-agent nodes run first<br/>(CloudWatch pre-scan, code analysis, memory recall)
    WFE->>PF: agent node → ReactStrategy
    PF->>PF: config · profile · skills · history · recall · MCP barrier
    PF->>PF: assemble action space + degrade notes
    PF-->>EX: RunPlan (query, tools, llm, spec, checkpointer)
    EX->>SUP: run_supervised(...)
    loop bounded supervisor iterations
        SUP->>AG: run_agent_once → execute_agent
        loop ReAct superstep
            AG->>AG: pre_model_hook (budget · compaction · sanitize)
            AG->>BR: model call (cached system+tools prefix)
            BR-->>AG: tool_use | final text
            AG->>T: policy gate → timeout → invoke
            T-->>AG: observation (capped · compressed · offloaded)
        end
        AG-->>SUP: result + tokens
        SUP->>SUP: drain usage ledger, score answer
        alt PASS / bounds hit
            SUP-->>EX: result
        else RETRY
            SUP->>AG: re-run with corrective guidance
        else HITL / ESCALATE
            SUP-->>API: hitl_pause SSE
        end
    end
    EX-->>FN: result + accumulated tokens
    FN->>FN: synthesis floor · auto-learn · trajectory · structured output · PII rehydrate
    FN-->>API: envelope
    API-->>U: SSE stream + final answer
```

### Triggers

A run starts from one of three places, all converging on the same executor:

- the **UI** (workflow execute, or a chat turn),
- the **scheduler** (`app/core/runtime/scheduler.py`, APScheduler behind a pg advisory
  leader lock so only one replica fires),
- the **DB-backed job queue** (`SKIP LOCKED` claim in `app/services/background_jobs.py`).

### Workflow DAG scheduling

[`app/workflow/executor/graph.py`](../agent-api/app/workflow/executor/graph.py) builds an
adjacency list + in-degree map and walks it breadth-first, running each frontier
concurrently. Nodes that never become runnable raise `WorkflowDeadlockError` naming the
stranded ids rather than silently vanishing. Per-node wall-clock budget is **300 s**, and
**900 s** for `agent` nodes (`WORKFLOW_NODE_TIMEOUT_SECONDS` /
`WORKFLOW_AGENT_NODE_TIMEOUT_SECONDS`).

Node handlers live in `app/workflow/executor/handlers/`:

| Node | Handler | What it contributes |
|------|---------|---------------------|
| `agent` | `agent.py` | The ReAct run itself |
| `language_model` | `language_model.py` | Model wiring (`lm::name` ports) |
| `cloudwatch` | `cloudwatch.py` | Deterministic pre-scan → seeded context block + live tools |
| `code_analyzer` | `code_analyzer.py` | Repo selection → codegraph + repo file tools |
| `mcp_server` | `mcp_server.py` | Connect an MCP server, expose its tools |
| `tool` | `tool.py` | Per-node tool filter / pinning |
| `database` | `database.py` | `db_server_map` → `db_*` schema tools |
| `vector_memory` | `vector_memory.py` | Which memory tiers are active |
| `subagent_window` | `subagent_window.py` | Named specialists reachable via `delegate_to_*` |
| `batch_agent` | `batch_agent.py` | Fan-out over a collection |
| `orchestrator`, `scheduler`, `wiki` | … | Control flow, cron, ADO wiki publish |

The executor accepts **both schema dialects** (legacy `codeAnalyzer`/`data` and the newer
`code_search_tool`/`params`), so old saved workflows keep running.

---

## 4. Phase 1 — Preflight

[`app/workflow/strategies/react/preflight.py`](../agent-api/app/workflow/strategies/react/preflight.py)
turns a node graph plus a user message into a `RunPlan`. It is where almost all of the
platform's "be smart before spending a token" logic lives.

```mermaid
flowchart TD
    Q["User query + workflow"] --> CFG["Extract agent config<br/>merge subagents node<br/>merge DB agent profile"]
    CFG --> SLASH{"starts with<br/>/skill-name?"}
    SLASH -->|yes| EXP["Expand runbook inline<br/>(record invoked skill)"]
    SLASH -->|no| CONV
    EXP --> LLMC
    CONV{"is_conversational?<br/>(greeting / thanks /<br/>'what can you do')"}
    CONV -->|yes| FAST["<b>Conversational fast-path</b><br/>one tiny tool-less call<br/>~28K tokens saved on 'Hi'"]
    FAST --> RET(["EarlyReturn"])
    CONV -->|no| LLMC["Resolve LLM config<br/>+ tools config<br/>(wired nodes ▸ gateway defaults)"]

    LLMC --> HIST["Rebuild tool-inclusive<br/>chat history<br/>(if Memory node has 'session')"]
    HIST --> REFS["Expand @file / @folder<br/>/ @url / @git references"]

    subgraph PAR1["concurrent"]
        RECALL["build_recall_query<br/>pinned ▸ semantic ▸ KB ▸ skill map<br/>under memory_turn_token_budget"]
        BARRIER["MCP pre-connect barrier<br/>(beats the cold-start race)"]
    end

    subgraph PAR2["concurrent"]
        ASM["assemble_base_tools<br/>MCP + CloudWatch + codegraph<br/>+ repo files + db_* → disclosure"]
        CRED["collect_backend_degradations<br/>per-provider credential probes"]
    end

    REFS --> PAR1
    PAR1 --> PAR2
    PAR2 --> GATE{"anything<br/>buildable?"}
    GATE -->|"nothing built, or<br/>model creds dead"| ABORT(["EarlyReturn:<br/>refresh credentials"])
    GATE -->|yes| NOTICE["Fold degrade notes into<br/><b>[System notice]</b> on the user turn"]

    NOTICE --> ANCHOR["Time anchor<br/><i>only if cloudwatch_* bound</i>"]
    ANCHOR --> SEED["Seed pre-computed blocks<br/>CloudWatch · code · memory"]
    SEED --> PII["Bind PII vault<br/>pseudonymize the query"]
    PII --> SKILLT["Bind skill / search_skills<br/><i>only if library non-empty</i>"]
    SKILLT --> EXT["add_extension_tools<br/>delegate · edit · todos · fs_*<br/>subagents · sandbox · verify<br/>read_tool_result · offload wrap"]
    EXT --> MM["Metamemory hydrate + seed<br/>+ resumed-state block"]
    MM --> WRAP["Wrap tools with pseudonymization"]
    WRAP --> SPEC["AgentSpec + policy expansion<br/>+ checkpointer"]
    SPEC --> PLAN(["RunPlan"])
```

Three design rules run through this phase:

**Degrade, never abort.** Every builder in
[`tool_assembler.py`](../agent-api/app/harness/tool_assembler.py) is independently
try/except'd. A dead CloudWatch credential, a codegraph engine that won't start, or a
crashed MCP subprocess removes *that family* and appends a note to `degraded`. The turn is
only short-circuited when **zero** tools built, or the model's own credentials are
confirmed dead. This is why an expired AWS session no longer blocks a pure-database
question.

**Tell the model what's missing.** `degraded` notes become a `[System notice]` prefix on
the user turn — "CloudWatch/log tools are unavailable this turn (AWS credentials
expired…)" — so the model routes around the gap instead of discovering it by calling a
tool that was never bound.

**Everything per-run goes in the user turn, never the system prompt.** The time anchor,
degrade notices, recalled memory, skill map and seeded analysis blocks are all appended to
the query. The system prompt stays byte-stable so the Bedrock cache prefix survives — see
[tool-calling.md § prompt caching](tool-calling.md#7-the-cache-contract).

---

## 5. Phase 2 — Build & run

### 5.1 Building the agent

[`react_agent.build_agent`](../agent-api/app/harness/react_agent.py) composes the prompt,
prepares the governed action space, and compiles a
`langgraph.prebuilt.create_react_agent` graph.

```mermaid
flowchart TD
    SPEC["AgentSpec + llm + tools"] --> CSP["<b>compose_system_prompt</b><br/>agent_builder.py"]

    subgraph CSP2["deterministic, cache-stable sections — in this exact order"]
        R["Role sentence<br/>(from active capabilities)"]
        D["# Doing tasks"]
        P["# Plan → execute → verify<br/><i>multi mode, no todo tools</i>"]
        UT["# Using your tools<br/>(STEP ZERO skill check · domain routing)"]
        CAPS["Capability sections<br/>database ▸ rds_performance ▸<br/>cloudwatch ▸ code_analyzer ▸ extras"]
        GOV["# Governance<br/>(AGENT_POLICY.md sliced to bound tools)"]
        DEEP["# Planning · # Scratch filesystem<br/># Sandboxed shell · # Verify · # Delegation"]
        SK["# Skills <b>(must be last)</b>"]
        INS["Additional instructions"]
    end

    CSP --> CSP2 --> PAS["<b>prepare_action_space</b>"]
    PAS --> PB["+ playbook tools"]
    PB --> POL["Policy engine<br/>allow / ask / deny · output cap<br/>cost ceiling · max tool calls"]
    POL --> TO["Per-tool wall-clock cap"]
    TO --> CACHE{"provider?"}
    CACHE -->|Bedrock| BC["bind_tools + cachePoint ttl=1h"]
    CACHE -->|Anthropic| AC["SystemMessage with cache_control"]
    BC --> CRA
    AC --> CRA["create_react_agent<br/>(+ pre_model_hook, checkpointer)"]
    CRA --> HITL{"hitl_enabled?"}
    HITL -->|yes| SG["StateGraph(MessagesState)<br/>run_agent → hitl_synthesis → END"]
    HITL -->|no| DONE(["compiled agent"])
    SG --> DONE
```

Two invariants worth knowing:

- **Never name a tool the agent doesn't have.** Each prompt section is gated on the tool
  actually being in the final bound list (`has_cloudwatch_tools`, `has_verify_tool`,
  `has_search_tools`, …), not on the operator's intent to enable it. Otherwise a degraded
  run would carry thousands of tokens describing tools that aren't in the schema list.
- **`# Skills` must be the last platform section.** Measured: when it preceded the
  capability sections, "prefer `codegraph__find_symbol`" beat "load the matching skill
  first" 8/8 times. Recency is what binds it.

### 5.2 The ReAct superstep

Every model call passes through `_pre_model_hook`. This is the one place that runs before
*every* LLM invocation, so all per-turn governance lives there.

```mermaid
flowchart TD
    S(["superstep begins"]) --> B{"run budget"}
    B -->|"≥100%"| RAISE["raise RunBudgetExhausted"]
    B -->|"≥90%, first time"| NUDGE["mark nudged"]
    B -->|"under"| C
    NUDGE --> C["<b>CompressionPipeline.maybe_compact</b>"]

    subgraph LADDER["compaction ladder"]
        MM{"metamemory<br/>/context_summary.txt<br/>non-empty & over threshold?"}
        MM -->|yes| MMS["agent-authored summary<br/>supersedes the LLM tier<br/>(zero extra LLM calls)"]
        MM -->|no| MICRO["microcompact"]
        MICRO --> AUTO["threshold autocompact<br/>(LLM summary + preserved tail,<br/>pairing-safe split)"]
    end

    C --> LADDER --> SAN["sanitize_messages_for_model<br/>drop dangling tool calls<br/>fill empty content"]
    SAN --> ADD{"nudge pending?"}
    ADD -->|yes| HN["append synthesis nudge<br/><i>to llm_input_messages only</i>"]
    ADD -->|no| ANT
    HN --> ANT{"Anthropic?"}
    ANT -->|yes| CC["apply cache_control"]
    ANT -->|no| CAL
    CC --> CAL["token-estimate calibration<br/>(opt-in EWMA)"]
    CAL --> M["→ model call"]
```

The nudge and compaction ride `llm_input_messages` — the **model view only** — never the
persisted graph state, so checkpointing and HITL resume are unaffected.

### 5.3 The recovery ladder

[`agent_runner.execute_agent`](../agent-api/app/harness/agent_runner.py) is mostly a
ladder of honest failure modes. Every rung produces a *labelled partial answer* rather
than an error or a half-thought passed off as a conclusion.

```mermaid
flowchart TD
    RUN["stream / invoke with retry"] --> E{"outcome"}

    E -->|"RunBudgetExhausted"| B1["recover partial from checkpointer<br/>label 'stopped at time/token limit'<br/><b>skip all further model calls</b>"]
    E -->|"GraphInterrupt"| B2["emit hitl_pause SSE<br/>return paused sentinel"]
    E -->|"GraphRecursionError"| B3["compact recovery state<br/>+ FORCED_SYNTHESIS_NUDGE<br/>one tool-free synthesis turn"]
    E -->|"context overflow"| B4["LLM-assisted compress<br/>→ retry once"]
    E -->|"ok"| P["_serialize_agent_result"]
    B3 --> P
    B4 --> P

    P --> T{"stop_reason = max_tokens<br/>AND no tool calls?"}
    T -->|yes| T1["up to 3 continuation turns<br/>still truncated → label partial"]
    T -->|no| M{"ends on a mid-thought<br/>preamble under 400 chars?<br/>('Let me search…')"}
    M -->|yes| M1["one more turn to actually act<br/>keep only if it advanced"]
    M -->|no| OFF
    T1 --> OFF
    M1 --> OFF

    OFF["<b>offload oversized tool results</b><br/>store full text, leave a pointer"]
    OFF --> TOK["token accounting<br/>(cache read/write is a BREAKDOWN of input,<br/>never added to it)"]
    TOK --> GRD["ID-grounding guard<br/>flag identifiers no tool produced"]
    GRD --> R(["result envelope"])
```

### 5.4 The supervisor loop

[`supervisor_loop.run_supervised`](../agent-api/app/harness/supervisor_loop.py) is the
**quality** supervisor: it runs *after* a turn and grades the answer. (It is distinct from
the **Action Supervisor**, which gates individual write-class actions *before* they
execute.)

```mermaid
stateDiagram-v2
    [*] --> Bounds
    Bounds --> Run: within iteration cap,<br/>wall clock (900s),<br/>token budget (1.2M)
    Bounds --> Stop: any bound hit → mark exhausted
    Run --> Drain: fold in subagent +<br/>auxiliary LLM usage
    Drain --> Score: estimate_confidence +<br/>InvestigationSupervisor.evaluate
    Score --> Stop: PASS (≥0.60)
    Score --> Retry: score below threshold
    Score --> Hitl: ≤0.50 → engineer review
    Score --> Escalate: unrecoverable
    Retry --> Bounds: re-run full loop with<br/>corrective guidance prepended
    Hitl --> Stop
    Escalate --> Stop
    Stop --> [*]
```

Bounds are **defensive and independent** of the supervisor's own `max_retries`: an
absolute iteration ceiling, a wall-clock deadline, and a cumulative token budget. A RETRY
re-runs the entire ReAct loop — roughly a 2× token multiplier — so it is logged at WARNING
with the score breakdown that triggered it.

---

## 6. Context & token management

Four independent mechanisms keep a long investigation inside the window. They compose;
none of them is the whole answer.

```mermaid
flowchart LR
    subgraph IN["Inbound — what enters context"]
        A1["Per-turn memory budget<br/>memory_turn_token_budget = 800"]
        A2["Skill map: names only<br/>skill_map_char_budget = 1500"]
        A3["Tool map: names only<br/>tool_map_char_budget = 2000"]
        A4["Seeded analysis blocks<br/>cap_context_block"]
    end
    subgraph OBS["Observation-side — what a tool returns"]
        B1["MCP self-cap 8000 chars"]
        B2["Policy output cap 16000"]
        B3["Compression sidecar<br/>(JSON yes, prose/code no)"]
        B4["VFS offload > 6000 chars<br/>→ handle + preview"]
    end
    subgraph LOOP["In-loop — as history grows"]
        C1["metamemory summary<br/>supersedes LLM summary"]
        C2["microcompact"]
        C3["threshold autocompact"]
        C4["reactive compact<br/>on overflow error"]
    end
    subgraph OUT["Across turns — what persists"]
        D1["tool_result_store<br/>full text in pg KV"]
        D2["trajectory keeps a pointer<br/>+ 600-char preview"]
        D3["read_tool_result(handle)<br/>fetches the original bytes"]
        D4["chat session compaction"]
    end
    IN --> LOOP
    OBS --> LOOP
    LOOP --> OUT
```

**Why the store matters.** Tool results used to be cut to 2,000 chars *at write time* into
`executions.trajectory` — which is exactly what a follow-up chat turn replays from. The cut
destroyed the evidence before the read-side budget could see it, so turn 2 of "now check
the logs" started blind and re-derived everything. Storing the full text and replaying a
pointer made replay both **recoverable** and **cheaper** (≈300 chars vs 2,000).

**Token accounting, correctly.** `input_tokens` is the *whole* prompt.
`cache_read_tokens` and `cache_creation_tokens` are a **breakdown of it**, not counters to
add to it. Three call sites once did the addition; the run token budget consequently
stopped runs at roughly half their real budget. See
[`app/core/observability/cache_metrics.py`](../agent-api/app/core/observability/cache_metrics.py)
for the live-Bedrock evidence.

**Subagent tokens are counted.** A delegated child runs under its own execution id and its
own callback, so its usage is invisible to the parent. The
[usage ledger](../agent-api/app/harness/usage_ledger.py) — a ContextVar **mutated in
place**, never re-`set` (a `.set()` is lost across `asyncio.wait_for`) — collects child and
auxiliary (compaction, grader, extractor) usage, and the supervisor loop drains it after
every turn. Without it, a measured run reported 119K tokens against ~1.05M actually spent.

---

## 7. Delegation & multi-agent

A single factory — [`subagent_factory.py`](../agent-api/app/harness/subagent_factory.py) —
builds every `delegate_*` tool, so there is one LLM-resolution path, one tool-scoping path
and one result envelope.

```mermaid
flowchart TD
    P["<b>Parent agent</b><br/>full action space<br/>minus squad-scoped tools"]
    P -->|"delegate_to_&lt;name&gt;"| S1["Named specialist<br/>own role prompt<br/>own capability ids<br/>fnmatch tool subset"]
    P -->|"delegate_parallel"| FAN["Concurrent fan-out<br/>gather + semaphore<br/>+ per-child wait_for"]
    P -->|"delegate_batch"| BAT["items_ref via VFS<br/>(>5 items)"]
    P -->|"delegate_investigation"| GEN["Generic depth-1 child<br/>(when code tools present)"]
    S1 --> R["Fresh context window<br/>own run budget<br/>returns a concise summary only<br/>(delegation_output_max_chars)"]
    FAN --> R
    BAT --> R
    GEN --> R
    R -->|"record_child_usage"| L["usage ledger"]
    R --> P
```

**Strict squad scoping.** A tool matched by a specialist's glob is stripped from the
*parent's* own list — reachable only through that child. `delegate_*` tools and wildcard
(`*`) defs are never stripped.

**Model precedence** (explicit signals only, in order): global
`SUBAGENT_MODEL_OVERRIDE` → per-def `model` (or the `"inherit"` sentinel) → **the parent's
LLM** → the global `subagent` model role. Step 3 is the default, which is how a workflow's
wired Language Model node reaches its children.

Bounds: `DELEGATION_MAX_DEPTH=1`, `DELEGATION_MAX_CONCURRENT=3`,
`DELEGATION_CHILD_TIMEOUT_SECONDS=240`, `DELEGATION_OUTPUT_MAX_CHARS=8000`, plus a CSV
`DELEGATION_BLOCKED_TOOLS` fnmatch list always subtracted from children. There is
deliberately **no** fire-and-forget delegation mode — an earlier async-handle registry
leaked task handles across runs and was removed.

---

## 8. Governance & safety

Five gates, layered, each with a different job.

```mermaid
flowchart TD
    M["Model emits a tool call"] --> G1["<b>Policy engine</b><br/>app/core/policy<br/>declarative set → ResolvedPolicy"]
    G1 --> G2["<b>Permission gate</b><br/>allow / ask / deny<br/>precedence: deny ▸ ask ▸ allow"]
    G2 -->|deny| BLK["structured 'blocked' result<br/>(loop continues)"]
    G2 -->|ask| INT["LangGraph interrupt()<br/>→ approve/deny card in chat"]
    G2 -->|allow| G3
    INT -->|approved| G3["<b>Action Supervisor</b><br/>write-class review<br/>(shadow-first, off by default)"]
    INT -->|denied| BLK
    G3 --> G4["<b>Guardrail controller</b><br/>loop detection · repetition<br/>· max_tool_calls · cost ceiling"]
    G4 --> G5["<b>Per-tool timeout</b><br/>min(configured cap,<br/>remaining run deadline)"]
    G5 --> EXEC["invoke → observation"]
    EXEC --> CAP["output cap → compression<br/>→ PII pseudonymization → offload"]
    CAP --> M
```

- **Mutation classification is dynamic.** Beyond the name patterns
  (`edit_file`, `*_write`, `*_delete`, `run_command`, …), an unknown MCP tool is classified
  by its MCP read-only/destructive annotation, then as a last resort by whole-word mutation
  verbs matched against the leaf name. Read verbs (`get`/`list`/`search`/`describe`/
  `query`/`fetch`/`read`) are never caught. So a newly-wired server is gated without a code
  change and without hardcoding any server name.
- **Modes:** `default` (rules apply), `auto_allow` (skip ask prompts), `plan` (dry-run
  preview — risky tools stay `ask`).
- **PII pseudonymization** binds a per-run vault, swaps entities for stable placeholders in
  everything bound for the model, scrubs tool output through the same vault, and rehydrates
  the final answer on the way out.
- **Governance text** is sliced from [`AGENT_POLICY.md`](../agent-api/AGENT_POLICY.md) down
  to the rules matching this run's bound tools, and injected as a `# Governance` section —
  empty (a no-op) when nothing matches.

---

## 9. Streaming & observability

SSE event types are defined in
[`app/workflow/event_schema.py`](../agent-api/app/workflow/event_schema.py):

| Group | Events |
|-------|--------|
| Workflow lifecycle | `workflow_started`, `workflow_completed`, `workflow_failed` |
| Node lifecycle | `node_started`, `node_completed`, `node_failed` |
| Agent streaming | `llm_token`, `tool_call`, `tool_result`, `agent_error`, `agent_complete` |
| Human-in-the-loop | `hitl_pause`, `hitl_approved`, `hitl_rejected` |
| Cost | `cost_update`, plus live `token_usage_delta` |
| Meta | `stream_end`, `heartbeat` |

`tool_call` events carry an `agent` field so the chat can attribute a call to the parent or
to a specific delegated child, and the stream callback is tagged with the resolved model
name per event.

Per-run telemetry surfaced in the result envelope: `stop_reason`, `truncated`,
`did_forced_synthesis`, `verify_pending`/`verify_last_passed`, `ungrounded_ids`,
`subagent_usage` (per-child, most expensive first), `auxiliary_usage` (by source),
`compression_stats` (including `skipped_uncompressible` — the sidecar answers 200 OK and
hands text straight back for content it can't shrink, so "enabled" tells you nothing about
whether it did anything).

---

## 10. Known structural notes

- **Two disclosure modes coexist by design.** `legacy` (all-or-nothing threshold defer) is
  the default; `window` (per-assembly ranked budget) is opt-in and is a measured no-op
  below the same threshold. See [tool-calling.md](tool-calling.md).
- **Two planning mechanisms, mutually exclusive by construction.** Profile
  `planning: true` binds `write_todos`/`update_todo` and emits `# Planning`; the markdown
  checklist section is the fallback for `agentMode == "multi"` agents *without* todo tools.
  The prompt never emits both.
- **Tool registry is discovery-only.** The startup catalog documents intent; live runs
  still build tools per-execution in `tool_assembler`.
- **`evals/` is not in the production image.** Run accuracy evals on the host (`PYTHONUTF8=1`)
  or copy them into the container.
