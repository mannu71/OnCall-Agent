"""Postgres-backed LLM prompt cache.

Cache key: sha256(model_id + "\\0" + prompt_text)
This ensures cache entries are isolated per model — no cross-model
collisions even when the same prompt is sent to different models.

Usage::

    from app.core.llm.cache import get_cached, put_cached

    hit = await get_cached(prompt, model_id)
    if hit is None:
        response = await call_provider(prompt)
        await put_cached(prompt, model_id, response, tokens_in, tokens_out)
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _cache_key(model_id: str, prompt: str) -> str:
    """Compute the sha256 cache key for (model_id, prompt)."""
    payload = f"{model_id}\x00{prompt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def get_cached(prompt: str, model_id: str) -> Optional[str]:
    """Return the cached response string, or None on cache miss.

    Bumps ``hits`` and ``last_hit_at`` on a hit.
    """
    from app.core.database import AsyncSessionLocal

    key = _cache_key(model_id, prompt)
    try:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("SELECT response FROM llm_cache WHERE prompt_sha256 = :key"),
                {"key": key},
            )
            result = row.fetchone()
            if result is None:
                return None

            try:
                await session.execute(
                    text("""
                        UPDATE llm_cache
                           SET hits = hits + 1, last_hit_at = NOW()
                         WHERE prompt_sha256 = :key
                    """),
                    {"key": key},
                )
                await session.commit()
            except Exception as update_exc:
                logger.debug("llm_cache hit-counter update failed: %s", update_exc)

            return result[0]
    except Exception as exc:
        logger.warning("llm_cache get failed: %s", exc)
        return None


async def put_cached(
    prompt: str,
    model_id: str,
    response: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Insert or update a cache entry. Silently ignores errors."""
    from app.core.database import AsyncSessionLocal

    key = _cache_key(model_id, prompt)
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO llm_cache
                        (prompt_sha256, model_id, response, tokens_in, tokens_out)
                    VALUES
                        (:key, :model_id, :response, :tokens_in, :tokens_out)
                    ON CONFLICT (prompt_sha256) DO UPDATE
                        SET response    = EXCLUDED.response,
                            tokens_in   = EXCLUDED.tokens_in,
                            tokens_out  = EXCLUDED.tokens_out,
                            last_hit_at = NOW()
                """),
                {
                    "key": key,
                    "model_id": model_id,
                    "response": response,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                },
            )
            await session.commit()
    except Exception as exc:
        logger.warning("llm_cache put failed: %s", exc)


async def cache_stats() -> dict:
    """Return a summary dict describing the prompt cache."""
    from app.core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("""
                    SELECT
                        COUNT(*)                AS total_rows,
                        COALESCE(SUM(hits), 0)  AS total_hits,
                        COALESCE(AVG(hits), 0)  AS avg_hits,
                        COUNT(DISTINCT model_id) AS model_count
                    FROM llm_cache
                """)
            )
            r = row.fetchone()
            return {
                "rows": r[0],
                "total_hits": r[1],
                "avg_hits_per_entry": round(float(r[2]), 2),
                "model_count": r[3],
            }
    except Exception as exc:
        logger.warning("cache_stats failed: %s", exc)
        return {"error": str(exc)}
