"""Memory management module for agent-api.

This module provides two orthogonal capabilities:

1. **Memory providers** (manager.py / builtin.py) — persistent agent
   knowledge across sessions. ``MemoryManager`` orchestrates them.

2. **Context compaction** (compaction.py / compaction_manager.py) — bounded
   context window via pi-style walk-back-to-budget summarization, plus
   cumulative file/symbol tracking that survives across summaries.
   ``ContextCompactionManager`` is the integration point.
"""

from app.core.memory.manager import MemoryManager, MemoryProvider
from app.core.memory.builtin import BuiltinMemoryProvider
from app.core.memory.compaction import StructuredSummary, compact
from app.core.memory.compaction_manager import ContextCompactionManager

__all__ = [
    # Memory providers
    "MemoryManager",
    "MemoryProvider",
    "BuiltinMemoryProvider",
    # Context compaction
    "StructuredSummary",
    "compact",
    "ContextCompactionManager",
]
