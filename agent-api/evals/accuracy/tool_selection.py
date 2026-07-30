"""Tool-selection eval — does the disclosure layer put the RIGHT tool where the
agent can reach it without a discovery round-trip?

Why this suite exists: the trajectory eval (``run_trajectory``) deliberately
bypasses ``assemble_base_tools`` / the tool router, so no existing suite measures
what happens when a workflow wires large, open-ended MCP catalogs. This one drives
the REAL disclosure code — ``tool_disclosure.apply_tool_disclosure`` (legacy) and
``tool_exposure.ToolExposureManager`` (window) — over a labelled multi-MCP catalog
and reports three numbers per mode.

Fully hermetic and deterministic: it builds a fixed catalog of stub tools (name +
description across several fake "servers"), runs the real BM25 ranker
(``app.core.tools.router.rank_tools``) through the real disclosure paths, and
grades against an ACCEPT-SET per query (several tools can legitimately serve one
intent). No DB, no AWS, no Bedrock — runnable on the host with ``PYTHONUTF8=1``.

Metrics (all measured live here, not read from any prior report):

* ``ranker_hit@k``   — mode-independent: does the shared BM25 ranker place an
  acceptable tool in its top-k? This is the quality of ``search_tools`` and the
  window ranking (same function), and it is the signal that decides whether the
  deferred synonym / embedding work is ever needed.
* ``direct_bind``    — per mode: fraction of queries where an acceptable tool is
  in the DIRECTLY BOUND set, i.e. NO ``search_tools`` round-trip is needed. This
  is the metric that captures the round-trip problem; it is where window mode is
  expected to beat legacy above the floor.
* ``latency_ms``     — per mode: wall time to assemble the bound tool list.

Run:  python -m evals.accuracy.tool_selection
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Set, Tuple

# search_tools' default result limit — the k the agent actually sees per search.
_RANKER_K = 8
# Window budget under test — matches settings.tool_exposure_max default.
_MAX_DIRECT = 12

# Bridge tool names, excluded when checking what is "directly bound" (a tool
# reachable only via the bridge is NOT a direct bind — it costs a round-trip).
_BRIDGE_NAMES = {"search_tools", "call_tool"}


class _StubTool:
    """Minimal stand-in exposing the attributes the disclosure code reads
    (``name``, ``description``, ``args_schema``, ``router_pinned``)."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.args_schema = None
        self.router_pinned = False

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return f"<StubTool {self.name}>"


# ── Core tools (always bound in both modes — never deferred/ranked) ───────────
# Prefixes cloudwatch_ / codegraph_ are core per tool_disclosure._is_core_tool.
_CORE: List[Tuple[str, str]] = [
    ("cloudwatch_search_logs", "Search CloudWatch log groups for matching events."),
    ("codegraph__find_symbol", "Find a code symbol (function/class) by name in the indexed repos."),
]

# ── Deferrable MCP catalog: several fake servers, > 25 tools so it clears the
# legacy floor and window mode actually ranks. Names avoid core keep-prefixes. ─
_MCP: List[Tuple[str, str]] = [
    # Azure DevOps
    ("ado_get_work_item", "Retrieve a single work item (PBI, bug, task) by its numeric id."),
    ("ado_query_work_items", "Run a WIQL query to list work items matching criteria."),
    ("ado_create_work_item", "Create a new work item of a given type."),
    ("ado_list_builds", "List recent build pipeline runs for a project."),
    ("ado_get_build_log", "Fetch the log output of a specific build run."),
    ("ado_list_pull_requests", "List active pull requests in a repository."),
    ("ado_list_test_cases", "List test cases in a test plan or suite."),
    ("ado_get_test_result", "Get the result of a specific test run."),
    # GitHub
    ("gh_get_issue", "Get a GitHub issue by its number."),
    ("gh_list_issues", "List issues in a GitHub repository."),
    ("gh_create_issue", "Open a new GitHub issue."),
    ("gh_get_pull_request", "Get a GitHub pull request by its number."),
    ("gh_merge_pull_request", "Merge an open pull request."),
    ("gh_list_commits", "List recent commits on a branch."),
    ("gh_list_workflows", "List GitHub Actions workflows in a repository."),
    # PagerDuty
    ("pd_list_incidents", "List current PagerDuty incidents."),
    ("pd_get_oncall", "Find who is on call for an escalation policy right now."),
    ("pd_acknowledge_incident", "Acknowledge a triggered incident."),
    # Slack
    ("slack_post_message", "Send a message to a Slack channel."),
    ("slack_list_channels", "List Slack channels in the workspace."),
    ("slack_search_messages", "Search message history across channels."),
    # Weather (synonym-gap probes — descriptions avoid the word "weather")
    ("weather_fetch_forecast", "Return the multi-day forecast for a location."),
    ("weather_current_conditions", "Current temperature, humidity and wind for a place."),
    # Jira
    ("jira_get_issue", "Get a Jira issue by its key."),
    ("jira_search_issues", "Search Jira issues with JQL."),
    ("jira_transition_issue", "Move a Jira issue to a new status."),
    ("jira_add_comment", "Add a comment to a Jira issue."),
    # Kubernetes
    ("k8s_list_pods", "List pods in a namespace."),
    ("k8s_get_pod_logs", "Fetch logs from a pod container."),
    ("k8s_restart_deployment", "Roll a deployment to restart its pods."),
    # Object storage (object vs "file" synonym probe)
    ("s3_list_objects", "List objects in a storage bucket."),
    ("s3_get_object", "Download an object from a storage bucket."),
    # Compute
    ("ec2_list_instances", "List virtual machine instances."),
    ("ec2_stop_instance", "Stop a running instance."),
    # TRUE paraphrase probes: name AND description deliberately share no tokens
    # with their query, so the underscore-split-name enrichment can't rescue them.
    # These are where BM25 is expected to actually struggle.
    ("booking_void_reservation", "End an existing booking and return the money to the customer."),
    ("hr_offboard_employee", "Deactivate accounts and terminate permissions when a person departs the organisation."),
]

# ── Labelled cases: (query, accept_set, kind). accept_set = tools that would be
# a correct pick for the intent (not a single golden answer). ──────────────────
_CASES: List[Tuple[str, Set[str], str]] = [
    ("get work item 877 by id", {"ado_get_work_item"}, "lexical"),
    ("list the active pull requests in the repository", {"ado_list_pull_requests"}, "lexical"),
    ("who is on call right now", {"pd_get_oncall"}, "lexical"),
    ("post a message to a slack channel", {"slack_post_message"}, "lexical"),
    ("search jira issues with jql", {"jira_search_issues"}, "lexical"),
    ("fetch the logs from a pod container", {"k8s_get_pod_logs"}, "lexical"),
    ("open a new github issue", {"gh_create_issue"}, "lexical"),
    ("acknowledge the pagerduty incident", {"pd_acknowledge_incident"}, "lexical"),
    ("restart the deployment", {"k8s_restart_deployment"}, "lexical"),
    ("merge the open pull request", {"gh_merge_pull_request"}, "lexical"),
    ("list recent build pipeline runs", {"ado_list_builds"}, "lexical"),
    ("get the test cases in the test plan", {"ado_list_test_cases"}, "lexical"),
    ("current temperature and humidity", {"weather_current_conditions"}, "lexical"),
    # Synonym-gap cases — the query vocabulary does NOT overlap the tool text.
    # These first two still hit because the underscore-split TOOL NAME carries the
    # concept word ("weather", "object" via "download ... bucket"); kept to show
    # that name enrichment already closes many so-called synonym gaps.
    ("get the weather", {"weather_fetch_forecast", "weather_current_conditions"}, "synonym"),
    ("download a file from the bucket", {"s3_get_object"}, "synonym"),
    # TRUE paraphrase cases — no token overlap with the target's name OR desc.
    ("cancel my hotel trip and get a refund", {"booking_void_reservation"}, "paraphrase"),
    ("someone is leaving the team, revoke their access", {"hr_offboard_employee"}, "paraphrase"),
]


def _catalog() -> List[_StubTool]:
    return [_StubTool(n, d) for n, d in _CORE + _MCP]


def _bound_names(tools: List[Any]) -> Set[str]:
    """Names of directly-bound, non-bridge tools (what the model sees up front)."""
    return {
        getattr(t, "name", "")
        for t in tools
        if getattr(t, "name", "") and getattr(t, "name", "") not in _BRIDGE_NAMES
    }


def _best_accept_rank(rankable: List[_StubTool], query: str, accept: Set[str]) -> int:
    """1-indexed rank of the highest-placed acceptable tool in the BM25 order.

    Mirrors production enrichment (underscore-split name prepended to the desc)
    so the eval measures exactly what search_tools / the window ranker rank on.
    Returns the best (lowest) rank across the accept-set, or a large sentinel if
    somehow none are present. Reporting the RANK — not just a top-k boolean —
    matters because with a small catalog any stopword overlap beats the zero-
    scorers, so a loose top-k saturates at 100% and hides where BM25 actually
    places a paraphrase target.
    """
    from app.core.tools.router import rank_tools

    schemas = []
    for t in rankable:
        words = t.name.replace("__", " ").replace("_", " ")
        schemas.append({"name": t.name, "description": f"{words} {t.description}"})
    ranked = rank_tools(schemas, query)
    order = [s["name"] for s, _score in ranked]
    ranks = [order.index(n) + 1 for n in accept if n in order]
    return min(ranks) if ranks else len(order) + 1


def run_tool_selection_suite() -> List[Dict[str, Any]]:
    """Run every case through the real legacy + window paths. Returns graded rows."""
    from app.harness.tool_disclosure import apply_tool_disclosure
    from app.harness.tool_exposure import ToolExposureManager

    # Legacy path reads TOOL_DISCLOSURE_MODE; force the production default.
    prev_mode = os.environ.get("TOOL_DISCLOSURE_MODE")
    os.environ["TOOL_DISCLOSURE_MODE"] = "auto"

    rows: List[Dict[str, Any]] = []
    mcp_tools = [_StubTool(n, d) for n, d in _MCP]

    # Warm up the one-time import/JIT cost (langchain + pydantic StructuredTool
    # build, the ranker) BEFORE timing, so the first timed iteration doesn't
    # misattribute import warmup to whichever mode runs first.
    apply_tool_disclosure(_catalog())
    ToolExposureManager(_catalog(), max_direct=_MAX_DIRECT).window("warmup")

    try:
        for query, accept, kind in _CASES:
            # Shared BM25 ranker quality — the RANK of the best acceptable tool
            # (mode-independent; drives search_tools + the window ranking alike).
            rank = _best_accept_rank(mcp_tools, query, accept)

            # Legacy: assemble + measure what is directly bound.
            t0 = time.perf_counter()
            legacy_out = apply_tool_disclosure(_catalog())
            legacy_ms = (time.perf_counter() - t0) * 1000.0
            legacy_bound = _bound_names(legacy_out)
            legacy_direct = bool(legacy_bound & accept)

            # Window: fresh manager per query (assembly cost is part of latency).
            t0 = time.perf_counter()
            mgr = ToolExposureManager(_catalog(), max_direct=_MAX_DIRECT)
            window_out = mgr.window(query)
            window_ms = (time.perf_counter() - t0) * 1000.0
            window_bound = _bound_names(window_out)
            window_direct = bool(window_bound & accept)

            # Core must always be directly bound in both modes.
            core_names = {n for n, _ in _CORE}
            core_ok = core_names <= legacy_bound and core_names <= window_bound

            rows.append({
                "id": f"{kind}:{query[:34]}",
                "kind": kind,
                "accept": sorted(accept),
                "ranker_rank": rank,
                "ranker_hit_at_3": rank <= 3,
                "ranker_hit_at_k": rank <= _RANKER_K,
                "legacy_direct": legacy_direct,
                "window_direct": window_direct,
                "core_ok": core_ok,
                "legacy_ms": legacy_ms,
                "window_ms": window_ms,
            })
    finally:
        if prev_mode is None:
            os.environ.pop("TOOL_DISCLOSURE_MODE", None)
        else:
            os.environ["TOOL_DISCLOSURE_MODE"] = prev_mode

    return rows


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows) or 1
    def rate(key: str) -> float:
        return sum(1 for r in rows if r[key]) / n
    return {
        "n": len(rows),
        "ranker_hit_at_3": rate("ranker_hit_at_3"),
        "ranker_hit_at_k": rate("ranker_hit_at_k"),
        "ranker_rank_mean": sum(r["ranker_rank"] for r in rows) / n,
        "ranker_rank_worst": max(r["ranker_rank"] for r in rows),
        "legacy_direct_bind": rate("legacy_direct"),
        "window_direct_bind": rate("window_direct"),
        "core_always_bound": all(r["core_ok"] for r in rows),
        "legacy_ms_avg": sum(r["legacy_ms"] for r in rows) / n,
        "window_ms_avg": sum(r["window_ms"] for r in rows) / n,
    }


def main() -> None:
    rows = run_tool_selection_suite()
    print(f"{'case':<44} {'rank':>4} {'legacy':>6} {'window':>6}")
    print("-" * 64)
    for r in rows:
        print(f"{r['id']:<44} "
              f"{r['ranker_rank']:>4} "
              f"{'Y' if r['legacy_direct'] else '.':>6} "
              f"{'Y' if r['window_direct'] else '.':>6}")

    s = _summarize(rows)
    print("\n── summary ─────────────────────────────────────────────────────")
    print(f"cases:                {s['n']}")
    print(f"ranker rank (best accept): mean {s['ranker_rank_mean']:.2f}, "
          f"worst {s['ranker_rank_worst']}  (1 = perfect; lower is better)")
    print(f"ranker hit@3:         {s['ranker_hit_at_3']*100:5.1f}%   "
          f"hit@{_RANKER_K}: {s['ranker_hit_at_k']*100:5.1f}%  "
          f"(shared BM25 quality — gates the deferred synonym/embedding work)")
    print(f"legacy direct-bind:   {s['legacy_direct_bind']*100:5.1f}%  "
          f"(fraction needing NO search_tools round-trip)")
    print(f"window direct-bind:   {s['window_direct_bind']*100:5.1f}%  "
          f"(the round-trip win — expected >> legacy above the floor)")
    print(f"core always bound:    {s['core_always_bound']}")
    print(f"latency avg:          legacy {s['legacy_ms_avg']:.2f} ms | "
          f"window {s['window_ms_avg']:.2f} ms")

    # By-kind breakdown so a BM25 paraphrase gap is visible, not averaged away.
    for kind in ("lexical", "synonym", "paraphrase"):
        sub = [r for r in rows if r["kind"] == kind]
        if not sub:
            continue
        mean_rank = sum(r["ranker_rank"] for r in sub) / len(sub)
        hit3 = sum(1 for r in sub if r["ranker_hit_at_3"]) / len(sub)
        win = sum(1 for r in sub if r["window_direct"]) / len(sub)
        print(f"  {kind:<10} mean-rank {mean_rank:4.1f} | hit@3 {hit3*100:5.1f}% | "
              f"window direct-bind {win*100:5.1f}%  (n={len(sub)})")


# ── Optional end-to-end confirmation (NOT run by default) ─────────────────────
# The hermetic suite above measures ranking + binding. The real outcome — does
# the agent call the right tool WITHOUT a discovery turn — needs a live Bedrock
# trajectory run and is intentionally gated: it costs money and needs creds.
# Enable with TOOL_SELECTION_E2E=1 once the ranker numbers justify it. Left as a
# thin, explicit hook so the confirmation step is represented, not forgotten.
def run_e2e_confirmation() -> List[Dict[str, Any]]:  # pragma: no cover
    if os.environ.get("TOOL_SELECTION_E2E") != "1":
        print("[e2e] skipped (set TOOL_SELECTION_E2E=1 to run live trajectory checks)")
        return []
    raise NotImplementedError(
        "Live e2e confirmation is a follow-up: drive build_agent_from_spec → "
        "execute_agent over a handful of _CASES with a real (windowed) tool set "
        "and assert the correct tool is called with no search_tools call. Kept "
        "out of the default run to keep this suite hermetic."
    )


if __name__ == "__main__":
    main()
    run_e2e_confirmation()
