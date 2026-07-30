"""Context-management package: compaction, tool-output sizing, references.

Consolidates the app's context-window machinery. Import submodules directly;
this package intentionally re-exports nothing (avoids import cycles):

* compaction.py / compaction_manager.py — history compaction
  (``compact``, ``StructuredSummary``, ``ContextCompactionManager``).
* tool_output.py — headroom sidecar (``compress_then_cap``).
* overflow.py — reactive overflow path (lazily imported by agent_runner).
* references.py — @file / @url reference expansion.
"""
