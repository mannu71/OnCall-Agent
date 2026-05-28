"""DEPRECATED: legacy shim — re-exports the canonical ExecutionRepository.

The canonical implementation lives in
``app.infrastructure.persistence.execution_repository``. New code should
import from there directly. This module is preserved for backwards
compatibility with callers that still do
``from app.repositories import ExecutionRepository`` and will be removed
in a future release.
"""
from __future__ import annotations

import warnings

from app.infrastructure.persistence.execution_repository import (
    ExecutionRepository as _CanonicalExecutionRepository,
)

warnings.warn(
    "app.repositories.execution_repository is deprecated; "
    "import from app.infrastructure.persistence.execution_repository instead.",
    DeprecationWarning,
    stacklevel=2,
)


class ExecutionRepository(_CanonicalExecutionRepository):
    """Backwards-compatible alias for the canonical ExecutionRepository."""

    pass


__all__ = ["ExecutionRepository"]
