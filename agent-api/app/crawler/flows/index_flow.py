"""indexFlow — crawl and index a repository.

DAG::

    FetchRepo
      >> FilterChangedFiles       (per-file SHA diff vs kg_files)
      >> ParseFilesAST            (tree-sitter — pure static analysis)
      >> ResolveBareCallTargets   (cross-file resolution)
      >> PersistGraphDelta        (writes kg_nodes/kg_edges/kg_files)
      >> ExtractAbstractions      (LLM — high-level overview, best-effort)
      >> AnalyzeRelationships     (LLM — relationship summary, best-effort)
      >> BuildFileMap             (deterministic)
      >> PersistOverview          (writes repo_abstractions row)

KG-first ordering: the knowledge graph (deterministic, no LLM) is built
and persisted *before* the LLM nodes run.  If a downstream LLM call is
refused by a content filter the kg_* tables are already populated and the
KG-backed query flows (findSymbolFlow, tracePathFlow) keep working.
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes import (
    FetchRepo,
    ExtractAbstractions,
    AnalyzeRelationships,
    BuildFileMap,
    PersistOverview,
    FilterChangedFiles,
    ParseFilesAST,
    ResolveBareCallTargets,
    PersistGraphDelta,
)

_fetch = FetchRepo()
_kg_filter = FilterChangedFiles()
_kg_parse = ParseFilesAST()
_kg_resolve = ResolveBareCallTargets()
_kg_persist = PersistGraphDelta()
_extract = ExtractAbstractions(max_retries=3, wait=10.0)
_analyze = AnalyzeRelationships(max_retries=3, wait=10.0)
_build = BuildFileMap()
_persist = PersistOverview()

(_fetch >> _kg_filter >> _kg_parse >> _kg_resolve >> _kg_persist
        >> _extract >> _analyze >> _build >> _persist)

index_flow = AsyncFlow(start=_fetch)
