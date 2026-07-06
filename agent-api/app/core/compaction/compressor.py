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
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

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
class CompressionStats:
    """Per-run accumulator of compression savings (context chars/tokens avoided).

    This is a different dimension from the billed LLM token ledger: it measures
    how much context bulk the sidecar removed before it reached the model.
    """
    calls: int = 0
    chars_before: int = 0
    chars_after: int = 0
    tokens_before: int = 0
    tokens_after: int = 0


_stats_var: ContextVar[Optional[CompressionStats]] = ContextVar(
    "compression_stats", default=None
)


def start_stats() -> CompressionStats:
    """Reset and return a fresh per-run stats accumulator for this context."""
    stats = CompressionStats()
    _stats_var.set(stats)
    return stats


def get_stats() -> Optional[CompressionStats]:
    """Return the current context's stats accumulator, or None if unset."""
    return _stats_var.get()


async def compress_text(
    text: str,
    *,
    endpoint: str,
    timeout_ms: int,
) -> CompressionResult:
    """Compress *text* using the local sidecar's /v1/compress endpoint.

    The text is wrapped as an assistant-role message so the sidecar treats it
    as prior-context eligible for compression (user messages are protected).

    Retries up to ``settings.compression_max_retries`` times with exponential
    backoff on transient errors (connection refused, 503, timeout). Returns the
    original text unchanged when all attempts fail.
    """
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

            if compressed_text and compressed_text != text:
                stats = _stats_var.get()
                if stats is not None:
                    stats.calls += 1
                    stats.chars_before += len(text)
                    stats.chars_after += len(compressed_text)
                    stats.tokens_before += tokens_before
                    stats.tokens_after += tokens_after
                return CompressionResult(
                    text=compressed_text,
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                    compressed=True,
                )
            logger.info(
                "Compression: sidecar response unchanged (tokens_before=%d tokens_after=%d)",
                tokens_before,
                tokens_after,
            )
            # Sidecar responded successfully but returned unchanged text —
            # no point retrying; break out and return the no-op result.
            return CompressionResult(text=text, compressed=False)

        except Exception as exc:  # noqa: BLE001 — compression must never break tool output
            logger.warning(
                "Compression sidecar error (attempt %d/%d): %s",
                attempt + 1, max_retries + 1, exc,
            )

    return CompressionResult(text=text, compressed=False)


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
        logger.info(
            "Compression: attempting %d-char output from '%s' (min_chars=%d)",
            len(text), tool_name or "?", threshold,
        )
        result = await compress_text(
            text,
            endpoint=settings.compression_endpoint,
            timeout_ms=settings.compression_timeout_ms,
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
