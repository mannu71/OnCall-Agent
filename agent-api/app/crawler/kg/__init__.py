"""Knowledge graph layer for the code crawler.

Static-analysis (tree-sitter) view of every indexed repository, persisted to
the four ``kg_*`` tables created in migration 009.  Sits alongside (not
replacing) the LLM-generated ``repo_abstractions`` overview so query flows
can answer EXACT questions ("who calls CheckEntity()?") from the graph and
fall back to the LLM only for fuzzy/semantic queries.

Submodules:
  parser   — tree-sitter dispatcher + per-language extractors
  resolver — cross-file bare-call-target resolution pass

The async flow nodes that consume this layer live in
``app/crawler/nodes/kg.py`` (FilterChangedFiles, ParseFilesAST,
ResolveBareCallTargets, PersistGraphDelta).
"""
from app.crawler.kg.parser import (
    EdgeRecord,
    NodeRecord,
    parse_file,
    detect_language,
    SUPPORTED_LANGUAGES,
)
from app.crawler.kg.resolver import resolve_bare_call_targets

__all__ = [
    "EdgeRecord",
    "NodeRecord",
    "parse_file",
    "detect_language",
    "resolve_bare_call_targets",
    "SUPPORTED_LANGUAGES",
]
