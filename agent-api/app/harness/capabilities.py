"""Capability registry — composable agent capabilities.

Each capability contributes a role-sentence fragment and an optional system-prompt
section. Pulling these out of ``agent_builder.build_agent`` lets an agent *profile*
declare an arbitrary set of capabilities instead of the platform hard-coding a fixed
trio. An agent node can declare any combination and the prompt adapts accordingly.

The three builtin sections (database / cloudwatch / code_analyzer) are deliberately
scenario-agnostic: they describe *how to use the tools well* for any task, not how to
run a specific investigation workflow. The ``order`` field keeps them stable (DB →
CloudWatch → code); any custom capability registered later (order default 100) appends.

Note: these section texts were previously byte-identical to the original inline prompt
to preserve the Bedrock cachePoint prefix. They have been updated to remove
scenario-specific framing; the accuracy eval must be re-baselined after this change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Capability:
    """A composable agent capability.

    ``role_fragment`` is joined into the "You are an expert engineering assistant
    with access to {…}" sentence. ``section`` (when set) is appended as its own
    block in the system prompt. ``order`` gives a stable sort for both.
    """

    id: str
    role_fragment: str
    section: Optional[str] = None
    order: int = 100


# ── Builtin capability section text (verbatim from build_agent) ───────────────

_DATABASE_SECTION = (
    "To find the right table, call db_list_tables(name_like='<keyword>'); if you know a "
    "column/field but not its table, call db_search_columns(name_like='<column>'); then "
    "db_describe_table('<table>') for just that table. Do NOT query information_schema "
    "directly or list the entire schema (it wastes context). With multiple databases "
    "connected, omit `server` to search all of them at once (results are labelled "
    "[server]). IMPORTANT: if a filtered lookup returns nothing, broaden or drop the "
    "name_like and retry before concluding the table/column does not exist — the "
    "unfiltered list is complete. When querying data, prefer targeted queries over full "
    "table scans: use WHERE clauses, date ranges, and LIMIT.\n"
    "SQL discipline — apply to EVERY query you write:\n"
    "- Always include LIMIT (≤100 for diagnostic queries, ≤10 for heavy joins).\n"
    "- Prefix expensive diagnostic queries with SET LOCAL statement_timeout = '15s' so a "
    "runaway query cannot block the agent or the database.\n"
    "- Read-only only: SELECT and EXPLAIN only; never EXPLAIN ANALYZE a write statement.\n"
    "- Fully qualify every column in catalog and pg_stat_* queries to avoid 'column "
    "reference is ambiguous' errors — e.g. c.relname, s.relname, a.query — never bare "
    "relname or query when the FROM clause touches more than one table."
)

_RDS_PERFORMANCE_SECTION = (
    "# RDS / Postgres performance investigation protocol\n"
    "Follow this protocol whenever the investigation involves CPU, load, latency, slow "
    "queries, connection saturation, or any 'database performance' alarm:\n\n"
    "1. TOP SQL BY DB LOAD — query pg_stat_statements (if available) ordered by "
    "total_exec_time DESC, then mean_exec_time DESC, then calls DESC; fetch top 10. "
    "Always qualify columns (s.query, s.total_exec_time, s.calls, s.mean_exec_time, "
    "s.rows). If pg_stat_statements is not installed, fall back to pg_stat_activity and "
    "note the limitation.\n"
    "   Example:\n"
    "   SET LOCAL statement_timeout = '15s';\n"
    "   SELECT s.queryid, LEFT(s.query, 120) AS query_snippet,\n"
    "          s.calls, ROUND(s.total_exec_time::numeric, 2) AS total_ms,\n"
    "          ROUND(s.mean_exec_time::numeric, 2) AS mean_ms, s.rows\n"
    "   FROM pg_stat_statements s\n"
    "   ORDER BY s.total_exec_time DESC LIMIT 10;\n\n"
    "2. ACTIVE SESSIONS & WAIT EVENTS — query pg_stat_activity for currently active "
    "sessions; capture a.query, a.wait_event_type, a.wait_event, a.query_start, "
    "a.usename, a.application_name, a.state; group by wait_event_type to see the "
    "dominant bottleneck (Lock / IO / CPU / Client).\n"
    "   Example:\n"
    "   SET LOCAL statement_timeout = '15s';\n"
    "   SELECT a.pid, a.usename, a.application_name, a.state,\n"
    "          a.wait_event_type, a.wait_event,\n"
    "          NOW() - a.query_start AS duration,\n"
    "          LEFT(a.query, 100) AS query_snippet\n"
    "   FROM pg_stat_activity a\n"
    "   WHERE a.state = 'active' AND a.pid <> pg_backend_pid()\n"
    "   ORDER BY duration DESC NULLS LAST LIMIT 20;\n\n"
    "3. HOT TABLES & INDEX HEALTH — check pg_stat_user_tables for high seq_scan + "
    "seq_tup_read with low idx_scan (signals a missing index). Check "
    "pg_stat_user_indexes for indexes with idx_scan = 0 (bloat candidates).\n"
    "   Example:\n"
    "   SET LOCAL statement_timeout = '15s';\n"
    "   SELECT t.relname AS table_name, t.seq_scan, t.seq_tup_read,\n"
    "          t.idx_scan, t.n_live_tup\n"
    "   FROM pg_stat_user_tables t\n"
    "   ORDER BY t.seq_tup_read DESC LIMIT 15;\n\n"
    "4. BLOCKING — identify blockers via pg_locks ⋈ pg_stat_activity; look for "
    "granted=false rows and join to find the blocking PID.\n"
    "   Example:\n"
    "   SET LOCAL statement_timeout = '15s';\n"
    "   SELECT blocked.pid AS blocked_pid,\n"
    "          LEFT(blocked_activity.query, 80) AS blocked_query,\n"
    "          blocking.pid AS blocking_pid,\n"
    "          LEFT(blocking_activity.query, 80) AS blocking_query\n"
    "   FROM pg_locks blocked\n"
    "   JOIN pg_stat_activity blocked_activity ON blocked_activity.pid = blocked.pid\n"
    "   JOIN pg_locks blocking ON blocking.transactionid = blocked.transactionid\n"
    "     AND blocking.pid <> blocked.pid AND blocking.granted\n"
    "   JOIN pg_stat_activity blocking_activity ON blocking_activity.pid = blocking.pid\n"
    "   WHERE NOT blocked.granted LIMIT 10;\n\n"
    "5. CROSS-CHECK INSTANCE METRICS — call cloudwatch_get_metric_data for the RDS "
    "instance: CPUUtilization, ReadIOPS, WriteIOPS, DatabaseConnections, ReadLatency, "
    "WriteLatency, FreeableMemory. Also run SELECT pg_is_in_recovery() to determine "
    "whether you are on the writer or a read replica — this affects which metrics matter.\n\n"
    "6. CONCLUDE WITH NAMED RESOURCES + TUNING — your conclusion MUST:\n"
    "   a. Name the specific offending queries (queryid / first 120 chars of normalized "
    "      text) and their load share.\n"
    "   b. Name sessions / application_name causing wait events or blocking.\n"
    "   c. Name hot tables and whether the problem is a missing index or full scans.\n"
    "   d. Give concrete tuning recommendations tied to each evidence row — e.g. add "
    "      index on table.column (because seq_scan=X, idx_scan=0), rewrite query Y to "
    "      use bind params, raise work_mem for sort-heavy queries, add connection pooling "
    "      (PgBouncer) if DatabaseConnections is near max_connections.\n"
    "   A vague 'high read load on the instance' conclusion is NOT acceptable — name the "
    "   specific resource."
)

_CLOUDWATCH_SECTION = (
    "When a 'Pre-computed CloudWatch Analysis' block is present at the start of this "
    "query, a deterministic log scan has ALREADY run — its results "
    "(alarms, anomalies, error patterns, any drill-down, and a data_quality "
    "coverage block) are in that block. Treat that as your starting evidence. Do NOT "
    "re-run the full scan. Use the live tools only to VERIFY or DRILL DEEPER into "
    "specific findings: cloudwatch_search_logs (drill_down=true) for raw events "
    "behind a pattern/anomaly, cloudwatch_correlate_logs to trace one request across "
    "groups, cloudwatch_discover_log_groups only if a referenced group is missing. "
    "If the user provides a correlation id / request id / trace id (or the pre-computed "
    "block is a 'correlation-lookup'), LEAD with cloudwatch_correlate_logs for that "
    "id and build the cross-service timeline before anything else. "
    "Cite log_group, timestamp, z_score/occurrence_count, and normalized_pattern. "
    "Respect data_quality: if it reports partial/sampled results or failed groups, "
    "say so and confirm with a targeted cloudwatch_search_logs before concluding; "
    "if coverage is full and nothing was found, state that explicitly rather than "
    "implying a problem. "
    "If NO pre-computed block is present, START by calling the appropriate live "
    "CloudWatch tool for any question about logs, errors, alarms, metrics, or "
    "latency (e.g. cloudwatch_analyze_patterns or cloudwatch_search_logs); only skip "
    "the tools for a pure greeting or small talk that needs no logs."
)

_CODE_ANALYZER_SECTION = (
    "Code analysis tools are available over the connected repositories. Fetch only what each "
    "question needs — never read whole files or dump the codebase.\n"
    "PROJECT INTELLIGENCE: a per-repo project brief (what it does, domain model, "
    "architecture) and coding-standards summary may already be in your initial context. "
    "Treat it as ground truth about the project. For more depth call crawler_project_brief(repo), "
    "crawler_module_doc(repo, path) for how a module works, and crawler_find_feature(repo, query) "
    "to map a feature/flow to its code. BEFORE writing or proposing any code, call "
    "crawler_coding_standards(repo) and make your change match the project's naming, layout, "
    "framework idioms and error-handling conventions.\n"
    "GENERAL EXPLORATION: crawler_grep (regex/text over file contents) and crawler_read_file "
    "(read any file by path) work on ANY language, config, or IaC file. ALWAYS prefer "
    "crawler_grep with a pattern, or crawler_repo_map for orientation — they are fast. "
    "ONLY call crawler_list_files when you know the extension or directory you want "
    "(pass a glob= filter, e.g. glob='*.cs' or glob='src/Services/**'). "
    "NEVER call crawler_list_files without a glob filter — it walks the entire repo "
    "(10-15 s on large repos) and the file list is too long to reason over. "
    "To understand how connected services relate (one service calling another's endpoint, "
    "or a queue/topic one publishes and another consumes), investigate it yourself: "
    "grep across the connected repos for the evidence (base URLs, route paths, "
    "queue/topic names, client usages), read the matching files, and reason from what "
    "you find — do not assume a fixed set of integration channels.\n"
    "MANDATORY source confirmation: whenever a finding or claim names a source location — "
    "a file path, file:line (e.g. 'ReportService.cs:427'), class, method, or symbol — "
    "you MUST confirm it in code before asserting it. Call crawler_find_symbol(<symbol>, repo) "
    "or crawler_investigate_alert(<error/stack trace>, repo), then crawler_get_body(handle) to "
    "read the responsible lines. Do NOT draw conclusions from names or log text alone when the "
    "code is reachable. Map a service to its repo by name (e.g. a log group named after a "
    "service → the repo with that service's code).\n"
    "Tool-selection protocol:\n"
    "1. LOCATE — if you know the symbol name, use crawler_find_symbol(symbol, repo) (exact, "
    "fastest). If you only have a concept/description, use crawler_search_semantic(query, repo). "
    "To orient in an unfamiliar repo, crawler_repo_map(repo, name_like=...) gives a cheap "
    "names-only list. For an error/stack-trace/alert, crawler_investigate_alert(alert, repo).\n"
    "2. READ — call crawler_get_body(handle) on a returned body_handle to confirm the code "
    "before citing it. Never conclude from a name alone.\n"
    "3. TRACE — crawler_trace_path(symbol, repo, direction='callers'|'callees', depth) to follow "
    "the call graph once you have a symbol.\n"
    "Only call crawler_index_repo(repo) if a repository appears unindexed. Pass the exact repo "
    "name shown in the tool descriptions.\n"
    "When you conclude, cite repo, file path, symbol and line number(s) as evidence.\n"
    "Implement-feature protocol (when asked to ADD or CHANGE code, not just diagnose): "
    "1. LOCATE the insertion point with crawler_find_symbol / crawler_repo_map. "
    "2. READ the surrounding code with crawler_get_body so your change matches the existing "
    "conventions. 3. APPLY: edit_file for surgical changes to an existing file (old_string "
    "must be unique); create_file ONLY for a genuinely new file (it fails if the file already "
    "exists). Keep the change minimal — no drive-by refactors. 4. AFTER applying, summarize "
    "exactly what changed (file and lines) and tell the user to build/test — you cannot run "
    "the build yourself."
)


_REGISTRY: Dict[str, Capability] = {}


def register(cap: Capability) -> None:
    """Register (or replace) a capability descriptor by id."""
    _REGISTRY[cap.id] = cap


def get(cap_id: str) -> Optional[Capability]:
    return _REGISTRY.get(cap_id)


def resolve(cap_ids: List[str]) -> List[Capability]:
    """Resolve ids to descriptors (unknown ids dropped), sorted by (order, id)."""
    caps = [c for c in (_REGISTRY.get(cid) for cid in cap_ids) if c is not None]
    return sorted(caps, key=lambda c: (c.order, c.id))


def all_ids() -> List[str]:
    return [c.id for c in sorted(_REGISTRY.values(), key=lambda c: (c.order, c.id))]


# ── Register the investigation builtins ───────────────────────────────────────

register(Capability(
    id="database",
    role_fragment="database and MCP tools",
    section=_DATABASE_SECTION,
    order=10,
))
register(Capability(
    id="rds_performance",
    role_fragment="RDS/Postgres performance diagnostics",
    section=_RDS_PERFORMANCE_SECTION,
    order=15,
))
register(Capability(
    id="cloudwatch",
    role_fragment="AWS CloudWatch logs and metrics",
    section=_CLOUDWATCH_SECTION,
    order=20,
))
register(Capability(
    id="code_analyzer",
    role_fragment="source-code analysis",
    section=_CODE_ANALYZER_SECTION,
    order=30,
))
