---
name: service-restart-checklist
description: Decide whether restarting/redeploying a service is the right mitigation, and confirm it actually resolved the issue afterward.
config:
  post_restart_wait_minutes: 5
---

# Service Restart Checklist

Use this when a proposed fix is "restart/redeploy the service" — e.g. a client/connection object was
destroyed and won't self-heal, or a process looks wedged.

## Protocol

1. **Confirm restart is the right call** — check that the symptom matches a "wedged process" pattern
   (e.g. repeated identical errors like "client already destroyed", no recovery over a long window,
   or a memory/connection leak trend) rather than an external dependency outage a restart won't fix.
2. **Identify the exact target** — the specific task/container/instance ID from the logs, not just
   the service name, so the recommendation is actionable.
3. **State the expected effect** — what a restart actually resolves (reinitializes in-memory client
   state, clears a leaked connection pool, picks up a config change) versus what it won't (a bad
   deploy, a downstream outage, a data problem).
4. **Recommend + verify plan** — recommend the restart/redeploy, and specify what to check
   afterward: error rate for the same pattern should drop to zero within a few minutes, and the
   previously-failing operation should succeed.
5. **If already restarted** — check whether the error pattern actually stopped after the restart
   timestamp; if it's still occurring, the restart didn't fix the root cause and a code/config fix
   is needed instead.

## Rules

- Never recommend a restart as the *only* fix if the root cause is a code bug (e.g. a client that
  doesn't reinitialize after being destroyed) — recommend both the immediate restart AND the
  underlying code fix.
- Always give a concrete way to verify the restart worked, not just "restart and monitor."
