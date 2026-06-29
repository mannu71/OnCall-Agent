"""Pipeline orchestrator — ties parser + graph + runner + scope together.

This is the only place where the four other modules are composed. Everything
else in the package is intentionally ignorant of the others' details so each
piece can be tested in isolation.

Algorithm
---------
1. Parse SQL text into statements.
2. Build dependency waves with the graph module.
3. For each wave: render + execute every statement concurrently (bounded by
   a semaphore), then absorb successful results into the scope before moving
   to the next wave.
4. Stop on the first wave that produces any failure (matches legacy semantics
   — the workflow UI relies on this for partial-failure handling).
5. Return a :class:`PipelineReport` with per-statement results and metadata.

Public surface: just :func:`run_pipeline`. Everything else is private.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import settings

from .graph import CircularReferenceError, build_waves
from .parser import parse
from .runner import run_statement
from .scope import Scope
from .statement import PipelineReport, Statement, StatementResult

logger = logging.getLogger(__name__)


# Same env var used elsewhere in the codebase for concurrent task fan-out, so
# operators only need to learn one knob. Falls back to 5 if unset.
_DEFAULT_CONCURRENCY = settings.parallel_flow_concurrency


async def run_pipeline(
    sql_content: str,
    *,
    server_id: str,
    mcp_manager: Any,
    db_server_map: Optional[Dict[str, str]] = None,
    workflow_inputs: Optional[Dict[str, Any]] = None,
    timeout: float = 900.0,
    max_concurrency: Optional[int] = None,
    max_retries: int = 2,
    dry_run: bool = False,
    parameterized: bool = True,
) -> PipelineReport:
    """Execute a multi-statement SQL workflow.

    Parameters
    ----------
    sql_content:
        Raw SQL text containing one or more statements separated by ``;``.
        Supports ``-- label:``, ``-- db:``, and ``-- as:`` directives.
    server_id:
        Default MCP server connection key to use when a statement has no
        ``-- db:`` directive or its directive isn't in ``db_server_map``.
    mcp_manager:
        Any object exposing the ``MCPClientManager.execute_tool`` interface.
        Production passes the real manager; tests pass a fake.
    db_server_map:
        Maps ``-- db: <label>`` strings to MCP connection keys. The database
        node handler builds this map when it connects to multiple servers
        for a single workflow.
    workflow_inputs:
        Optional dict of variables to seed into the scope (e.g. trigger
        parameters from an external scheduler). Available as ``{name}`` in
        any statement.
    timeout:
        Per-statement timeout in seconds.
    max_concurrency:
        Maximum statements to execute simultaneously within a wave. Defaults
        to the ``PARALLEL_FLOW_CONCURRENCY`` env var (or 5).
    max_retries:
        Retries for transient MCP failures, per statement.
    dry_run:
        When True, statements are rendered and returned in the report but
        never sent to MCP.
    parameterized:
        When True, use ``$N`` placeholders and bind values. When False, fall
        back to safe-inline literal formatting.
    """
    db_map = db_server_map or {}
    concurrency = max(1, max_concurrency or _DEFAULT_CONCURRENCY)

    statements = parse(sql_content)
    if not statements:
        return PipelineReport(
            success=False,
            statements_executed=0,
            failures=0,
            results=[],
            error="No statements found in SQL content",
            parameterized=parameterized,
        )

    try:
        waves = build_waves(statements)
    except CircularReferenceError as exc:
        return PipelineReport(
            success=False,
            statements_executed=0,
            failures=0,
            results=[],
            error=str(exc),
            parameterized=parameterized,
        )

    scope = Scope(workflow_inputs=workflow_inputs)
    all_results: List[StatementResult] = []
    wave_sizes: List[int] = []
    semaphore = asyncio.Semaphore(concurrency)

    for wave_index, wave in enumerate(waves):
        wave_sizes.append(len(wave))
        logger.info(
            "Pipeline wave %d/%d: %d statement(s)",
            wave_index + 1, len(waves), len(wave),
        )

        wave_results = await asyncio.gather(
            *(
                _run_with_semaphore(
                    semaphore,
                    statement=stmt,
                    server_id=_resolve_server_id(stmt, db_map, server_id),
                    mcp_manager=mcp_manager,
                    scope=scope,
                    timeout=timeout,
                    max_retries=max_retries,
                    parameterized=parameterized,
                    dry_run=dry_run,
                )
                for stmt in wave
            )
        )

        # Absorb successes into the scope before launching the next wave so
        # dependent statements see their producers' values.
        for stmt, result in zip(wave, wave_results):
            all_results.append(result)
            if result.success:
                scope.absorb(stmt, result)

        # Stop on first wave with any failure — matches legacy behaviour.
        if any(not r.success for r in wave_results):
            break

    failures = sum(1 for r in all_results if not r.success)
    return PipelineReport(
        success=(failures == 0 and len(all_results) == len(statements)),
        statements_executed=len(all_results),
        failures=failures,
        results=all_results,
        error=None if failures == 0 else _first_error(all_results),
        parameterized=parameterized,
        wave_sizes=wave_sizes,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_with_semaphore(
    semaphore: asyncio.Semaphore,
    *,
    statement: Statement,
    **kwargs: Any,
) -> StatementResult:
    async with semaphore:
        return await run_statement(statement, **kwargs)


def _resolve_server_id(
    statement: Statement, db_map: Dict[str, str], default_server_id: str
) -> str:
    """Resolve which MCP connection key to use for a statement.

    Honours the ``-- db:`` directive when the label is present in ``db_map``;
    falls back to the pipeline default with a warning when the directive is
    set but unresolved.
    """
    if statement.database:
        mapped = db_map.get(statement.database)
        if mapped:
            return mapped
        logger.warning(
            "Statement %s requested database %r but no mapping; "
            "falling back to default server %r",
            statement.id, statement.database, default_server_id,
        )
    return default_server_id


def _first_error(results: List[StatementResult]) -> str:
    for r in results:
        if not r.success and r.error:
            return r.error
    return "Pipeline reported failure with no error message"
