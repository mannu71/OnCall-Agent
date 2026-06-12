"""Lookup-accuracy eval — does lazy retrieval still find the CORRECT thing?

The token-saving tools (repo_map / db_list_tables / db_search_columns) only help
if they preserve accuracy: the agent must still land on the right symbol/table
without seeing the whole codebase/schema. This measures that directly,
deterministically (no agent LLM loop):

  * CODE recall: every known symbol in the fixture repo must appear in the
    names-only repo map when filtered by its name.
  * DB recall: a filtered db_list_tables / db_search_columns must surface the
    expected table, and an UNFILTERED list must be complete (the agent never
    decides on a silently-truncated set).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from evals.accuracy import _bootstrap  # noqa: F401


# ── CODE: repo-map recall over the fixture's known symbols ────────────────────
async def _code_recall() -> List[Dict[str, Any]]:
    from evals.accuracy.run_crawler import _ensure_indexed
    from evals.accuracy.build_crawler_cases import DEFAULT_REPO_NAME, derive_crawler_cases
    from app.services.crawler_flows import crawler_repo_map

    await _ensure_indexed()
    symbols = sorted({c["args"]["symbol"] for c in derive_crawler_cases() if c["op"] == "find"})
    rows: List[Dict[str, Any]] = []
    for sym in symbols:
        res = await crawler_repo_map(DEFAULT_REPO_NAME, name_like=sym, limit=50)
        found = sym in {s.get("name") for s in (res.get("symbols") or [])}
        rows.append({"feature": "code", "metric": "repo_map_recall", "id": f"map-{sym}",
                     "score": 1.0 if found else 0.0,
                     "diagnostic": "found in map" if found else "MISSING from repo_map"})
    return rows


# ── DB: filter/search recall over a synthetic catalog (fake MCP) ──────────────
class _FakeMCP:
    """Serves a synthetic information_schema so the lookup logic can be graded."""
    TABLES = [
        "profiles", "profile_kyc_alerts", "kyc_monitoring_notifications",
        "schedule_updater_runs", "schedule_updater_run_items", "worklist_monitor_metrics",
        "monitor_updater_runs", "aml_alert_emails", "users", "orders",
    ]
    COLUMNS = {  # table -> [columns]
        "schedule_updater_run_items": ["id", "schedule_updater_run_id", "error_code"],
        "profiles": ["id", "status", "profile_import_request_id"],
    }

    async def execute_tool(self, *, server_id, tool_name, arguments):
        sql = arguments["sql"].lower()
        if "information_schema.tables" in sql:
            return {"success": True,
                    "content": [{"table_schema": "public", "table_name": t} for t in self.TABLES]}
        if "information_schema.columns" in sql and "like" in sql:
            term = sql.split("like '%", 1)[1].split("%'", 1)[0]
            hits = [{"table_name": t, "column_name": c}
                    for t, cols in self.COLUMNS.items() for c in cols if term in c.lower()]
            return {"success": True, "content": hits}
        if "information_schema.columns" in sql:
            tbl = sql.split("table_name = '", 1)[1].split("'", 1)[0]
            return {"success": True, "content": [
                {"column_name": c, "data_type": "text", "is_nullable": "YES"}
                for c in self.COLUMNS.get(tbl, [])]}
        return {"success": True, "content": []}


async def _db_recall() -> List[Dict[str, Any]]:
    from app.workflow.tools.db_schema_tools import build_db_schema_tools
    tools = {t.name: t for t in build_db_schema_tools({"prod": "conn-prod"}, _FakeMCP())}
    rows: List[Dict[str, Any]] = []

    # filtered table lookup must surface the expected table
    table_cases = [("profile", "profiles"), ("schedule_updater", "schedule_updater_run_items"),
                   ("monitor", "monitor_updater_runs"), ("aml", "aml_alert_emails")]
    for needle, expected in table_cases:
        out = await tools["db_list_tables"].coroutine(name_like=needle)
        ok = expected in out
        rows.append({"feature": "db", "metric": "list_tables_recall", "id": f"tbl-{needle}",
                     "score": 1.0 if ok else 0.0,
                     "diagnostic": f"{'found' if ok else 'MISSING'} {expected}"})

    # unfiltered list must be COMPLETE (no silent truncation)
    full = await tools["db_list_tables"].coroutine()
    complete = all(t in full for t in _FakeMCP.TABLES)
    rows.append({"feature": "db", "metric": "list_complete", "id": "tbl-complete",
                 "score": 1.0 if complete else 0.0,
                 "diagnostic": "all tables present" if complete else "INCOMPLETE unfiltered list"})

    # column search must find the table that owns a known column
    col_cases = [("run_id", "schedule_updater_run_items"), ("import_request", "profiles")]
    for needle, expected in col_cases:
        out = await tools["db_search_columns"].coroutine(name_like=needle)
        ok = expected in out
        rows.append({"feature": "db", "metric": "search_columns_recall", "id": f"col-{needle}",
                     "score": 1.0 if ok else 0.0,
                     "diagnostic": f"{'found' if ok else 'MISSING'} {expected}"})
    return rows


async def run_lookup_suite() -> List[Dict[str, Any]]:
    return (await _code_recall()) + (await _db_recall())


if __name__ == "__main__":
    rows = asyncio.run(run_lookup_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else "XX "
        print(f"{flag}{r['feature']:<5} {r['id']:<22} {r['metric']:<22} {r['diagnostic']}")
    n = len(rows)
    hit = sum(r["score"] for r in rows)
    print(f"\nlookup accuracy: {hit:.0f}/{n} = {100*hit/n:.1f}%")
