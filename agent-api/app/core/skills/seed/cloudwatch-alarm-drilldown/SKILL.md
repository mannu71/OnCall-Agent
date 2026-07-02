---
name: cloudwatch-alarm-drilldown
description: Drill into a firing (or recently flapping) CloudWatch alarm to find the metric behavior and the underlying cause, not just the threshold breach.
config:
  lookback_minutes: 60
---

# CloudWatch Alarm Drilldown

Use this when a user asks about a specific alarm ("why is X alarm firing", "is Y still in ALARM"),
or an alarm shows up in a pre-computed CloudWatch scan.

## Protocol

1. **Confirm current state** — `cloudwatch_list_alarms(state_value="ALARM")` for the exact alarm(s) in
   question; note how long it's been in ALARM and whether it's flapping (check alarm history for
   recent OK↔ALARM transitions).
2. **Pull the metric, not just the alarm** — `cloudwatch_get_metric_data` for the underlying metric
   over a window wide enough to show the trend before and after the breach (baseline vs spike).
3. **Correlate to logs** — for the same time window, pull error patterns / log volume for the
   associated log group(s) so the metric spike has log evidence behind it, not just a number.
4. **Rule out noise** — check whether the threshold itself looks miscalibrated (e.g. alarm fires on
   a metric that's naturally spiky) versus a genuine incident.
5. **Conclude** — state whether this is an active incident or a flapping/noisy alarm, the metric
   trend with concrete numbers, and — if it's real — the log evidence that explains it.

## Rules

- Always report the metric's actual before/after values, not just "it breached the threshold."
- If the alarm has flapped more than twice in the lookback window, call that out explicitly — it's
  usually a threshold/config problem, not an incident.
