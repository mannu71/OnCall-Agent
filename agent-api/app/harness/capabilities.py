"""Capability registry — composable agent capabilities.

Each capability contributes a role-sentence fragment and an optional system-prompt
section. Pulling these out of ``agent_builder.build_agent`` lets an agent *profile*
(Phase 1) declare an arbitrary set of capabilities instead of the platform
hard-coding the investigation trio (database / CloudWatch / code analysis).

BEHAVIOUR CONTRACT: the three builtins below carry the *exact* text and ordering
that ``build_agent`` previously had inline, so the composed system prompt — which
is the Bedrock cachePoint prefix — is byte-identical for the investigation path.
The ``order`` field reproduces the original sequence (DB → CloudWatch → code); any
custom capability registered later (order default 100) appends after them.
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
    "table scans: use WHERE clauses, date ranges, and LIMIT."
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
    "If the user gives a correlation id / request id / trace id (or the pre-computed "
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
    "GENERAL EXPLORATION: crawler_grep (regex/text over file contents), crawler_read_file (read "
    "any file by path), and crawler_list_files work on ANY language, config, or IaC file — use "
    "them when the symbol-graph tools don't cover what you need. To understand how connected "
    "services relate (one service calling another's endpoint, or a queue/topic one publishes and "
    "another consumes), investigate it yourself: grep across the connected repos for the evidence "
    "(base URLs, route paths, queue/topic names, client usages), read the matching files, and "
    "reason from what you find — do not assume a fixed set of integration channels.\n"
    "MANDATORY drill-in: whenever a finding (a CloudWatch error pattern, stack trace, log "
    "line, or alert) names a source location — a file path, file:line (e.g. "
    "'ReportService.cs:427'), class, method, or symbol — you MUST confirm it in code before "
    "stating a root cause. Call crawler_investigate_alert(<the error/stack trace>, repo) or "
    "crawler_find_symbol(<symbol>, repo), then crawler_get_body(handle) to read the "
    "responsible lines. Do NOT conclude root cause from the log text alone when the code is "
    "reachable. Map the failing service to its repo by name (e.g. a "
    "'compliance_kyc-protect-api' log group → the 'compliance-api' repo).\n"
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


# ── Register the three investigation builtins (exact original ordering) ───────

register(Capability(
    id="database",
    role_fragment="database and MCP tools",
    section=_DATABASE_SECTION,
    order=10,
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
