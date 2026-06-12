# Documentation

Architecture and design docs for the KYC Protect on-call agent platform.

## Contents

- [Architecture overview](architecture.md) — system layout, backend layers, storage,
  external integrations, and the workflow execution flow.
- [Agent harness](agent-harness.md) — how an agent node is built and run: the build
  pipeline, the supervised retry loop, envelopes, and the tool registry.

## Diagrams

Standalone SVGs in [`diagrams/`](diagrams), referenced inline from the docs above:

| File | Shows |
|------|-------|
| [`system-overview.svg`](diagrams/system-overview.svg) | UI, backend layers, storage, external services |
| [`workflow-execution-flow.svg`](diagrams/workflow-execution-flow.svg) | Run trigger → executor → harness → tools |
| [`harness-build-pipeline.svg`](diagrams/harness-build-pipeline.svg) | Node config → agent build stages |
| [`harness-supervisor-loop.svg`](diagrams/harness-supervisor-loop.svg) | Run → score → pass / retry / HITL / escalate |
