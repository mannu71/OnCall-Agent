"""Context-management utilities (token budgeting, ledgers).

Placed under ``app.core.context`` so future modules (compression, sliding
summarisation, reference expansion) can co-locate without colliding with
the existing ``app.core.context_references`` and ``app.core.context_compression``
modules.
"""
from app.core.context.token_budget import (
    LedgerEntry,
    TokenLedger,
    make_usage_callback,
)
from app.core.context.sliding_window import (
    SummariseFn,
    maybe_summarise,
)

__all__ = [
    "LedgerEntry",
    "TokenLedger",
    "make_usage_callback",
    "SummariseFn",
    "maybe_summarise",
]
