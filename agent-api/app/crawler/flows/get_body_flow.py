"""getBodyFlow — retrieve source lines for a body handle.

DAG: ResolveHandle >> ReadFile >> SliceLines >> BuildBodyResponse
"""
from app.engine.crawler_engine import AsyncFlow
from app.crawler.nodes import (
    ResolveHandle,
    ReadFile,
    SliceLines,
    BuildBodyResponse,
)

_resolve = ResolveHandle()
_read = ReadFile()
_slice = SliceLines()
_build = BuildBodyResponse()

_resolve >> _read >> _slice >> _build

get_body_flow = AsyncFlow(start=_resolve)
