"""Tests for the Phase 3 Voyage AI embedding provider and router.

Covers:
  1. embed_text falls back to Bedrock when EMBEDDING_PROVIDER != "voyage".
  2. embed_text calls Voyage when env is set (mock the HTTP layer).
  3. embed_with_voyage raises RuntimeError when VOYAGE_API_KEY is missing.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helper: build a fake Voyage API response
# ---------------------------------------------------------------------------


def _voyage_response(n: int = 1, dim: int = 1024) -> dict[str, Any]:
    """Build a minimal Voyage AI embeddings API response body."""
    return {
        "data": [
            {"index": i, "embedding": [0.1 * (i + 1)] * dim}
            for i in range(n)
        ],
        "model": "voyage-code-3",
        "usage": {"total_tokens": 10 * n},
    }


# ---------------------------------------------------------------------------
# Test 1: embed_text falls back to Bedrock when provider != voyage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_text_falls_back_to_bedrock_when_provider_not_voyage(monkeypatch):
    """When EMBEDDING_PROVIDER is not 'voyage', embed_text should call _embed_text."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bedrock")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    fake_embedding = [0.42] * 1536

    import app.services.code_indexing.embedding as emb_mod
    emb_mod._provider_logged = False  # reset cache so provider is re-evaluated

    with patch(
        "app.services.code_indexing.embedding._embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ) as mock_bedrock:
        result = await emb_mod.embed_text("hello world")

    mock_bedrock.assert_called_once_with("hello world")
    assert result == [fake_embedding]


@pytest.mark.asyncio
async def test_embed_text_falls_back_when_voyage_key_missing(monkeypatch):
    """When EMBEDDING_PROVIDER=voyage but key missing, should fall back to Bedrock."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    fake_embedding = [0.1] * 1536

    import app.services.code_indexing.embedding as emb_mod
    emb_mod._provider_logged = False

    with patch(
        "app.services.code_indexing.embedding._embed_text",
        new_callable=AsyncMock,
        return_value=fake_embedding,
    ) as mock_bedrock:
        result = await emb_mod.embed_text("test query")

    mock_bedrock.assert_called_once()
    assert result == [fake_embedding]


# ---------------------------------------------------------------------------
# Test 2: embed_text calls Voyage when env is set (mock HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_text_calls_voyage_when_env_set(monkeypatch):
    """When EMBEDDING_PROVIDER=voyage and VOYAGE_API_KEY is set, Voyage HTTP is called."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key-abc123")

    import app.services.code_indexing.embedding as emb_mod
    emb_mod._provider_logged = False

    fake_resp_data = _voyage_response(n=1, dim=1024)
    expected_embedding = fake_resp_data["data"][0]["embedding"]

    # Build a mock httpx response
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=fake_resp_data)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await emb_mod.embed_text("def authenticate(user):")

    assert result == [expected_embedding]
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["json"]["model"] == "voyage-code-3"
    assert call_kwargs[1]["json"]["input"] == ["def authenticate(user):"]


@pytest.mark.asyncio
async def test_embed_text_voyage_batch_input(monkeypatch):
    """embed_text with list input should pass all items to Voyage in one call."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key-xyz")

    import app.services.code_indexing.embedding as emb_mod
    emb_mod._provider_logged = False

    texts = ["def foo(): ...", "class Bar: ...", "import os"]
    fake_resp_data = _voyage_response(n=3, dim=1024)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=fake_resp_data)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await emb_mod.embed_text(texts)

    assert len(result) == 3
    assert all(len(v) == 1024 for v in result)
    # Verify correct inputs were sent
    sent_input = mock_client.post.call_args[1]["json"]["input"]
    assert sent_input == texts


# ---------------------------------------------------------------------------
# Test 3: embed_with_voyage raises RuntimeError when VOYAGE_API_KEY missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_with_voyage_raises_when_key_missing(monkeypatch):
    """embed_with_voyage should raise RuntimeError when VOYAGE_API_KEY is not set."""
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from app.services.code_indexing.embedding import embed_with_voyage

    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        await embed_with_voyage("some text")


@pytest.mark.asyncio
async def test_embed_with_voyage_retries_on_transient_error(monkeypatch):
    """embed_with_voyage should retry once on 5xx / timeout errors."""
    monkeypatch.setenv("VOYAGE_API_KEY", "retry-test-key")

    import httpx
    from app.services.code_indexing.embedding import embed_with_voyage

    fake_resp_data = _voyage_response(n=1, dim=1024)
    success_response = MagicMock()
    success_response.raise_for_status = MagicMock()
    success_response.json = MagicMock(return_value=fake_resp_data)

    # First call raises a 503 HTTPStatusError, second succeeds
    error_response = MagicMock()
    error_response.status_code = 503
    http_error = httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=MagicMock(),
        response=error_response,
    )

    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise http_error
        return success_response

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await embed_with_voyage("retry test")

    assert call_count == 2, "Should have retried exactly once"
    assert len(result) == 1
    assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_embed_with_voyage_raises_after_double_failure(monkeypatch):
    """embed_with_voyage should raise RuntimeError if both attempts fail."""
    monkeypatch.setenv("VOYAGE_API_KEY", "fail-test-key")

    import httpx
    from app.services.code_indexing.embedding import embed_with_voyage

    error_response = MagicMock()
    error_response.status_code = 503
    http_error = httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=MagicMock(),
        response=error_response,
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=http_error)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="failed after retry"):
            await embed_with_voyage("fail twice")
