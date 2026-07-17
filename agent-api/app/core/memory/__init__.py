"""Memory module for agent-api.

Post-turn / background maintenance of ``semantic_memory``:

* **Durable-fact extraction** (fact_extractor.py) — distils durable facts from a
  finished turn.
* **Memory curator** (curator.py) — periodic consolidation/promotion of stored
  memory.

Context compaction moved to :mod:`app.core.context` (compaction.py /
compaction_manager.py). The old ``MemoryManager`` / ``MemoryProvider``
abstraction was removed: the live ReAct path calls
``context_builder.build_recall_query`` and ``semantic_memory`` directly, so the
provider indirection had zero live callers.

Import submodules directly (e.g. ``from app.core.memory.curator import ...``);
this package intentionally re-exports nothing.
"""
