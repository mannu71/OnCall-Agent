"""Context-management package: compaction, tool-output sizing, references.

Consolidates the app's context-window machinery. Import submodules directly;
this package intentionally re-exports nothing (avoids import cycles):

* compaction.py / compaction_manager.py — history compaction
  (``compact``, ``StructuredSummary``, ``ContextCompactionManager``).
* tool_output.py — headroom sidecar (``compress_then_cap``).
* overflow.py — LEGACY reactive overflow path.
* references.py — @file / @url reference expansion.
* token_budget.py, sliding_window.py — UNUSED (no live callers); kept pending
  removal in a later pass.
"""
