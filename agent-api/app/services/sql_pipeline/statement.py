"""Frozen data shapes used by the SQL pipeline.

Keeping these in their own module (no behaviour, just types) lets the parser,
renderer, runner, and graph modules import them without pulling in async or
database dependencies. This is what makes the test surface trivial: parser
output is just data and you compare it with ``==``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional


@dataclass(frozen=True)
class Statement:
    """One parsed SQL statement ready for rendering and execution.

    Attributes
    ----------
    id:
        Deterministic identifier — ``stmt_1``, ``stmt_2``, … assigned by the
        parser in source order. Stable across parses of the same input so
        graph edges and error messages reference the same statement.
    label:
        Human-readable name from the ``-- label:`` directive. Falls back to
        ``id`` when no directive is present. Used in the result payload that
        the workflow UI renders and as a graph node identifier (so SQL authors
        can reference a previous statement by label, not just by anonymous id).
    database:
        Server label from ``-- db:<name>``. ``None`` if the statement should
        run on the pipeline's default ``server_id``. Per-statement directives
        do **not** leak to subsequent statements (a known footgun of the
        legacy orchestrator that this design fixes).
    promote_as:
        Variable name from ``-- as:<name>``. When set, the statement's result
        is bound into the :class:`Scope` under this name so later statements
        can reference it via ``{<name>}`` or ``{<name>.column}``. ``None``
        means the result lands in the final report only — no implicit
        column-name promotion (the legacy behaviour).
    sql:
        The raw SQL body with ``{var}`` and ``{var.column}`` placeholders
        unresolved. The renderer takes this plus the live scope and returns
        a parameterised query string + a bind-values list.
    references:
        The set of top-level variable names this statement reads (extracted
        once at parse time, never recomputed). For ``WHERE id = {WorklistRunId}``
        and ``WHERE created_at = {next_date}`` the set is
        ``{"WorklistRunId", "next_date"}``. The graph module uses this set
        to compute dependency edges.
    """

    id: str
    label: str
    sql: str
    references: FrozenSet[str]
    database: Optional[str] = None
    promote_as: Optional[str] = None


@dataclass(frozen=True)
class StatementResult:
    """Outcome of executing one statement.

    Mirrors the per-query payload the workflow UI already consumes, with two
    additions over the legacy shape: ``rendered_sql`` (so the UI can show the
    actual executed query, useful for debugging template expansion) and
    ``elapsed_ms`` (for surfacing slow queries).
    """

    statement_id: str
    label: str
    success: bool
    rows: Optional[List[dict]]
    error: Optional[str]
    server_id: str
    elapsed_ms: int
    rendered_sql: Optional[str] = None
    database_label: Optional[str] = None  # original -- db: value, for UI grouping


@dataclass(frozen=True)
class PipelineReport:
    """Final result returned by :func:`run_pipeline`.

    ``success`` is true iff every statement succeeded. ``parameterized`` is
    False when the renderer was forced into the safe-inline fallback path
    (the deployed MCP postgres server didn't accept a ``params`` array) — the
    UI can surface a one-time warning in that case.
    """

    success: bool
    statements_executed: int
    failures: int
    results: List[StatementResult]
    error: Optional[str] = None
    parameterized: bool = True
    # wave_sizes[i] is the number of statements run concurrently in wave i.
    # Lets us prove parallelism is actually happening without parsing logs.
    wave_sizes: List[int] = field(default_factory=list)
