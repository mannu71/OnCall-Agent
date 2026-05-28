"""Statement runner — renders a :class:`Statement` and calls MCP to execute it.

This is the only async module in the pipeline that owns I/O. Everything above
(parser, renderer, graph, scope) is pure and testable in isolation; everything
that needs the wire goes through here.

Two concerns this module handles that the legacy orchestrator did inline:

* **MCP response unwrapping.** Postgres MCP server responses come back as
  ``{success, content: [TextContent(text='<json>')], isError}``. We unwrap to
  a plain Python ``list[dict]`` of rows or capture the error string.

* **Outer retries.** ``MCPClientManager.execute_tool`` already retries twice
  on raw call failures. We add one outer retry layer for cases where the
  inner retries succeed in returning a response that itself reports failure
  — e.g. a transient connection error surfaced in ``error`` rather than as
  an exception. The default of ``max_retries=2`` matches the rest of the
  codebase.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional, Tuple

from .renderer import render
from .scope import Scope
from .statement import Statement, StatementResult

logger = logging.getLogger(__name__)


async def run_statement(
    statement: Statement,
    *,
    server_id: str,
    mcp_manager: Any,
    scope: Scope,
    timeout: float = 900.0,
    max_retries: int = 2,
    parameterized: bool = True,
    dry_run: bool = False,
) -> StatementResult:
    """Render ``statement`` against ``scope`` and execute it on ``server_id``.

    ``dry_run=True`` short-circuits the MCP call: the rendered SQL is returned
    in :class:`StatementResult` with ``success=True`` and ``rows=None``, so
    the UI can preview the exact query without touching the database.
    """
    rendered_sql, bind_values = render(
        statement.sql, scope, parameterized=parameterized
    )

    if dry_run:
        return StatementResult(
            statement_id=statement.id,
            label=statement.label,
            success=True,
            rows=None,
            error=None,
            server_id=server_id,
            elapsed_ms=0,
            rendered_sql=rendered_sql,
            database_label=statement.database,
        )

    # Build the MCP tool arguments. Only include `params` when parameterized
    # mode is on AND there are values to bind — the legacy postgres MCP
    # server complains about an unexpected key with an empty array.
    arguments: dict = {"sql": rendered_sql}
    if parameterized and bind_values:
        arguments["params"] = bind_values

    start = time.monotonic()
    last_error: Optional[str] = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            # Exponential backoff: 0.5s, 1s, 2s, 4s, capped at 8s.
            delay = min(0.5 * (2 ** (attempt - 1)), 8.0)
            await asyncio.sleep(delay)
            logger.info(
                "Retrying statement %s (attempt %d/%d after %.1fs)",
                statement.id, attempt + 1, max_retries + 1, delay,
            )

        try:
            mcp_result = await mcp_manager.execute_tool(
                server_id=server_id,
                tool_name="query",
                arguments=arguments,
                tool_timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — wrap any transport error
            last_error = str(exc)
            logger.warning(
                "Statement %s MCP call raised: %s", statement.id, last_error,
            )
            continue

        if mcp_result.get("success"):
            rows, parse_error = _extract_rows(mcp_result.get("content"))
            if parse_error is not None:
                # Response decoded but rows were unrecognisable — non-transient,
                # don't retry.
                return _failure(
                    statement, server_id, rendered_sql, parse_error,
                    _elapsed_ms_from(start),
                )
            return StatementResult(
                statement_id=statement.id,
                label=statement.label,
                success=True,
                rows=rows,
                error=None,
                server_id=server_id,
                elapsed_ms=_elapsed_ms_from(start),
                rendered_sql=rendered_sql,
                database_label=statement.database,
            )

        # MCP returned success=False — keep the error and (maybe) retry.
        last_error = mcp_result.get("error") or "Unknown MCP error"
        logger.warning(
            "Statement %s MCP returned failure: %s", statement.id, last_error,
        )

    return _failure(
        statement,
        server_id,
        rendered_sql,
        last_error or "Unknown error after retries",
        _elapsed_ms_from(start),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_rows(content: Any) -> Tuple[Optional[list], Optional[str]]:
    """Unwrap MCP tool ``content`` into a list of row dicts.

    Postgres MCP returns ``[TextContent(text='[{...},{...}]')]``. Some tools
    return the rows directly. We accept both.

    Returns ``(rows, error)``. ``error`` is non-None only when ``content`` is
    syntactically present but cannot be interpreted.
    """
    if content is None:
        return [], None
    if isinstance(content, list):
        if not content:
            return [], None
        first = content[0]
        text = getattr(first, "text", None)
        if text is not None:
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                return None, f"MCP content was not valid JSON: {exc}"
            return _coerce_rows(decoded)
        # Already a list of row dicts.
        return _coerce_rows(content)
    return _coerce_rows(content)


def _coerce_rows(value: Any) -> Tuple[Optional[list], Optional[str]]:
    """Normalise ``value`` into ``List[dict]`` or surface a parse error."""
    if value is None:
        return [], None
    if isinstance(value, list):
        return value, None
    if isinstance(value, dict):
        return [value], None
    return None, f"Unexpected row payload type: {type(value).__name__}"


def _failure(
    statement: Statement,
    server_id: str,
    rendered_sql: str,
    error: str,
    elapsed_ms: int,
) -> StatementResult:
    return StatementResult(
        statement_id=statement.id,
        label=statement.label,
        success=False,
        rows=None,
        error=error,
        server_id=server_id,
        elapsed_ms=elapsed_ms,
        rendered_sql=rendered_sql,
        database_label=statement.database,
    )


def _elapsed_ms_from(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
