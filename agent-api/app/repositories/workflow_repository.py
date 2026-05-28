"""DEPRECATED: legacy shim — re-exports the canonical WorkflowRepository.

The canonical implementation lives in
``app.infrastructure.persistence.workflow_repository``. New code should
import from there directly. This module exists for backwards compatibility
with callers that still do ``from app.repositories import WorkflowRepository``
and will be removed in a future release.
"""
from __future__ import annotations

import warnings

from app.infrastructure.persistence.workflow_repository import (
    WorkflowRepository as _CanonicalWorkflowRepository,
)

warnings.warn(
    "app.repositories.workflow_repository is deprecated; "
    "import from app.infrastructure.persistence.workflow_repository instead.",
    DeprecationWarning,
    stacklevel=2,
)


class WorkflowRepository(_CanonicalWorkflowRepository):
    """Backwards-compatible alias for the canonical WorkflowRepository."""

    pass


__all__ = ["WorkflowRepository"]
