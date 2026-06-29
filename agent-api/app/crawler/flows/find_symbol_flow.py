"""findSymbolFlow — locate a symbol definition in an indexed repo.

DAG::

    QueryKGForSymbol         (KG fast path)
      ─ "skip_to_build" → BuildFindResponse        (graph hit → no LLM)
      ─ default         → LookupOverview
                            >> NarrowToAbstractions
                            >> ScanFilesInScope
                            >> LLMExtractMatches
                            >> VerifyOnDisk
                            >> BuildFindResponse   (LLM fallback)

The fast path returns in one DB query when the symbol exists in ``kg_nodes``.
On miss (unknown symbol / unindexed repo) the flow falls through to the
existing LLM-based pipeline.
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes import (
    QueryKGForSymbol,
    LookupOverview,
    NarrowToAbstractions,
    ScanFilesInScope,
    LLMExtractMatches,
    VerifyOnDisk,
    BuildFindResponse,
)

_kg = QueryKGForSymbol()
_lookup = LookupOverview()
_narrow = NarrowToAbstractions()
_scan = ScanFilesInScope()
_extract = LLMExtractMatches(max_retries=3, wait=10.0)
_verify = VerifyOnDisk()
_build = BuildFindResponse()

# Default path (KG miss) → existing LLM pipeline
_kg >> _lookup >> _narrow >> _scan >> _extract >> _verify >> _build

# Fast path (KG hit) → jump straight to BuildFindResponse
_kg.next(_build, action="skip_to_build")

find_symbol_flow = AsyncFlow(start=_kg)
