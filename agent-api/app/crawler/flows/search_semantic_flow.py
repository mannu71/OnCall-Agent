"""searchSemanticFlow — answer freeform queries against an indexed repo.

DAG::

    QueryKGForHits              (KG fast path — deterministic, disk-valid)
      ─ "skip_to_build" → BuildHitList            (graph hit → no LLM)
      ─ default         → LookupOverview
                            >> LLMSuggestFiles
                            >> ReadFiles
                            >> LLMExtractMatches
                            >> VerifyOnDisk
                            >> BuildHitList        (LLM fallback)

The fast path answers from ``kg_nodes`` (full-text + identifier match) in one
DB query. Only when the graph returns nothing does the flow fall through to the
LLM pipeline, preserving natural-language recall while fixing the large-repo
case where every LLM-proposed hit failed on-disk verification (0/N).
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes.semantic import (
    QueryKGForHits,
    LookupOverview,
    LLMSuggestFiles,
    ReadFiles,
    LLMExtractMatches,
    VerifyOnDisk,
    BuildHitList,
)

_kg = QueryKGForHits()
_lookup = LookupOverview()
_suggest = LLMSuggestFiles(max_retries=3, wait=10.0)
_read = ReadFiles()
_extract = LLMExtractMatches(max_retries=3, wait=10.0)
_verify = VerifyOnDisk()
_build = BuildHitList()

# Default path (KG miss) → existing LLM pipeline
_kg >> _lookup >> _suggest >> _read >> _extract >> _verify >> _build

# Fast path (KG hit) → jump straight to BuildHitList
_kg.next(_build, action="skip_to_build")

search_semantic_flow = AsyncFlow(start=_kg)
