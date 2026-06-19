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
from app.core.memory.semantic_provider import SemanticMemoryProvider
from app.core.memory.compaction import StructuredSummary, compact
from app.core.memory.compaction_manager import ContextCompactionManager


def build_default_memory_manager() -> "MemoryManager":
    """Wire the standard provider stack: builtin (KB) + semantic (when enabled).

    The semantic provider is registered as the single external provider only
    when ``settings.semantic_memory_enabled`` is set, keeping the manager a
    no-op extension by default.
    """
    from app.config import settings
    from app.services.knowledge_base import KnowledgeBaseService

    manager = MemoryManager()
    manager.add_provider(BuiltinMemoryProvider(KnowledgeBaseService()), is_builtin=True)
    if settings.semantic_memory_enabled:
        manager.add_provider(SemanticMemoryProvider(), is_builtin=False)
    return manager


__all__ = [
    # Memory providers
    "MemoryManager",
    "MemoryProvider",
    "BuiltinMemoryProvider",
    "SemanticMemoryProvider",
    "build_default_memory_manager",
    # Context compaction
    "StructuredSummary",
    "compact",
    "ContextCompactionManager",
]
