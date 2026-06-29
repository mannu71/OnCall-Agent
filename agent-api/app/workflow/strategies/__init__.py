"""Workflow strategies package.

Node-level strategies (agent, batch agent, router) are invoked from
``workflow/executor/handlers/`` during visual workflow execution.

SQL orchestration is **not** a strategy — see
``workflow/executor/handlers/orchestrator.py`` and ``services/sql_pipeline/``.
"""

from .base import BaseStrategy
from .react import ReactStrategy
from .batch_react import BatchReactStrategy
from .router import RouterStrategy

__all__ = ["BaseStrategy", "ReactStrategy", "BatchReactStrategy", "RouterStrategy"]
