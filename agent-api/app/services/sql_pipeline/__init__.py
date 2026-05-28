"""SQL pipeline — small, testable replacement for ``sql_orchestrator.SQLOrchestrator``.

Public entry point: :func:`run_pipeline`. Internal modules are exposed for tests
and for callers that need a finer-grained surface (e.g. running just the parser
to validate a SQL file at save time).

Re-architecture rationale and design notes live in
``C:/Users/csinvmmn/.claude/plans/continue-linear-galaxy.md`` — read that
before making large changes here.
"""
from __future__ import annotations

from .pipeline import run_pipeline
from .statement import PipelineReport, Statement, StatementResult
from .scope import Scope

__all__ = [
    "run_pipeline",
    "PipelineReport",
    "Statement",
    "StatementResult",
    "Scope",
]
