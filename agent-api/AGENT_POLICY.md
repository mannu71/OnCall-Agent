# Agent Policy

Dispatch-time rules for the agent harness (`app.core.governance.brief_slicer`).
Each rule below is sliced into the system prompt only for runs that actually
bind a matching tool — so the injected text stays small (~400 tokens) and
relevant, rather than a static paragraph every run pays for regardless of
whether it applies.

## Rule format

```
## R-<n> [tools: <fnmatch pattern,csv>] <rule text>
```

- `[tools: ...]` is a comma-separated list of fnmatch patterns (e.g.
  `edit_file,create_file` or `cw_logs_insights*`). Omit it entirely for a
  rule that applies to every run regardless of bound tools.
- Rule IDs (`R-<n>`) should stay stable once assigned — they may be
  referenced elsewhere (e.g. a governance-converter proposal's `control_ref`).
- Add new rules by appending a `##` line below; nothing else in this file is
  machine-parsed (prose sections like this one are ignored).

## Rules

## R-001 [tools: edit_file,create_file] Never edit or create a file without first reading it (fs_read / codegraph__get_code_snippet / repo_read_file) to confirm the change matches the actual current content — old_string must be copied verbatim, not reconstructed from memory.
## R-002 [tools: edit_file,create_file] After a successful edit, call run_verify (if available) before reporting the change as done — an edit that hasn't been verified is not a confirmed fix.
## R-003 [tools: cw_logs_insights,cloudwatch_search_logs] Always bound a Logs Insights / log-search query with an explicit time window — an unbounded query risks scanning far more data than the task needs.
## R-004 [tools: run_command] Treat sandboxed shell commands as untrusted by default — never pipe their raw output back into another shell command without inspecting it first.
