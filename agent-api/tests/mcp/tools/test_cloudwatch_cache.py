"""Unit tests for app.core.cloudwatch_cache.

Tests TTL expiry, thundering-herd prevention, and invalidation.
No AWS credentials needed.
"""
import asyncio
import time

import pytest

# Reset module-level state before each test to ensure isolation.
import app.core.cloudwatch_cache as _cache_module


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the in-process cache store between tests."""
    _cache_module._store.clear()
    _cache_module._locks.clear()
    _cache_module._fn_index.clear()
    _cache_module._meta_lock = None
    yield
    _cache_module._store.clear()
    _cache_module._locks.clear()
    _cache_module._fn_index.clear()
    _cache_module._meta_lock = None


from app.core.cloudwatch_cache import cached_call, invalidate


# ---------------------------------------------------------------------------
# Basic hit / miss
# ---------------------------------------------------------------------------

class TestCachedCall:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_fn(self):
        call_count = 0

        async def _fn(**kw):
            nonlocal call_count
            call_count += 1
            return {"result": "data"}

        result = await cached_call("test_fn", _fn, ttl_seconds=60, key="v1")
        assert result == {"result": "data"}
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_cache_hit_does_not_call_fn_again(self):
        call_count = 0

        async def _fn(**kw):
            nonlocal call_count
            call_count += 1
            return {"result": "data"}

        await cached_call("test_fn", _fn, ttl_seconds=60, key="v1")
        await cached_call("test_fn", _fn, ttl_seconds=60, key="v1")
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_kwargs_different_keys(self):
        call_count = 0

        async def _fn(**kw):
            nonlocal call_count
            call_count += 1
            return {"result": kw}

        await cached_call("test_fn", _fn, ttl_seconds=60, group="a")
        await cached_call("test_fn", _fn, ttl_seconds=60, group="b")
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_ttl_zero_bypasses_cache(self):
        call_count = 0

        async def _fn(**kw):
            nonlocal call_count
            call_count += 1
            return {}

        await cached_call("test_fn", _fn, ttl_seconds=0, key="v1")
        await cached_call("test_fn", _fn, ttl_seconds=0, key="v1")
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_expired_entry_calls_fn_again(self, monkeypatch):
        """An entry past its TTL should trigger a fresh API call."""
        call_count = 0

        async def _fn(**kw):
            nonlocal call_count
            call_count += 1
            return {"v": call_count}

        # First call — populates cache.
        await cached_call("test_fn", _fn, ttl_seconds=1, key="v1")
        assert call_count == 1

        # Manually expire the entry by back-dating its expiry.
        for k in list(_cache_module._store.keys()):
            result_val, _ = _cache_module._store[k]
            _cache_module._store[k] = (result_val, time.monotonic() - 5)  # already expired

        await cached_call("test_fn", _fn, ttl_seconds=1, key="v1")
        assert call_count == 2


# ---------------------------------------------------------------------------
# Thundering-herd prevention
# ---------------------------------------------------------------------------

class TestThunderingHerd:
    @pytest.mark.asyncio
    async def test_concurrent_misses_call_fn_once(self):
        """Two coroutines missing the cache simultaneously should call fn only once."""
        call_count = 0

        async def _slow_fn(**kw):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simulate AWS latency
            return {"data": "ok"}

        results = await asyncio.gather(
            cached_call("slow_fn", _slow_fn, ttl_seconds=60, key="same"),
            cached_call("slow_fn", _slow_fn, ttl_seconds=60, key="same"),
        )

        assert call_count == 1
        assert results[0] == results[1] == {"data": "ok"}


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------

class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_all(self):
        async def _fn(**kw):
            return {}

        await cached_call("fn_a", _fn, ttl_seconds=60, k="1")
        await cached_call("fn_b", _fn, ttl_seconds=60, k="2")
        assert len(_cache_module._store) == 2

        removed = invalidate()
        assert removed == 2
        assert len(_cache_module._store) == 0

    @pytest.mark.asyncio
    async def test_invalidate_by_fn_name(self):
        async def _fn(**kw):
            return {}

        await cached_call("fn_target", _fn, ttl_seconds=60, k="1")
        await cached_call("fn_keep", _fn, ttl_seconds=60, k="2")
        assert len(_cache_module._store) == 2

        removed = invalidate("fn_target")
        assert removed == 1
        # fn_keep entry should still be present.
        assert len(_cache_module._store) == 1
