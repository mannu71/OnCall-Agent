"""Open Knowledge Format (OKF) knowledge bundle.

The durable knowledge tier — known issues, log patterns, services, and skills —
stored as a portable directory of markdown files with YAML frontmatter, exactly
per Google's Open Knowledge Format v0.1 (``type`` is the only required field;
cross-document markdown links form a lightweight knowledge graph). This is the
single write target for durable knowledge (auto-learn, playbooks, the REST/MCP
knowledge surfaces): every write is a reviewable file diff, the bundle is git/tar
portable, and humans can author knowledge by dropping in a markdown file. It
replaced the ``knowledge_entries`` / ``log_patterns`` tables entirely.

Recall runs through :mod:`app.services.semantic_memory`: bundle concepts are
indexed into the ``kb`` memory bank so Postgres FTS surfaces them (tags are
paraphrase synonyms) — OKF is the source-of-truth FORMAT, the FTS store is the
INDEX. :mod:`app.core.knowledge.okf_queries` provides the list/create/search
operations the REST/MCP surfaces call.
"""

from app.core.knowledge.bundle import (
    KnowledgeBundle,
    get_default_bundle,
    index_concept_into_kb,
    lint_frontmatter,
    migrate_legacy_skills,
    reconcile_kb_index,
)
from app.core.knowledge.okf_queries import (
    add_pattern,
    create_known_issue,
    list_known_issues,
    list_patterns,
    search_patterns,
)

__all__ = [
    "KnowledgeBundle",
    "get_default_bundle",
    "index_concept_into_kb",
    "lint_frontmatter",
    "migrate_legacy_skills",
    "reconcile_kb_index",
    "add_pattern",
    "create_known_issue",
    "list_known_issues",
    "list_patterns",
    "search_patterns",
]
