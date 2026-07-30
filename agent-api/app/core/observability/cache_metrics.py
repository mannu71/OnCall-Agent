"""Prompt-cache efficiency metric.

The raw counters (``input_tokens`` / ``cache_read_tokens`` /
``cache_creation_tokens``) have been plumbed end to end for a long time —
``core.streaming.callbacks`` -> ``harness.agent_runner`` ->
``workflow.executor.handlers.agent`` -> ``workflow.executor.result`` ->
``chat_sessions.total_cache_*`` — but nothing ever computed a RATIO from them,
so nobody could tell a healthy cache from a broken one at a glance.

That matters because the health is invisible and the failure is silent. The
system prompt and the tool schemas ride a 1h Bedrock cachePoint, and staying
cacheable depends on a convention, not a mechanism: ``compose_system_prompt``
must be deterministic given ``agent_config`` (its CACHE CONTRACT). Anything that
quietly breaks that — a timestamp added to a capability section, an MCP server
reshuffling its advertised tool order between runs — does not raise. It just
moves tokens from the cheap read path to the full-price write path, and the only
symptom is a bill.

Measured baseline, 2026-07-21, 26 sessions:

    input 4,436,160 | cache_read 3,436,225  ->  hit rate 0.775

``cache_read`` is a SUBSET of ``input_tokens``, so the denominator is
``input_tokens`` alone. This is the one thing to get right here, and it is easy
to get wrong: several comments in this repo still describe cache_read as
"ADDITIVE to input_tokens, not a subset". That was true of an older
langchain_aws; it is not true now. ``bedrock_converse._parse_usage_metadata``
computes ``input_tokens = bedrock_input + cache_read + cache_write``, and
``callbacks._parse_llm_output`` normalizes the raw-provider branches to match.
Dividing by ``input + cache_read`` instead double-counts and understates the
rate — it reported 0.436 for the 0.775 baseline above.

On ``cache_creation``: it read 0 in every session for a long time, which cannot
mean "no writes ever happened" — reads require prior writes. Cause found
2026-07-21 and fixed in ``callbacks._parse_cache_tokens``: when a TTL is set
(react_agent always sets ttl="1h") Bedrock returns a per-TTL ``cacheDetails``
breakdown, and langchain_aws puts the write under
``ephemeral_1h_input_tokens`` while explicitly setting ``cache_creation`` to 0.
Live probe (Claude Haiku 4.5):

    call 1 (write): cache_read=0     cache_creation=0  ephemeral_1h=4962
    call 2 (read):  cache_read=4962  cache_creation=0  ephemeral_1h absent

Sessions written BEFORE that fix have ``total_cache_creation_tokens = 0``
regardless of what really happened; do not read historical write cost as real.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Below this, something is likely busting the cached prefix. Chosen from the
#: 0.775 baseline with generous headroom: short single-turn runs legitimately
#: sit low (nothing is cached yet on turn one), so this must only fire on a
#: real collapse, not on normal variance.
CACHE_HIT_RATE_FLOOR = 0.25

#: Runs smaller than this have too little signal to judge — a two-message turn
#: can be 0.0 with a perfectly healthy cache.
_MIN_TOKENS_TO_JUDGE = 20_000

#: Billing RATIOS relative to the base input rate, used by :func:`billed_units`.
#: Deliberately ratios and not prices: they are stable across Anthropic models,
#: so the weighting stays correct whichever model the DB config resolves to.
#: Per-model dollar pricing is DB-configured and must never be hardcoded here.
RATE_FRESH_INPUT = 1.0
RATE_CACHE_READ = 0.1
RATE_CACHE_WRITE = 1.25
RATE_OUTPUT = 5.0


def cache_hit_rate(
    input_tokens: int,
    cache_read_tokens: int,
) -> float:
    """Share of prompt tokens served from cache, in [0.0, 1.0].

    ``cache_read_tokens / input_tokens`` — cache_read is a SUBSET of
    input_tokens (see module docstring). Returns 0.0 when there is nothing to
    divide, never raises, and never returns None: callers put this straight
    into a result dict and a JSON response.

    Defensive fallback: if ``cache_read`` exceeds ``input_tokens`` the data must
    have come from a source using the EXCLUSIVE convention (a provider we have
    not normalized, or a row written before ``_parse_llm_output`` started
    reconciling). Rather than return a nonsense rate above 1.0, treat the two as
    disjoint and divide by their sum, which is the correct reading of exclusive
    data. A silently-capped 1.0 would hide the mismatch; this keeps the number
    meaningful either way.
    """
    inp = max(int(input_tokens or 0), 0)
    read = max(int(cache_read_tokens or 0), 0)
    if read > inp:
        total = inp + read
        return round(read / total, 4) if total > 0 else 0.0
    if inp <= 0:
        return 0.0
    return round(read / inp, 4)


def billed_units(
    input_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    output_tokens: int,
) -> Dict[str, Any]:
    """Convert a run's raw counters into COST-WEIGHTED units.

    Raw token totals are a poor optimization target because the four components
    are billed at very different rates. Measured on execution 240's parent
    (2026-07-23), the naive total says 119,078 tokens while the weighted view
    says where the money actually went:

        cache_creation  21,635 x 1.25 = 27,044   (49%)  <- the lever
        output           3,663 x 5.00 = 18,315   (34%)
        cache_read      93,768 x 0.10 =  9,377   (17%)
        fresh input         12 x 1.00 =     12   ( 0%)

    That ordering is the whole point: cache WRITES dominate, and a cache write
    is new content entering the context — overwhelmingly tool results. Trimming
    the cached prefix (system prompt, tool schemas) targets the 17% line.

    ``fresh`` is derived, not measured: ``input_tokens`` is INCLUSIVE of both
    cache counters (see the module docstring), so the never-cached remainder is
    ``input - read - creation``. Clamped at 0 — a provider whose counters do not
    reconcile must not produce a negative component.

    The multipliers are RATIOS to the base input rate, not prices. They are the
    published Anthropic cache/output ratios, which are stable across models,
    so this stays model-agnostic — real per-model pricing belongs in the DB
    model config, never in code.
    """
    inp = max(int(input_tokens or 0), 0)
    read = max(int(cache_read_tokens or 0), 0)
    write = max(int(cache_creation_tokens or 0), 0)
    out = max(int(output_tokens or 0), 0)
    fresh = max(inp - read - write, 0)

    components = {
        "fresh_input": round(fresh * RATE_FRESH_INPUT, 1),
        "cache_read": round(read * RATE_CACHE_READ, 1),
        "cache_creation": round(write * RATE_CACHE_WRITE, 1),
        "output": round(out * RATE_OUTPUT, 1),
    }
    total = round(sum(components.values()), 1)
    return {
        "billed_units": total,
        "billed_breakdown": components,
        "billed_shares": {
            k: (round(v / total, 4) if total > 0 else 0.0)
            for k, v in components.items()
        },
        "raw_tokens": inp + out,
        "fresh_input_tokens": fresh,
    }


def annotate_cache_metrics(
    target: Dict[str, Any],
    input_tokens: int,
    cache_read_tokens: int,
    *,
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """Add ``cache_hit_rate`` to *target* and warn if the cache has collapsed.

    Mutates and returns *target* so it can be used inline. The WARNING is the
    actual point of this module: it is the tripwire for a prefix-busting
    regression, which is otherwise silent.
    """
    rate = cache_hit_rate(input_tokens, cache_read_tokens)
    target["cache_hit_rate"] = rate

    # input_tokens is inclusive of cache_read, so it IS the prompt total.
    total = max(int(input_tokens or 0), int(cache_read_tokens or 0))
    if total >= _MIN_TOKENS_TO_JUDGE and rate < CACHE_HIT_RATE_FLOOR:
        logger.warning(
            "Prompt cache hit rate %.3f is below the %.2f floor over %d prefix "
            "tokens (input=%d, cache_read=%d)%s — something may be busting the "
            "cached prefix. Check that nothing run-specific (timestamp, "
            "execution id, recalled memory) has been added to the system prompt "
            "or a capability section, and that MCP tool ordering is stable.",
            rate, CACHE_HIT_RATE_FLOOR, total,
            int(input_tokens or 0), int(cache_read_tokens or 0),
            f" [{context}]" if context else "",
        )
    return target
