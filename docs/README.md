# Documentation

Complete reference for the KYC Protect on-call agent platform — an AI investigation
system that diagnoses production issues by combining CloudWatch log/metric analysis,
source-code intelligence, live database access and MCP tool servers behind a single
supervised ReAct agent running on AWS Bedrock.

Every diagram in these docs is Mermaid and renders inline on GitHub/GitLab and in most
IDE markdown previewers.

## Contents

| Doc | What it covers |
|-----|----------------|
| [Architecture & flow](architecture.md) | System topology, backend layers, the full request→answer flow, the ReAct superstep, context management, recovery ladder, supervision, delegation |
| [Tool calling](tool-calling.md) | **How the agent picks the right tool** — the six-stage funnel, progressive disclosure, the BM25 ranker, prompt-level routing, skills, prompt caching, and result-side economics |
| [Feature catalogue](features.md) | Every shipped feature, its entry point, its default, and the knob that controls it |
| [codegraph engine](codegraph.md) | The C code-intelligence engine end to end — indexing pipeline, tree-sitter extraction, the symbol-resolution strategy chain, graph schema, and the query path behind every `codegraph__*` tool |

## Reading order

1. **[architecture.md](architecture.md)** — start here. It gives the altitude split
   (orchestration → agency → capability) and traces one request end to end.
2. **[tool-calling.md](tool-calling.md)** — the accuracy-critical part. Everything the
   platform does so the model calls the *correct* tool, not merely *a* tool.
3. **[features.md](features.md)** — the inventory, for "does it do X, and how do I turn
   it on?"
4. **[codegraph.md](codegraph.md)** — go one level down into the code-intelligence
   engine when you need to know *why* a symbol resolved the way it did.

## Source of truth

These docs describe code, and code moves. The authoritative entry points are:

| Concern | Module |
|---------|--------|
| Workflow DAG execution | [`app/services/visual_workflow_executor.py`](../agent-api/app/services/visual_workflow_executor.py), [`app/workflow/executor/graph.py`](../agent-api/app/workflow/executor/graph.py) |
| Agent run orchestration | [`app/workflow/strategies/react/`](../agent-api/app/workflow/strategies/react) (`preflight` → `executor` → `finalizer`) |
| Agent construction | [`app/harness/react_agent.py`](../agent-api/app/harness/react_agent.py), [`app/harness/agent_builder.py`](../agent-api/app/harness/agent_builder.py) |
| Agent execution + recovery | [`app/harness/agent_runner.py`](../agent-api/app/harness/agent_runner.py) |
| Tool assembly & selection | [`app/harness/tool_assembler.py`](../agent-api/app/harness/tool_assembler.py), [`app/harness/tool_disclosure.py`](../agent-api/app/harness/tool_disclosure.py), [`app/harness/tool_exposure.py`](../agent-api/app/harness/tool_exposure.py), [`app/core/tools/router.py`](../agent-api/app/core/tools/router.py) |
| All tunables | [`app/config/`](../agent-api/app/config) (one mixin per domain) |
| Governance rules | [`agent-api/AGENT_POLICY.md`](../agent-api/AGENT_POLICY.md) |
| Code intelligence | [`codegraph/src/indexer/pipeline.c`](../codegraph/src/indexer/pipeline.c) (indexing), [`codegraph/src/indexer/registry.c`](../codegraph/src/indexer/registry.c) (resolution), [`codegraph/src/server/mcp.c`](../codegraph/src/server/mcp.c) (tools) |
