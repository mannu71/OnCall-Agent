"""
Tool-output compressor adapter.

Sends large tool-output strings to a local compression sidecar via
POST /v1/compress.  The output is wrapped as an assistant-role message
so the sidecar treats it as older context eligible for compression
(it protects user-role messages from compression by design).

Falls back silently to the original text on any error or timeout —
it must never break the agent loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Backoff base for transient sidecar errors (connection refused, 503, etc.).
# Attempt count comes from settings.compression_max_retries (opportunistic:
# the truncation fallback is always correct, so retries stay cheap).
_RETRY_BACKOFF_S = 0.5

# Idempotency marker: text already capped (here, by _truncate_output, or by a
# per-family cap) is skipped so we never compress-then-double-suffix.
_TRUNCATION_MARKER = "[truncated"

# CCR ("content reference") handle the sidecar can emit in place of a span.
_CCR_MARKER = "<ccr:"

# The `search` transform appends a pointer to the spans it dropped, e.g.
#   "[150 matches compressed to 5. Retrieve more: hash=2a986cec3b7018c7]"
# Resolving it requires the sidecar's retrieve tool, which we deliberately never
# expose to the model (HEADROOM_NO_CCR_INJECT_TOOL=1). Left in place the model
# burns turns trying to dereference a handle nothing can answer — the same
# failure mode as a raw CCR marker, just phrased as prose. Keep the useful part
# (how much was dropped), drop the unusable pointer.
_RETRIEVAL_POINTER_RE = re.compile(r"\.?\s*Retrieve more:\s*hash=[0-9a-fA-F]+")

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient()
    return _client


async def close_client() -> None:
    """Close the shared HTTP client.

    Call this from the application lifespan shutdown hook so the connection
    pool is drained cleanly instead of being garbage-collected mid-flight.
    """
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


@dataclass
class CompressionResult:
    text: str
    tokens_before: int = 0
    tokens_after: int = 0
    compressed: bool = False


@dataclass
class ToolCompressionStat:
    """One tool's compression outcomes within a run."""
    calls: int = 0
    compressed: int = 0
    unchanged: int = 0
    skipped: int = 0          # short-circuited by the uncompressible memo
    discarded: int = 0        # sidecar result rejected (e.g. CCR handles)
    errors: int = 0
    chars_before: int = 0
    chars_after: int = 0


@dataclass
class CompressionStats:
    """Per-run accumulator of compression savings (context chars/tokens avoided).

    This is a different dimension from the billed LLM token ledger: it measures
    how much context bulk the sidecar removed before it reached the model.

    Outcome counters matter as much as the savings: measured 2026-07-23, code
    output is returned UNCHANGED 100% of the time (the sidecar's code path needs
    tree-sitter, which the pinned image lacks), so a run can call the sidecar
    dozens of times and save nothing. Without these counters that is invisible.
    """
    calls: int = 0
    chars_before: int = 0
    chars_after: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    unchanged: int = 0
    skipped_uncompressible: int = 0
    discarded: int = 0
    errors: int = 0
    per_tool: Dict[str, ToolCompressionStat] = field(default_factory=dict)

    def tool(self, name: str) -> ToolCompressionStat:
        return self.per_tool.setdefault(name or "?", ToolCompressionStat())

    def saved_pct(self) -> float:
        """Share of submitted chars removed, in [0.0, 1.0]."""
        if self.chars_before <= 0:
            return 0.0
        return round(1.0 - (self.chars_after / self.chars_before), 4)

    def summary(self) -> str:
        """One-line human summary for the end-of-run log."""
        per_tool = ", ".join(
            f"{n}: {s.compressed}/{s.calls} compressed"
            + (f", {s.skipped} skipped" if s.skipped else "")
            + (f", {s.discarded} discarded" if s.discarded else "")
            for n, s in sorted(self.per_tool.items())
        )
        return (
            f"{self.calls} call(s), {self.chars_before}→{self.chars_after} chars "
            f"({self.saved_pct() * 100:.1f}% saved); unchanged={self.unchanged} "
            f"skipped={self.skipped_uncompressible} discarded={self.discarded} "
            f"errors={self.errors}"
            + (f" | {per_tool}" if per_tool else "")
        )


_stats_var: ContextVar[Optional[CompressionStats]] = ContextVar(
    "compression_stats", default=None
)


def start_stats() -> CompressionStats:
    """Reset and return a fresh per-run stats accumulator for this context.

    Mutated in place by the compression path, so it survives the context COPY
    that ``asyncio.wait_for``/``create_task`` makes for delegated subagents —
    the same requirement (and the same solution) as ``harness.usage_ledger``.
    """
    stats = CompressionStats()
    _stats_var.set(stats)
    return stats


def get_stats() -> Optional[CompressionStats]:
    """Return the current context's stats accumulator, or None if unset."""
    return _stats_var.get()


# ── Uncompressible-content memo ──────────────────────────────────────────────
# Some tools return content the sidecar can never shrink — code above all, which
# it routes to `protected:recent_code` and (absent tree-sitter) hands straight
# back. Each such call still costs a round-trip (~100ms measured) on the hot
# tool path for a guaranteed no-op. After a tool has come back unchanged
# _UNCOMPRESSIBLE_STREAK times in a row we stop asking, and re-probe every
# _REPROBE_EVERY skips so a tool whose output shape later changes recovers.
#
# Process-global (not context-scoped) so the knowledge carries across runs, and
# keyed by the REAL tool name — the MCP adapter must pass its own `self.name`,
# never a family label, or codegraph (uncompressible) and a JSON-returning MCP
# tool (52% compressible) would share one entry.
_UNCOMPRESSIBLE_STREAK = 3
_REPROBE_EVERY = 20

_tool_memo: Dict[str, list] = {}  # name -> [unchanged_streak, skips_since_probe]


def _memo_should_skip(tool_name: str) -> bool:
    """True when *tool_name* has earned a skip. Advances the re-probe counter."""
    entry = _tool_memo.get(tool_name)
    if entry is None or entry[0] < _UNCOMPRESSIBLE_STREAK:
        return False
    entry[1] += 1
    if entry[1] >= _REPROBE_EVERY:
        entry[1] = 0
        return False  # let one through to re-check
    return True


def _memo_record(tool_name: str, *, changed: bool) -> None:
    entry = _tool_memo.setdefault(tool_name, [0, 0])
    if changed:
        entry[0] = 0
        entry[1] = 0
    else:
        entry[0] += 1


def reset_tool_memo() -> None:
    """Clear the memo (tests, and any deliberate re-probe of every tool)."""
    _tool_memo.clear()


# ── Endpoint circuit breaker ────────────────────────────────────────────────
# The uncompressible memo above is keyed by TOOL, and deliberately does not
# record errors — an unreachable sidecar says nothing about the content. But
# that left the other failure mode uncovered: when the sidecar is simply down
# (not deployed, crashed, wrong COMPRESSION_ENDPOINT), every tool result over
# the size threshold still paid the full timeout plus every retry backoff, on
# the hot tool-call path, forever. At the shipped defaults that is ~4.5s added
# to each call for a guaranteed failure.
#
# So failures are tracked per ENDPOINT instead: after _BREAKER_THRESHOLD
# consecutive total failures the sidecar is skipped outright for
# _BREAKER_COOLDOWN_S, then one probe is allowed through. Any success closes it.
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 60.0

_breaker: Dict[str, list] = {}  # endpoint -> [consecutive_failures, open_until_ts]


def _breaker_is_open(endpoint: str) -> bool:
    """True when *endpoint* is in cooldown and should be skipped entirely."""
    entry = _breaker.get(endpoint)
    if entry is None or entry[0] < _BREAKER_THRESHOLD:
        return False
    import time as _time
    if _time.monotonic() >= entry[1]:
        # Cooldown elapsed — let ONE call through to re-probe. Reset the clock
        # so a still-dead sidecar re-opens after this probe fails rather than
        # letting every subsequent call through.
        entry[1] = _time.monotonic() + _BREAKER_COOLDOWN_S
        return False
    return True


def _breaker_record(endpoint: str, *, ok: bool) -> None:
    import time as _time
    entry = _breaker.setdefault(endpoint, [0, 0.0])
    if ok:
        entry[0] = 0
        entry[1] = 0.0
        return
    entry[0] += 1
    if entry[0] == _BREAKER_THRESHOLD:
        entry[1] = _time.monotonic() + _BREAKER_COOLDOWN_S
        logger.warning(
            "Compression: sidecar at %s failed %d times in a row — skipping "
            "compression for %.0fs (truncation fallback is unaffected)",
            endpoint, _BREAKER_THRESHOLD, _BREAKER_COOLDOWN_S,
        )


def reset_breaker() -> None:
    """Clear the endpoint circuit breaker (tests, config change)."""
    _breaker.clear()


async def compress_text(
    text: str,
    *,
    endpoint: str,
    timeout_ms: int,
    tool_name: str = "",
) -> CompressionResult:
    """Compress *text* using the local sidecar's /v1/compress endpoint.

    The text is wrapped as an assistant-role message so the sidecar treats it
    as prior-context eligible for compression (user messages are protected).

    Retries up to ``settings.compression_max_retries`` times with exponential
    backoff on transient errors (connection refused, 503, timeout). Returns the
    original text unchanged when all attempts fail.

    ``tool_name`` is used only for accounting — the per-run stats and the
    uncompressible memo — and never changes what is sent to the sidecar.
    """
    if _breaker_is_open(endpoint):
        logger.debug(
            "Compression: skipping '%s' — sidecar circuit breaker open for %s",
            tool_name or "?", endpoint,
        )
        _record(tool_name, "errors", len(text), len(text))
        return CompressionResult(text=text, compressed=False)

    timeout_s = timeout_ms / 1000.0
    max_retries = max(0, settings.compression_max_retries)
    payload = {
        # The sidecar's tokenizer selection keys off "model"; operators set a
        # real deployed model id via COMPRESSION_MODEL_HINT. Compression is
        # fully local — no LLM is called regardless of this value.
        "model": settings.compression_model_hint or "placeholder",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": "tool output"},
            {"role": "assistant", "content": text},
        ],
    }

    for attempt in range(max_retries + 1):
        if attempt > 0:
            backoff = _RETRY_BACKOFF_S * (2 ** (attempt - 1))
            logger.info(
                "Compression: retrying sidecar (attempt %d/%d) after %.1fs backoff",
                attempt + 1, max_retries + 1, backoff,
            )
            await asyncio.sleep(backoff)
        try:
            resp = await _get_client().post(
                f"{endpoint}/v1/compress",
                json=payload,
                timeout=timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()

            messages = data.get("messages") or []
            # Assistant message is index 1; fall back to original if shape unexpected.
            compressed_text = text
            if len(messages) >= 2:
                content = messages[1].get("content", text)
                compressed_text = content if isinstance(content, str) else text

            tokens_before = data.get("tokens_before", 0)
            tokens_after = data.get("tokens_after", 0)

            # Unchanged FIRST. The sidecar hands code straight back byte-identical
            # (its code path needs tree-sitter, which the pinned image lacks), and
            # a no-op result cannot contain anything the sidecar introduced — so
            # there is nothing to inspect and nothing to discard. Checking CCR
            # before this reversed both: a source file that merely MENTIONS the
            # marker (this module does, right below) came back untouched and was
            # still logged as a discard. That was doubly wrong — it reported a
            # phantom loss, and because a discard deliberately skips the memo, the
            # uncompressible-code streak never advanced and every code read kept
            # paying a round-trip forever for a guaranteed no-op.
            # The sidecar answered, whatever it decided about the content —
            # that is what the breaker tracks.
            _breaker_record(endpoint, ok=True)

            if not compressed_text or compressed_text == text:
                logger.info(
                    "Compression: sidecar response unchanged for '%s' "
                    "(tokens_before=%d tokens_after=%d)",
                    tool_name or "?", tokens_before, tokens_after,
                )
                _record(tool_name, "unchanged", len(text), len(text))
                _memo_record(tool_name, changed=False)
                # Sidecar responded successfully but returned unchanged text —
                # no point retrying; return the no-op result.
                return CompressionResult(text=text, compressed=False)

            # Guard: the sidecar's CCR ("content reference") transform replaces
            # spans with opaque `<ccr:hash,type,size>` handles and returns the
            # resolution map SEPARATELY in `ccr_hashes`. We only forward the
            # content, so those handles reach the model with no way to resolve
            # them — the model then loops trying to "dereference" them and burns
            # its whole budget. Never hand back CCR-referenced content: fall back
            # to the original text (which is then capped as usual).
            #
            # The marker test is INTRODUCED-ONLY (present in the output, absent
            # from the input). A plain substring test fires on any payload that
            # quotes the marker as data — reading this repo's own source is enough
            # — which discarded compressions that were perfectly forwardable.
            ccr_hashes = data.get("ccr_hashes") or []
            introduced_marker = (
                _CCR_MARKER in compressed_text and _CCR_MARKER not in text
            )
            if ccr_hashes or introduced_marker:
                logger.warning(
                    "Compression: sidecar returned CCR reference(s) the model "
                    "cannot resolve (%s); discarding compressed output and using "
                    "the original text (%d chars)",
                    "ccr_hashes" if ccr_hashes else "inline marker", len(text),
                )
                _record(tool_name, "discarded", len(text), len(text))
                # A discard is not evidence the content is uncompressible — the
                # sidecar DID compress it, we just cannot forward that form. Do
                # not let it train the skip memo.
                return CompressionResult(text=text, compressed=False)

            compressed_text = _strip_retrieval_pointers(compressed_text)

            _record(tool_name, "compressed", len(text), len(compressed_text),
                    tokens_before=tokens_before, tokens_after=tokens_after)
            _memo_record(tool_name, changed=True)
            return CompressionResult(
                text=compressed_text,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                compressed=True,
            )

        except Exception as exc:  # noqa: BLE001 — compression must never break tool output
            logger.warning(
                "Compression sidecar error (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, exc,
            )

    # Every attempt failed. An unreachable sidecar says nothing about the
    # content, so this must not train the per-tool memo — it trains the
    # per-endpoint breaker instead.
    _breaker_record(endpoint, ok=False)
    _record(tool_name, "errors", len(text), len(text))
    return CompressionResult(text=text, compressed=False)


def _strip_retrieval_pointers(text: str) -> str:
    """Remove sidecar retrieval pointers the model has no tool to resolve.

    Rewrites ``"...compressed to 5. Retrieve more: hash=2a98"`` to
    ``"...compressed to 5 (remainder omitted)"`` — the model still learns that
    content was dropped (so it can re-query the source tool with a narrower
    filter) without being handed a handle that leads nowhere.
    """
    if "Retrieve more" not in text:
        return text
    return _RETRIEVAL_POINTER_RE.sub(" (remainder omitted)", text)


def _record(
    tool_name: str,
    outcome: str,
    chars_before: int,
    chars_after: int,
    *,
    tokens_before: int = 0,
    tokens_after: int = 0,
) -> None:
    """Book one compression outcome against the run's stats. Never raises."""
    stats = _stats_var.get()
    if stats is None:
        return
    try:
        per = stats.tool(tool_name)
        per.calls += 1
        per.chars_before += chars_before
        per.chars_after += chars_after
        setattr(per, outcome, getattr(per, outcome, 0) + 1)

        if outcome == "skipped":
            stats.skipped_uncompressible += 1
            return  # a skip costs nothing and shrinks nothing; not a "call"
        stats.calls += 1
        stats.chars_before += chars_before
        stats.chars_after += chars_after
        stats.tokens_before += tokens_before
        stats.tokens_after += tokens_after
        if outcome == "unchanged":
            stats.unchanged += 1
        elif outcome == "discarded":
            stats.discarded += 1
        elif outcome == "errors":
            stats.errors += 1
    except Exception:  # noqa: BLE001 — accounting must never break tool output
        pass


async def compress_then_cap(
    text: str,
    cap: Callable[[str], str],
    *,
    min_chars: Optional[int] = None,
    tool_name: str = "",
) -> str:
    """Compress large text when enabled, then ALWAYS apply *cap*.

    This is the single shared entry point for tool-output compression. Compression
    raises information density *under* the cap — it never lifts the ceiling — so a
    successful compress still passes through ``cap``. When compression is disabled,
    the sidecar is unreachable, the text is below ``min_chars``, or it is already
    truncated, this is exactly ``cap(text)`` (byte-identical to the no-compression
    path).

    ``cap`` is supplied by the caller (MCP truncation hint vs. the universal
    per-family cap) so this module stays free of caller-specific formatting.
    """
    if not isinstance(text, str):
        return cap(text)  # type: ignore[arg-type]
    threshold = settings.compression_min_chars if min_chars is None else min_chars
    if (
        settings.compression_enabled
        and len(text) > threshold
        and _TRUNCATION_MARKER not in text[-200:]
    ):
        # Don't pay a round-trip for a tool that has proven it never shrinks.
        if _memo_should_skip(tool_name):
            logger.debug(
                "Compression: skipping '%s' — %d consecutive unchanged results",
                tool_name or "?", _UNCOMPRESSIBLE_STREAK,
            )
            _record(tool_name, "skipped", len(text), len(text))
            return cap(text)

        logger.info(
            "Compression: attempting %d-char output from '%s' (min_chars=%d)",
            len(text), tool_name or "?", threshold,
        )
        result = await compress_text(
            text,
            endpoint=settings.compression_endpoint,
            timeout_ms=settings.compression_timeout_ms,
            tool_name=tool_name,
        )
        if result.compressed:
            logger.info(
                "Compression: %d → %d chars (%.0f%% saved, %d → %d tokens) from '%s'",
                len(text), len(result.text),
                (1 - len(result.text) / max(len(text), 1)) * 100,
                result.tokens_before, result.tokens_after, tool_name or "?",
            )
            return cap(result.text)
        logger.info(
            "Compression: sidecar returned unchanged text for %d-char output; capping only",
            len(text),
        )
    return cap(text)
