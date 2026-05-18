"""Embedding helpers — Bedrock Titan (default) and Voyage code-3 (opt-in).

Extracted from ``app.services.code_indexer`` so the legacy file can shrink
without changing behavior. The public surface (``_embed_text``,
``_embed_texts``) is re-exported from ``code_indexer`` for backward
compatibility.

Phase 3 additions
-----------------
``embed_with_voyage``   — raw HTTP call to Voyage AI (voyage-code-3, 1024-dim).
``embed_text``          — provider router; reads ``EMBEDDING_PROVIDER`` env var.
                          Set ``EMBEDDING_PROVIDER=voyage`` and ``VOYAGE_API_KEY``
                          to switch; otherwise falls back to Bedrock Titan.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider-choice cache (log once, not on every call)
# ---------------------------------------------------------------------------
_provider_logged: bool = False

# Bedrock Titan embedding model (fallback when nothing is configured in the
# Settings page). The active model is looked up at call time from the
# ``llm_configs`` table where ``use_for_embeddings = true``.
_EMBED_MODEL = "amazon.titan-embed-text-v2:0"


async def _resolve_embedding_config() -> tuple[str, str, Optional[str]]:
    """Return (model_id, region, aws_profile) from the active LLM config.

    Reads the row in ``llm_configs`` flagged ``use_for_embeddings = true``. If
    no row is flagged (fresh install / pre-Settings POC) we fall back to the
    module-level Titan default in ``us-east-1`` and no profile override.
    """
    try:
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
        from app.models.db_models import LLMConfigModel

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel)
                .where(LLMConfigModel.use_for_embeddings.is_(True))
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return (
                    row.model or _EMBED_MODEL,
                    row.region or "us-east-1",
                    getattr(row, "aws_profile", None) or None,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "embedding: failed to read llm_configs (%s); "
            "falling back to default Titan model in us-east-1.", exc,
        )
    return _EMBED_MODEL, "us-east-1", None


async def _embed_text(
    text_to_embed: str,
    region: Optional[str] = None,
    model_id: Optional[str] = None,
    aws_profile: Optional[str] = None,
) -> Optional[List[float]]:
    """Call Bedrock and return an embedding vector.

    If ``region`` / ``model_id`` are not supplied, they are resolved from the
    Settings-page configuration (``llm_configs.use_for_embeddings = true``).
    This is the "right" way for callers to embed — it honors what the user
    picked in the UI instead of hard-coding the model and region.
    """
    import json as _json
    import boto3

    if region is None or model_id is None:
        cfg_model, cfg_region, cfg_profile = await _resolve_embedding_config()
        model_id = model_id or cfg_model
        region = region or cfg_region
        aws_profile = aws_profile or cfg_profile

    def _call() -> Optional[List[float]]:
        if aws_profile:
            session = boto3.Session(profile_name=aws_profile)
            client = session.client("bedrock-runtime", region_name=region)
        else:
            client = boto3.client("bedrock-runtime", region_name=region)
        payload = _json.dumps({"inputText": text_to_embed[:8191]})
        try:
            resp = client.invoke_model(
                modelId=model_id,
                body=payload,
                contentType="application/json",
                accept="application/json",
            )
            result = _json.loads(resp["body"].read())
            return result.get("embedding")
        except Exception as exc:
            logger.warning(
                "Bedrock embedding failed (model=%s region=%s): %s",
                model_id, region, exc,
            )
            return None

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call)


# ---------------------------------------------------------------------------
# Phase 3: Voyage AI embedding provider (opt-in, no voyageai SDK)
# ---------------------------------------------------------------------------

_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_MODEL = "voyage-code-3"
_VOYAGE_TIMEOUT = 30.0  # seconds


async def embed_with_voyage(text: str | List[str]) -> List[List[float]]:
    """Call Voyage AI to embed *text* using voyage-code-3 (1024-dim).

    Uses raw HTTP via ``httpx`` (already a project dependency) — no Voyage SDK.
    Reads the API key from the ``VOYAGE_API_KEY`` environment variable.

    Args:
        text: A single string or a list of strings to embed.

    Returns:
        A list of embedding vectors (list[list[float]]), one per input string.
        Single-string inputs are still returned as a one-element outer list.

    Raises:
        RuntimeError: When ``VOYAGE_API_KEY`` is not set.
        RuntimeError: When the Voyage API returns a non-2xx response after
                      one retry.
    """
    import httpx

    api_key = os.getenv("VOYAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "VOYAGE_API_KEY environment variable is not set. "
            "Set it to use the Voyage embedding provider."
        )

    inputs: List[str] = [text] if isinstance(text, str) else list(text)
    payload = {"model": _VOYAGE_MODEL, "input": inputs}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _call_once(client: httpx.AsyncClient) -> List[List[float]]:
        resp = await client.post(
            _VOYAGE_API_URL,
            json=payload,
            headers=headers,
            timeout=_VOYAGE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # Voyage response: {"data": [{"embedding": [...], "index": 0}, ...]}
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    async with httpx.AsyncClient() as client:
        try:
            return await _call_once(client)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            is_transient = isinstance(exc, httpx.TimeoutException) or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code >= 500
            )
            if not is_transient:
                raise RuntimeError(
                    f"Voyage API returned a non-retryable error: {exc}"
                ) from exc
            logger.warning(
                "embed_with_voyage: transient error (%s), retrying once…", exc
            )
            try:
                return await _call_once(client)
            except Exception as retry_exc:
                raise RuntimeError(
                    f"Voyage API call failed after retry: {retry_exc}"
                ) from retry_exc


# ---------------------------------------------------------------------------
# Phase 3: Provider router
# ---------------------------------------------------------------------------


async def embed_text(text: str | List[str]) -> List[List[float]]:
    """Route embedding to Voyage or Bedrock depending on env configuration.

    Reads ``EMBEDDING_PROVIDER`` (default ``"bedrock"``):
      - ``"voyage"`` + ``VOYAGE_API_KEY`` set → :func:`embed_with_voyage`
      - anything else                         → Bedrock Titan via
        :func:`_embed_text` / :func:`_embed_texts`

    Returns:
        A list of embedding vectors, one per input string.
    """
    global _provider_logged

    provider = os.getenv("EMBEDDING_PROVIDER", "bedrock").lower()
    voyage_key = os.getenv("VOYAGE_API_KEY", "")

    use_voyage = provider == "voyage" and bool(voyage_key)

    if not _provider_logged:
        chosen = "voyage (voyage-code-3)" if use_voyage else "bedrock (Titan)"
        logger.info("embed_text: using provider=%s", chosen)
        _provider_logged = True

    if use_voyage:
        return await embed_with_voyage(text)

    # Bedrock path — normalise to list[list[float]] output
    inputs: List[str] = [text] if isinstance(text, str) else list(text)
    if len(inputs) == 1:
        single = await _embed_text(inputs[0])
        return [single] if single is not None else [[]]
    results = await _embed_texts(inputs)
    return [v if v is not None else [] for v in results]


async def _embed_texts(
    texts: List[str],
    region: Optional[str] = None,
    concurrency: Optional[int] = None,
    model_id: Optional[str] = None,
    aws_profile: Optional[str] = None,
) -> List[Optional[List[float]]]:
    """Embed many texts concurrently. Titan v2 is single-input only, so we
    fan out N concurrent InvokeModel calls bounded by a semaphore. Concurrency
    defaults to env var ``BEDROCK_EMBED_CONCURRENCY`` (fallback 16). Returns
    embeddings in input order; failed items map to ``None``."""
    if not texts:
        return []
    if concurrency is None:
        try:
            concurrency = int(os.getenv("BEDROCK_EMBED_CONCURRENCY", "16"))
        except ValueError:
            concurrency = 16
    concurrency = max(1, concurrency)
    sem = asyncio.Semaphore(concurrency)

    # Resolve the embedding config ONCE for the whole batch so we don't hit
    # the DB N times when fanning out thousands of chunk embeddings.
    if region is None or model_id is None:
        cfg_model, cfg_region, cfg_profile = await _resolve_embedding_config()
        model_id = model_id or cfg_model
        region = region or cfg_region
        aws_profile = aws_profile or cfg_profile
        logger.info(
            "embedding: using model=%s region=%s profile=%s for %d texts",
            model_id, region, aws_profile or "<default>", len(texts),
        )

    async def _one(t: str) -> Optional[List[float]]:
        async with sem:
            return await _embed_text(
                t,
                region=region,
                model_id=model_id,
                aws_profile=aws_profile,
            )

    return await asyncio.gather(*[_one(t) for t in texts])
