"""Run-scoped ledger for token usage spent by *delegated* subagents.

A delegated child (see :mod:`app.harness.subagent_factory`) runs under its own
``execution_id`` with its own ``TokenUsageCallback``, so its usage is invisible
to the parent's counters. Before this module existed the child's totals were
dropped on the floor: the ``executions`` row, the Chat UI context bar and the
supervisor's token budget all reported the PARENT's tokens only.

That under-reporting is not cosmetic. A measured run (execution 240, 2026-07-23)
recorded 115,415 input tokens while seven children spent a further 906,029 —
the record showed 11% of what the run actually cost, and the supervisor's token
budget was comparing a 100,000 ceiling against that same 11%.

The ledger closes the gap. ``open_ledger`` installs a fresh accumulator for the
run, children add to it as they finish, and the supervisor loop drains it after
each agent turn so the totals it accumulates — and therefore everything
downstream — cover the whole agent tree.

Context propagation
-------------------
The ledger is held in a :class:`~contextvars.ContextVar` but is **mutated in
place**, never re-``set``. A child runs inside ``asyncio.wait_for`` /
``create_task``, which COPIES the context: a ``set`` there would be invisible to
the parent, while mutating the shared object is seen by everyone holding a
reference. This is the same pattern as ``_parent_stream_cb`` in
``subagent_factory``.

Depth is not a concern: each agent's counters cover only its own LLM calls, so
flat summation stays correct even if a child ever delegates further.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


@dataclass
class ChildRun:
    """One delegated child's contribution, kept for attribution.

    The aggregate alone cannot answer "which subagent cost the money?", and on a
    fan-out run that is the question worth asking: on execution 240 seven
    children ranged from 24,137 to 302,527 input tokens — a 12.5x spread that a
    single total hides completely.
    """

    child_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "child_id": self.child_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
        }


@dataclass
class AuxRun:
    """One auxiliary (non-agent) LLM call — compaction, grader, extractors.

    These go through ``transport.complete`` / ``call_llm`` rather than the agent
    graph, so no ``TokenUsageCallback`` ever sees them. Context compaction is the
    one that matters: it summarises up to ~20,000 tokens of history and fires
    exactly on the long runs where cost is being questioned.
    """

    source: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class RunUsage:
    """Every token a run spent that the parent agent's own callback cannot see:
    delegated children plus auxiliary LLM calls.

    ``input_tokens`` is the WHOLE prompt; ``cache_read_tokens`` and
    ``cache_creation_tokens`` are a BREAKDOWN of it, not counters to add to it
    (see ``app.core.observability.cache_metrics`` for the live-Bedrock
    evidence). Do not sum the three.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    runs: int = 0                    # delegated children
    aux_calls: int = 0               # auxiliary LLM calls
    per_child: List[ChildRun] = field(default_factory=list)
    per_aux: List[AuxRun] = field(default_factory=list)

    def as_dict(self) -> Dict[str, int]:
        return {f: getattr(self, f) for f in _FIELDS}

    def empty(self) -> bool:
        return not (self.runs or self.aux_calls)

    def breakdown(self) -> List[Dict[str, Any]]:
        """Per-child rows, most expensive first."""
        return [
            c.as_dict()
            for c in sorted(self.per_child, key=lambda c: c.input_tokens, reverse=True)
        ]

    def aux_breakdown(self) -> List[Dict[str, Any]]:
        """Auxiliary calls aggregated by source, most expensive first."""
        by_source: Dict[str, Dict[str, Any]] = {}
        for a in self.per_aux:
            row = by_source.setdefault(
                a.source, {"source": a.source, "calls": 0,
                           "input_tokens": 0, "output_tokens": 0},
            )
            row["calls"] += 1
            row["input_tokens"] += a.input_tokens
            row["output_tokens"] += a.output_tokens
        return sorted(by_source.values(), key=lambda r: r["input_tokens"], reverse=True)


#: Historical alias — the ledger covered only children before auxiliary LLM
#: calls were folded in (2026-07-23).
ChildUsage = RunUsage


_ledger: ContextVar[Optional[RunUsage]] = ContextVar(
    "subagent_usage_ledger", default=None
)


def open_ledger() -> RunUsage:
    """Install a fresh ledger for this run and return it.

    Called once per run, high enough in the call tree to be an ancestor context
    of both the delegating agent and whoever drains the totals. Because a plain
    ``await`` does NOT copy the context, the ledger set inside ``run_supervised``
    is still visible to its caller afterwards — which is what lets the finalizer
    capture post-loop auxiliary calls (auto_learn / grader).
    """
    ledger = RunUsage()
    _ledger.set(ledger)
    return ledger


def record_auxiliary_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    source: str = "",
    exclusive: bool = False,
) -> None:
    """Bank one non-agent LLM call (compaction, grader, extractors, …).

    Set ``exclusive=True`` when the counters come straight off Bedrock's RAW
    wire format — ``TransportResponse`` and anything reading ``usage`` out of an
    ``invoke_model`` body. Bedrock reports input_tokens EXCLUSIVE of the cache
    counters, while this ledger (like everything downstream of
    ``TokenUsageCallback``) is INCLUSIVE. Getting this backwards under-reports a
    cache-warm call by the whole cached prefix. See
    ``app.core.observability.cache_metrics`` for the live probe.

    Best-effort and never raises; a no-op when no ledger is open.
    """
    ledger = _ledger.get()
    if ledger is None:
        return
    try:
        inp = int(input_tokens or 0)
        read = int(cache_read_tokens or 0)
        write = int(cache_creation_tokens or 0)
        out = int(output_tokens or 0)
        if exclusive:
            inp += read + write

        ledger.input_tokens += inp
        ledger.output_tokens += out
        ledger.cache_read_tokens += read
        ledger.cache_creation_tokens += write
        ledger.aux_calls += 1
        ledger.per_aux.append(
            AuxRun(source=source or "?", input_tokens=inp, output_tokens=out)
        )
        logger.debug(
            "usage_ledger: recorded auxiliary call from %s (in=%d out=%d)",
            source or "?", inp, out,
        )
    except Exception as exc:  # noqa: BLE001 — accounting must never break a run
        logger.debug("usage_ledger: skipped auxiliary usage from %s (%s)", source, exc)


def record_child_usage(result: Mapping[str, Any], *, child_id: str = "") -> None:
    """Add one finished child's usage to the active ledger.

    Best-effort and never raises — accounting must not be able to break a run.
    A no-op when no ledger is open (e.g. a unit test driving a child directly).
    """
    ledger = _ledger.get()
    if ledger is None:
        return
    try:
        for field_name in _FIELDS:
            setattr(
                ledger, field_name,
                getattr(ledger, field_name) + int(result.get(field_name, 0) or 0),
            )
        ledger.runs += 1
        ledger.per_child.append(ChildRun(
            child_id=child_id or "?",
            input_tokens=int(result.get("input_tokens", 0) or 0),
            output_tokens=int(result.get("output_tokens", 0) or 0),
            tool_calls=len(result.get("tool_calls", []) or []),
        ))
        logger.debug(
            "usage_ledger: recorded child %s (in=%s out=%s); run total in=%d out=%d over %d child run(s)",
            child_id or "?", result.get("input_tokens"), result.get("output_tokens"),
            ledger.input_tokens, ledger.output_tokens, ledger.runs,
        )
    except Exception as exc:  # noqa: BLE001 — accounting must never break a run
        logger.debug("usage_ledger: skipped child usage for %s (%s)", child_id or "?", exc)


def drain_usage() -> RunUsage:
    """Return the usage banked since the last drain and reset the counters.

    Counters are reset alongside the token fields so repeated drains (one per
    supervisor iteration, plus a final one in the finalizer) partition the run
    rather than double-counting it.
    """
    ledger = _ledger.get()
    if ledger is None:
        return RunUsage()
    drained = RunUsage(
        input_tokens=ledger.input_tokens,
        output_tokens=ledger.output_tokens,
        cache_read_tokens=ledger.cache_read_tokens,
        cache_creation_tokens=ledger.cache_creation_tokens,
        runs=ledger.runs,
        aux_calls=ledger.aux_calls,
        per_child=list(ledger.per_child),
        per_aux=list(ledger.per_aux),
    )
    for field_name in _FIELDS:
        setattr(ledger, field_name, 0)
    ledger.runs = 0
    ledger.aux_calls = 0
    ledger.per_child.clear()
    ledger.per_aux.clear()
    return drained


#: Historical alias — see :data:`ChildUsage`.
drain_child_usage = drain_usage
