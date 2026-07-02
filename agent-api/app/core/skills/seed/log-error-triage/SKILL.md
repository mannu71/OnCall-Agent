---
name: log-error-triage
description: Triage a spike or new pattern of application errors from log output — find the offending error, correlate it to a code path, and assess blast radius.
config:
  severity_threshold: high
---

# Log Error Triage

Use this when a user reports errors in logs, an error spike, or asks "why is X failing" without
already having a root cause.

## Protocol

1. **Isolate the pattern** — pull the top error patterns for the affected log group(s) and time
   window (`cloudwatch_analyze_patterns` / `cloudwatch_search_logs`). Sort by occurrence count and
   recency; ignore info/debug noise.
2. **Get a verbatim example** — drill into one representative raw log line
   (`cloudwatch_search_logs` with `drill_down=true`) so the exception class, message, and any
   trace/correlation ID are concrete, not paraphrased.
3. **Trace to code** — search the connected repository for the exception message or the log string
   itself (`crawler_search_semantic` / `crawler_grep`) to find the emitting function, then read its
   callers to understand what triggers it.
4. **Assess blast radius** — check whether the pattern correlates with a recent deploy, a specific
   endpoint, or a specific downstream dependency (DB, external API, feature-flag SDK). Note whether
   it's degrading (worsening), steady, or already recovering.
5. **Conclude** — state the root cause with the verbatim evidence, the code citation (file:line), and
   a concrete recommended action (rollback, config fix, code fix, or "monitor — self-recovering").

## Rules

- Never conclude without at least one verbatim log line and one code citation.
- If the code isn't accessible for a service the logs implicate, say so explicitly rather than
  guessing at the fix.
