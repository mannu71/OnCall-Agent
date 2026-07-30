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
    "Use the db_* schema tools to find the right table — never query "
    "information_schema directly or list the entire schema, it wastes context. "
    "When querying data, prefer targeted queries over full table scans: use WHERE "
    "clauses, date ranges, and LIMIT.\n"
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
    "# RDS / Postgres performance investigation\n"
    "When the investigation involves CPU, load, latency, slow queries, connection "
    "saturation, locks, or any 'database performance' alarm, load the "
    "`rds-performance-investigation` skill BEFORE querying — it carries the full "
    "protocol (top SQL by load → wait events → index health → blocking → instance "
    "metrics) and the conclusion requirements. A vague 'high read load on the "
    "instance' conclusion is not acceptable: name the specific queries, sessions, "
    "and tables, and tie each tuning recommendation to an evidence row."
)


#: How to work a pre-computed scan used to live here — roughly half this section —
#: but it only applies when ``seed_context_blocks`` actually seeded a
#: '[Pre-computed CloudWatch Analysis]' block. That is per-turn state, not a
#: cached-prefix fact, so those instructions now ride with the block itself (see
#: ``context_builder._PRECOMPUTED_CW_GUIDANCE``) and appear only when there is a
#: block to talk about.
_CLOUDWATCH_SECTION = (
    "START by calling the appropriate CloudWatch tool for any question about logs, "
    "errors, alarms, metrics, or latency; only skip the tools for a pure greeting or "
    "small talk that needs no logs. When the user gives a correlation id / request id "
    "/ trace id, LEAD with cloudwatch_correlate_logs for that id and build the "
    "cross-service timeline before anything else. "
    "Respect any data_quality block in a result: if it reports partial/sampled "
    "results or failed groups, say so and confirm with a targeted "
    "cloudwatch_search_logs before concluding; if coverage is full and nothing was "
    "found, state that explicitly rather than implying a problem."
)

#: Only cross-tool judgment lives here now — what the model cannot learn from any
#: single tool schema. The per-tool procedure this section used to carry (the
#: LOCATE→READ→TRACE order, the ``project=`` argument rule, the glob requirement on
#: repo_list_files, the "prefer the indexed tools over search_code" steer, and the
#: implement-feature protocol) moved onto the tools themselves, where it is paid
#: only when that tool is bound and cannot describe a tool the agent does not have.
#: The search_code steer in particular was already shipping verbatim in
#: ``codegraph_tools._SEARCH_CODE_WARNING`` — it was duplicated, not moved.
_CODE_ANALYZER_SECTION = (
    "Use these tools for questions about the SOURCE CODE — how it is structured, what a "
    "symbol does, how services call each other, where a behavior is implemented. They read "
    "code, not live data: for a question about current data, records, counts, or statuses, "
    "query the connected database (or other data source) instead — code shows how the data "
    "is produced, not what is in it. Fetch only what each question needs — never read whole "
    "files or dump the codebase.\n"
    "MANDATORY source confirmation: whenever a finding or claim names a source location — "
    "a file path, file:line (e.g. 'ReportService.cs:427'), class, method, or symbol — "
    "you MUST confirm it in code before asserting it: locate the symbol, then read the "
    "responsible lines. Do NOT draw conclusions from names or log text alone when the code "
    "is reachable. Map a service to its repo by name (e.g. a log group named after a "
    "service → the repo with that service's code). When you conclude, cite repo, file path, "
    "symbol and line number(s) as evidence.\n"
    "To understand how connected services relate (one service calling another's endpoint, "
    "or a queue/topic one publishes and another consumes), investigate it yourself: "
    "search/grep across the connected repos for the evidence (base URLs, route paths, "
    "queue/topic names, client usages), read the matching files, and reason from what "
    "you find — do not assume a fixed set of integration channels."
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
