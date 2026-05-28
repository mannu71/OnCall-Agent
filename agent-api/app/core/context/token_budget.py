"""Per-execution token ledger.

Captures every LLM call's token usage in one place so the platform can:
- enforce per-execution budgets before submitting another expensive turn
- surface live spend in SSE (``token_usage_delta`` event)
- persist accurate totals (including the streaming path that previously
  discarded usage)

The ledger is intentionally cheap, thread-safe-by-asyncio (single-loop), and
free of side effects beyond an in-memory list. The caller decides how to
react when ``can_fit`` returns ``False`` — typical responses are "summarise
and continue" or "halt with graceful answer".

Wiring:
    ledger = TokenLedger(budget_input=200_000, budget_output=8_000)
    cb = make_usage_callback(ledger, source="react_agent")
    async for chunk in transport.complete_stream(messages, model=..., on_usage=cb):
        ...

This module has zero imports beyond stdlib + the transport callback type so it
can be used from anywhere without risk of circular imports.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.transport.provider import UsageCallback

logger = logging.getLogger(__name__)


@dataclass
class LedgerEntry:
    """One LLM call's worth of token usage."""
    source: str                # free-form label, e.g. "react_agent", "decompose"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    ts: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenLedger:
    """Per-execution token usage accumulator with optional hard ceilings.

    Budgets are advisory — the ledger never raises; callers consult
    ``can_fit`` / ``over_budget`` and decide policy. This keeps the LLM
    transport layer simple and avoids surprising the rest of the pipeline.
    """
    execution_id: Optional[str] = None
    budget_input: Optional[int] = None       # hard input-token ceiling
    budget_output: Optional[int] = None      # hard output-token ceiling
    budget_cost_usd: Optional[float] = None  # hard $$$ ceiling
    entries: List[LedgerEntry] = field(default_factory=list)

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------
    def record(self, entry: LedgerEntry) -> None:
        """Append a usage entry. Safe to call from any async context."""
        self.entries.append(entry)
        if self.over_budget():
            logger.warning(
                "TokenLedger over budget execution_id=%s totals=%s budgets=%s",
                self.execution_id, self.totals(), self._budgets_dict(),
            )

    def record_dict(self, source: str, usage: Dict[str, Any]) -> None:
        """Convenience: build a ``LedgerEntry`` from the transport callback dict."""
        self.record(LedgerEntry(
            source=source,
            model=str(usage.get("model", "")),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cost_usd=float(usage.get("cost_usd", 0.0) or 0.0),
        ))

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def totals(self) -> Dict[str, Any]:
        """Aggregate counters across all recorded entries."""
        in_tok = sum(e.input_tokens for e in self.entries)
        out_tok = sum(e.output_tokens for e in self.entries)
        cache_c = sum(e.cache_creation_input_tokens for e in self.entries)
        cache_r = sum(e.cache_read_input_tokens for e in self.entries)
        cost = sum(e.cost_usd for e in self.entries)
        return {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "cache_creation_input_tokens": cache_c,
            "cache_read_input_tokens": cache_r,
            "cost_usd": round(cost, 6),
            "calls": len(self.entries),
        }

    def can_fit(self, *, extra_input: int = 0, extra_output: int = 0) -> bool:
        """Return True iff adding ``extra_*`` tokens would still respect budgets.

        A ``None`` budget is treated as unlimited. Used by strategies before
        submitting an expensive turn so they can switch to summarisation
        instead of paying the 3x cost of an overflow retry.
        """
        t = self.totals()
        if self.budget_input is not None and t["input_tokens"] + extra_input > self.budget_input:
            return False
        if self.budget_output is not None and t["output_tokens"] + extra_output > self.budget_output:
            return False
        if self.budget_cost_usd is not None and t["cost_usd"] > self.budget_cost_usd:
            return False
        return True

    def over_budget(self) -> bool:
        return not self.can_fit()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _budgets_dict(self) -> Dict[str, Any]:
        return {
            "input": self.budget_input,
            "output": self.budget_output,
            "cost_usd": self.budget_cost_usd,
        }


def make_usage_callback(ledger: TokenLedger, *, source: str) -> UsageCallback:
    """Build an ``on_usage`` callback that records into the given ledger.

    Designed for the ``ProviderTransport.complete_stream(on_usage=...)`` seam:

        cb = make_usage_callback(ledger, source="react_main")
        async for tok in transport.complete_stream(..., on_usage=cb):
            ...
    """
    async def _cb(usage: Dict[str, Any]) -> None:
        ledger.record_dict(source, usage)
    return _cb
