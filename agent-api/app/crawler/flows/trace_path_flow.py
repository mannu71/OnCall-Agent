"""tracePathFlow — trace call-graph edges for a symbol.

DAG (post knowledge-graph migration, plan step 6)::

    TraverseKGEdges  >>  BuildEdgeList

The old LLM-based pipeline (FindSymbolDef → ScanCallerCandidates →
LLMResolveEdges → VerifyEachEdge) is replaced by a single Postgres recursive
CTE that walks ``kg_edges`` directly.  This is exact, deterministic, and
returns in tens of milliseconds versus tens of seconds.

The old classes remain importable in :mod:`app.crawler.nodes.trace` for
backward compatibility but are no longer chained in the default flow.
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes.trace import (
    TraverseKGEdges,
    BuildEdgeList,
)

_traverse = TraverseKGEdges()
_build = BuildEdgeList()

_traverse >> _build

trace_path_flow = AsyncFlow(start=_traverse)
