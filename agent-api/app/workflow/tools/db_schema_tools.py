"""Bounded, cached database schema tools for the ReAct agent.

The agent used to discover tables by dumping the *entire* schema (every table
and column) into context — either via an unbounded ``information_schema`` query
or the MCP server's own ``list_tables``/``describe``. That wastes tokens on
every run. These tools replace that with a lazy, filtered lookup that stays
accurate across MULTIPLE connected databases:

  * ``db_list_tables(name_like=...)``   — table *names* only, optionally filtered.
  * ``db_describe_table(table)``        — columns for ONE table.
  * ``db_search_columns(name_like)``    — which table(s) have a column like X.

Accuracy guarantees:
  * The internal catalog is fetched up to a high ``_FETCH_CAP`` so filtering runs
    over the COMPLETE set of names — the agent never decides on a silently
    truncated list. Only the raw *output* is bounded (``_OUTPUT_CAP``), and when
    it is trimmed the tool says so and tells the agent to narrow with name_like.
  * When ``server`` is omitted the tools FAN OUT across every connected database
    and label results by server, so "which DB has table/column X" is one call.
  * All identifiers are validated against a strict whitelist before reaching SQL,
    and every query is bounded with ``LIMIT``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field as PydanticField

from app.config import settings
from app.core.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# Catalogs change rarely; cache per connected server for a TTL.
_CATALOG_TTL = float(getattr(settings, "db_schema_cache_ttl_seconds", 300.0))
# Fetch the COMPLETE catalog (high cap) so filtering is accurate; bound only output.
_FETCH_CAP = int(getattr(settings, "db_schema_max_tables", 2000))
_OUTPUT_CAP = int(getattr(settings, "db_schema_output_max_rows", 200))
_COL_CAP = int(getattr(settings, "db_schema_max_columns", 300))
_catalog_cache = TTLCache(ttl_seconds=_CATALOG_TTL, maxsize=128)

# Strict whitelists — identifiers from the LLM never reach SQL unescaped.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,128}$")     # a table name
_SUBSTR_RE = re.compile(r"^[A-Za-z0-9_%. ]{0,128}$")  # a name_like substring
_TERM_RE = re.compile(r"^[A-Za-z0-9_]{1,128}$")       # a search term (no wildcards)


def build_db_schema_tools(
    db_server_map: Dict[str, str],
    mcp_manager: Any,
) -> List[StructuredTool]:
    """Build the lazy schema-lookup tools for the connected database server(s).

    Args:
        db_server_map: {logical_server_name: connected_server_id}. Used to
            resolve the ``server`` argument and to fan out across all when
            omitted.
        mcp_manager:   MCP client manager exposing ``execute_tool``.
    """
    server_map = dict(db_server_map or {})
    # logical name -> conn id; also accept the conn id itself as `server`.
    _alias = {**server_map, **{v: v for v in server_map.values()}}
    _multi = len(server_map) > 1

    def _targets(server: Optional[str]) -> Tuple[Optional[List[Tuple[str, str]]], Optional[str]]:
        """Return [(label, conn_id), ...] to query, or (None, error)."""
        if server:
            conn = _alias.get(server) or _alias.get(server.strip())
            if not conn:
                return None, (f"Unknown server '{server}'. Connected: "
                              f"{sorted(server_map.keys())}")
            return [(server, conn)], None
        if not server_map:
            return None, "No database server connected."
        # Fan out across all connected servers.
        return [(name, conn) for name, conn in server_map.items()], None

    async def _run_query(conn_id: str, sql: str) -> Tuple[Optional[list], Optional[str]]:
        from app.services.sql_pipeline.runner import _extract_rows
        try:
            res = await mcp_manager.execute_tool(
                server_id=conn_id, tool_name="query", arguments={"sql": sql},
            )
        except Exception as exc:  # noqa: BLE001
            return None, f"query failed: {exc}"
        if not res.get("success"):
            return None, f"query error: {res.get('content') or res.get('error') or 'unknown'}"
        rows, parse_err = _extract_rows(res.get("content"))
        return rows, parse_err

    async def _all_tables(conn_id: str) -> Tuple[Optional[list], bool, Optional[str]]:
        """Return (rows, truncated, error). Cached per server."""
        cache_key = f"tables:{conn_id}"
        cached = _catalog_cache.get(cache_key)
        if cached is not None:
            rows = cached
            return rows, len(rows) >= _FETCH_CAP, None
        sql = (
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
            f"ORDER BY table_schema, table_name LIMIT {_FETCH_CAP}"
        )
        rows, err = await _run_query(conn_id, sql)
        if err:
            return None, False, err
        rows = rows or []
        _catalog_cache.set(cache_key, rows)
        return rows, len(rows) >= _FETCH_CAP, None

    def _fmt_table(schema: Optional[str], tbl: str) -> str:
        return f"{schema}.{tbl}" if schema and schema != "public" else str(tbl)

    # ── Tool: list tables ─────────────────────────────────────────────────────
    class _ListInput(BaseModel):
        name_like: Optional[str] = PydanticField(
            None, description="Case-insensitive substring to filter table names "
                              "(e.g. 'profile'). Omit to list all.")
        server: Optional[str] = PydanticField(
            None, description="Connected server name; omit to search ALL connected databases.")

    async def _list_tables(name_like: Optional[str] = None, server: Optional[str] = None) -> str:
        targets, err = _targets(server)
        if err:
            return err
        if name_like and not _SUBSTR_RE.match(name_like):
            return "Invalid name_like (allowed: letters, digits, _ % . space)."
        needle = (name_like or "").replace("%", "").strip().lower()

        matches: List[str] = []
        truncated_servers: List[str] = []
        for label, conn in targets:
            rows, truncated, q_err = await _all_tables(conn)
            if q_err:
                matches.append(f"[{label}] error: {q_err}")
                continue
            if truncated:
                truncated_servers.append(label)
            for r in rows:
                if not isinstance(r, dict):
                    continue
                tbl = r.get("table_name")
                if not tbl or (needle and needle not in str(tbl).lower()):
                    continue
                name = _fmt_table(r.get("table_schema"), tbl)
                matches.append(f"[{label}] {name}" if _multi else name)

        if not matches:
            scope = "any connected database" if _multi and not server else "the database"
            return (f"No tables match '{name_like}' in {scope}." if name_like
                    else f"No user tables found in {scope}.")

        total = len(matches)
        shown = matches[:_OUTPUT_CAP]
        head = f"{total} table(s)" + (f" matching '{name_like}'" if name_like else "") + ":\n"
        out = head + "\n".join(shown)
        if total > _OUTPUT_CAP:
            out += (f"\n…showing {_OUTPUT_CAP} of {total}. Narrow with name_like "
                    "(filtering runs over the complete catalog).")
        if truncated_servers:
            out += (f"\n⚠ catalog truncated at {_FETCH_CAP} tables on: "
                    f"{truncated_servers} — a very large schema; use a more specific name_like.")
        return out

    # ── Tool: describe one table ──────────────────────────────────────────────
    class _DescribeInput(BaseModel):
        table: str = PydanticField(..., description="Exact table name to describe.")
        server: Optional[str] = PydanticField(
            None, description="Connected server name; omit to look across ALL databases.")

    async def _describe_one(conn: str, bare: str) -> Optional[str]:
        cache_key = f"cols:{conn}:{bare.lower()}"
        cached = _catalog_cache.get(cache_key)
        if cached is not None:
            return cached or None
        sql = (
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            f"WHERE table_name = '{bare}' ORDER BY ordinal_position LIMIT {_COL_CAP}"
        )
        rows, err = await _run_query(conn, sql)
        if err or not rows:
            _catalog_cache.set(cache_key, "")
            return None
        lines = []
        for r in rows[:_COL_CAP]:
            if not isinstance(r, dict):
                continue
            nn = "" if str(r.get("is_nullable", "YES")).upper() == "YES" else " NOT NULL"
            lines.append(f"  {r.get('column_name')}: {r.get('data_type')}{nn}")
        out = "\n".join(lines)
        _catalog_cache.set(cache_key, out)
        return out

    async def _describe_table(table: str, server: Optional[str] = None) -> str:
        targets, err = _targets(server)
        if err:
            return err
        bare = table.split(".")[-1].strip()
        if not _IDENT_RE.match(bare):
            return "Invalid table name (allowed: letters, digits, underscore)."
        blocks: List[str] = []
        for label, conn in targets:
            cols = await _describe_one(conn, bare)
            if cols:
                header = f"Columns of {bare}" + (f" [{label}]" if _multi else "") + ":"
                blocks.append(f"{header}\n{cols}")
        if not blocks:
            where = "any connected database" if _multi and not server else "this database"
            return (f"Table '{bare}' not found in {where}. "
                    "Use db_list_tables(name_like=...) to find the right name.")
        return "\n\n".join(blocks)

    # ── Tool: search columns (which table has a column like X) ────────────────
    class _ColInput(BaseModel):
        name_like: str = PydanticField(
            ..., description="Column-name substring to search for (e.g. 'email', 'run_id').")
        server: Optional[str] = PydanticField(
            None, description="Connected server name; omit to search ALL databases.")

    async def _search_columns(name_like: str, server: Optional[str] = None) -> str:
        targets, err = _targets(server)
        if err:
            return err
        term = (name_like or "").replace("%", "").strip()
        if not _TERM_RE.match(term):
            return "Invalid name_like (allowed: letters, digits, underscore)."
        results: List[str] = []
        for label, conn in targets:
            sql = (
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                f"AND lower(column_name) LIKE '%{term.lower()}%' "
                f"ORDER BY table_name, column_name LIMIT {_OUTPUT_CAP}"
            )
            rows, q_err = await _run_query(conn, sql)
            if q_err:
                results.append(f"[{label}] error: {q_err}")
                continue
            for r in (rows or []):
                if not isinstance(r, dict):
                    continue
                entry = f"{r.get('table_name')}.{r.get('column_name')}"
                results.append(f"[{label}] {entry}" if _multi else entry)
        if not results:
            return f"No columns matching '{name_like}' found."
        head = f"{len(results)} column(s) matching '{name_like}':\n"
        out = head + "\n".join(results[:_OUTPUT_CAP])
        if len(results) > _OUTPUT_CAP:
            out += f"\n…showing {_OUTPUT_CAP} of {len(results)}. Use a more specific term."
        return out

    tools = [
        StructuredTool.from_function(
            coroutine=_list_tables,
            name="db_list_tables",
            description=(
                "List database TABLE NAMES (not columns), optionally filtered by a "
                "case-insensitive substring. Use this FIRST to find the right table instead of "
                "dumping the whole schema. Omit `server` to search all connected databases. If a "
                "filtered list is empty, broaden or drop name_like before concluding a table is "
                "missing. Cheap and cached."),
            args_schema=_ListInput,
        ),
        StructuredTool.from_function(
            coroutine=_describe_table,
            name="db_describe_table",
            description=(
                "Show the columns (name, type, nullability) of ONE table. Call this only for the "
                "specific table you need after db_list_tables — never to enumerate the whole "
                "schema. Omit `server` to find the table across all databases."),
            args_schema=_DescribeInput,
        ),
        StructuredTool.from_function(
            coroutine=_search_columns,
            name="db_search_columns",
            description=(
                "Find which table(s) contain a column whose name matches a substring (e.g. "
                "'email', 'run_id'). Use when you know a column/field but not its table. Omit "
                "`server` to search all databases."),
            args_schema=_ColInput,
        ),
    ]
    logger.info("build_db_schema_tools: created %d tools (servers=%s)",
                len(tools), sorted(server_map.keys()))
    return tools


def clear_schema_cache(server_id: Optional[str] = None) -> None:
    """Invalidate cached catalogs (call after a known schema change)."""
    _catalog_cache.clear(prefix=None if server_id is None else f"tables:{server_id}")
    if server_id is not None:
        _catalog_cache.clear(prefix=f"cols:{server_id}:")
