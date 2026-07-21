---
name: runbook-r7
description: Assess the blast radius of changing a function — find every place that calls it (its callers) so an edit can be made safely.
when_to_use: Before modifying, refactoring, or deleting a function, when you need to know everything that depends on it and could break.
---

## Protocol

1. Locate the target function with `codegraph__find_symbol`.
2. Use `codegraph__query_graph` or `codegraph__trace_path` to find its callers (incoming callers).
3. Report each caller so the impact of the change is explicit.

## Rules

- Enumerate callers from the graph tools; never assume the set of dependents.
- Frame the answer as the change's blast radius (who is affected).
