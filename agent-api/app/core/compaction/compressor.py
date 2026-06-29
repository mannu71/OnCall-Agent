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
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Retry parameters for transient sidecar errors (connection refused, 503, etc.).
# Two retries with simple exponential backoff: 0.5 s → 1.0 s.
_MAX_RETRIES = 2
_RETRY_BACKOFF_S = 0.5

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


async def compress_text(
    text: str,
    *,
    endpoint: str,
    timeout_ms: int,
) -> CompressionResult:
    """Compress *text* using the local sidecar's /v1/compress endpoint.

    The text is wrapped as an assistant-role message so the sidecar treats it
    as prior-context eligible for compression (user messages are protected).

    Retries up to ``_MAX_RETRIES`` times with exponential backoff on transient
    errors (connection refused, 503, timeout). Returns the original text
    unchanged when all attempts fail.
    """
    timeout_s = timeout_ms / 1000.0
    payload = {
        # "model" is required by schema but compression is fully local — no LLM is called.
        "model": "placeholder",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": "tool output"},
            {"role": "assistant", "content": text},
        ],
    }

    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            backoff = _RETRY_BACKOFF_S * (2 ** (attempt - 1))
            logger.info(
                "Compression: retrying sidecar (attempt %d/%d) after %.1fs backoff",
                attempt + 1, _MAX_RETRIES + 1, backoff,
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
                attempt + 1, _MAX_RETRIES + 1, exc,
            )

    return CompressionResult(text=text, compressed=False)
