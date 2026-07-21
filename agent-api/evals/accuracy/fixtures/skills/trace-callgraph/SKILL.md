---
name: trace-callgraph
description: List the functions a given function calls (its callees) by following the call graph, and cite them.
when_to_use: When the user asks what functions a specific function calls, or to follow/trace a function's call graph.
---

## Protocol

1. Locate the function with `codegraph__find_symbol` if you need its exact identity.
2. Call `codegraph__trace_path` or `codegraph__query_graph` to follow its outgoing calls (callees).
3. List each callee by name, drawn from the tool result — never guess a callee.

## Rules

- Report only callees the graph tools actually return.
- Do not stop until you have the call graph from a code tool.
