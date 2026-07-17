"""Open Knowledge Format (OKF) knowledge bundle.

The durable knowledge tier — known issues, log patterns, services, and skills —
stored as a portable directory of markdown files with YAML frontmatter, exactly
per Google's Open Knowledge Format v0.1 (``type`` is the only required field;
cross-document markdown links form a lightweight knowledge graph). This replaces
the ``knowledge_entries`` table as the write target for auto-learn: every write
is a reviewable file diff, the bundle is git/tar portable, and humans can author
knowledge by dropping in a markdown file.

Recall still runs through :mod:`app.services.semantic_memory`: bundle concepts
are indexed into the ``kb`` memory bank so the existing hybrid FTS+vector recall
surfaces them — OKF is the source-of-truth FORMAT, the RRF store is the INDEX.
"""

from app.core.knowledge.bundle import (
    KnowledgeBundle,
    get_default_bundle,
    index_concept_into_kb,
    migrate_legacy_skills,
    reconcile_kb_index,
)

__all__ = [
    "KnowledgeBundle",
    "get_default_bundle",
    "index_concept_into_kb",
    "migrate_legacy_skills",
    "reconcile_kb_index",
]
