"""Dependency graph — partitions :class:`Statement` list into execution waves.

A *wave* is a set of statements that can run concurrently because none of them
depend on another. Waves are computed once before execution begins (Kahn's
algorithm) so the pipeline doesn't re-scan dependencies between waves the way
the legacy orchestrator did.

Dependency rule: ``A → B`` (read "B depends on A") iff some element of
``B.references`` matches one of:

* ``A.promote_as``  — the explicit ``-- as: <name>`` directive
* ``A.id``          — the synthetic ``stmt_N`` identifier
* ``A.label``       — the human-readable ``-- label: <name>`` directive

Matching is case-insensitive on all three.

References that match nothing in the statement list (and aren't built-ins) are
**not** considered errors — they're allowed because callers may seed the
scope with workflow-input variables that the graph cannot see. The renderer
warns when such a variable is still unresolved at run time.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set

from .statement import Statement


class CircularReferenceError(ValueError):
    """Raised by :func:`build_waves` when statements form a dependency cycle.

    The ``involved`` attribute lists the statement IDs that could not be
    scheduled, so the UI can highlight them.
    """

    def __init__(self, involved: List[str]):
        super().__init__(
            f"Circular dependency in SQL statements: {', '.join(involved)}"
        )
        self.involved = involved


def build_waves(statements: List[Statement]) -> List[List[Statement]]:
    """Group ``statements`` into topological waves for concurrent execution.

    Returns a list of lists. ``result[0]`` are statements with no in-pipeline
    dependencies; ``result[i+1]`` depends only on statements in
    ``result[0..i]``. Each wave preserves the relative order of statements
    from the input so error messages and the UI keep deterministic ordering.

    Raises :class:`CircularReferenceError` if a cycle is detected.
    """
    if not statements:
        return []

    # By-id map for O(1) lookup; original ordering preserved in `order`.
    by_id: Dict[str, Statement] = {s.id: s for s in statements}
    order: Dict[str, int] = {s.id: i for i, s in enumerate(statements)}

    # producers: lowercase handle → producer statement id. Handles include
    # promote_as / id / label so any one of them satisfies a reference.
    # First producer wins; ambiguous duplicates fall back to source order.
    producers: Dict[str, str] = {}
    for stmt in statements:
        for handle in _name_handles(stmt):
            producers.setdefault(handle.lower(), stmt.id)

    adjacency: Dict[str, List[str]] = defaultdict(list)
    in_degree: Dict[str, int] = {s.id: 0 for s in statements}

    for consumer in statements:
        seen_producers: Set[str] = set()
        for ref in consumer.references:
            producer_id = producers.get(ref.lower())
            if producer_id is None or producer_id == consumer.id:
                # Unknown reference (likely a workflow input) — let the
                # renderer deal with it. Self-references are silently dropped.
                continue
            if producer_id in seen_producers:
                continue
            seen_producers.add(producer_id)
            adjacency[producer_id].append(consumer.id)
            in_degree[consumer.id] += 1

    # Kahn's algorithm — emit one wave per pass.
    waves: List[List[Statement]] = []
    remaining: Set[str] = set(by_id)

    while remaining:
        ready_ids = [sid for sid in remaining if in_degree[sid] == 0]
        if not ready_ids:
            # Everything left is in a cycle.
            raise CircularReferenceError(sorted(remaining))

        # Preserve original source order within the wave for deterministic
        # logs and UI rendering.
        ready_ids.sort(key=lambda sid: order[sid])
        waves.append([by_id[sid] for sid in ready_ids])

        for sid in ready_ids:
            remaining.discard(sid)
            for child in adjacency.get(sid, []):
                in_degree[child] -= 1

    return waves


def _name_handles(stmt: Statement) -> List[str]:
    """Return every name a downstream statement can reference ``stmt`` by."""
    handles = [stmt.id, stmt.label]
    if stmt.promote_as:
        handles.append(stmt.promote_as)
    return handles
