"""searchSemanticFlow — answer freeform queries against an indexed repo.

DAG: LookupOverview >> LLMSuggestFiles >> ReadFiles
          >> LLMExtractMatches >> VerifyOnDisk >> BuildHitList
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes.semantic import (
    LookupOverview,
    LLMSuggestFiles,
    ReadFiles,
    LLMExtractMatches,
    VerifyOnDisk,
    BuildHitList,
)

_lookup = LookupOverview()
_suggest = LLMSuggestFiles(max_retries=3, wait=10.0)
_read = ReadFiles()
_extract = LLMExtractMatches(max_retries=3, wait=10.0)
_verify = VerifyOnDisk()
_build = BuildHitList()

_lookup >> _suggest >> _read >> _extract >> _verify >> _build

search_semantic_flow = AsyncFlow(start=_lookup)
