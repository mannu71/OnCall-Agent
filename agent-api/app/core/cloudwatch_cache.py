"""In-process TTL cache for CloudWatch API results.

Motivation
----------
CloudWatch API calls are charged per request and can take 1-5 seconds each.
During high-frequency on-call checks the same queries are often repeated
within a short window.  This module provides a lightweight, async-safe cache
that avoids redundant API calls within a configurable TTL.

Design
------
- Dict[str, (result, expiry_epoch)] keyed on a SHA-256 hash of the
  call arguments (excluding credentials, which change per-execution but
  are stable within a session).
- Each cache slot has its own asyncio.Lock to prevent a thundering-herd:
  when two coroutines miss the cache simultaneously only one fires the
  actual AWS call; the other waits and reuses the result.
- The store is per-process; it is cleared on restart and can be
  invalidated programmatically (e.g. after a configuration change).
- Thread-safe within a single asyncio event loop.

Usage
-----
    from app.core.cloudwatch_cache import cached_call

    result = await cached_call(
        "watch_log_groups",
        my_async_fn,
        ttl_seconds=120,
        log_group_names=sorted(groups),
        time_range_minutes=60,
        region="us-east-1",
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal store
# ---------------------------------------------------------------------------

# cache_store: key -> (result, expiry_monotonic)
_store: Dict[str, Tuple[Any, float]] = {}

# Per-slot asyncio locks — created lazily, guarded by _meta_lock.
_locks: Dict[str, asyncio.Lock] = {}

# Protects mutation of _locks itself.
_meta_lock: Optional[asyncio.Lock] = None


def _get_meta_lock() -> asyncio.Lock:
    """Return (or lazily create) the meta-lock for the running event loop."""
    global _meta_lock
    if _meta_lock is None:
        _meta_lock = asyncio.Lock()
    return _meta_lock


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _make_key(fn_name: str, **kwargs: Any) -> str:
    """Compute a stable SHA-256 cache key from the function name and kwargs."""
    payload = json.dumps({"fn": fn_name, **kwargs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def cached_call(
    fn_name: str,
    fn: Callable[..., Any],
    ttl_seconds: int = 120,
    **kwargs: Any,
) -> Any:
    """Call *fn*(**kwargs) and cache the result for *ttl_seconds* seconds.

    On a cache hit the stored result is returned immediately without calling
    *fn*.  On a cache miss only one concurrent coroutine actually calls *fn*;
    all others wait for that result (thundering-herd prevention).

    Args:
        fn_name: Logical name used as part of the cache key (e.g.
            ``"watch_log_groups"``).
        fn: Async callable to invoke on a cache miss.
        ttl_seconds: Seconds to cache the result (default 120).  Pass 0 to
            bypass the cache entirely (always call *fn*).
        **kwargs: Arguments forwarded to *fn* and included in the cache key.
            Exclude secrets / credentials from kwargs so they never affect
            the key (bind them via closure inside *fn* instead).

    Returns:
        The result of *fn*(**kwargs), from cache or freshly computed.
    """
    if ttl_seconds <= 0:
        return await fn(**kwargs)

    key = _make_key(fn_name, **kwargs)

    # Lazily create a per-slot lock.
    async with _get_meta_lock():
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        slot_lock = _locks[key]

    async with slot_lock:
        entry = _store.get(key)
        if entry is not None:
            result, expiry = entry
            remaining = expiry - time.monotonic()
            if remaining > 0:
                logger.debug(
                    "cloudwatch_cache: HIT %s (%.0fs remaining)", fn_name, remaining
                )
                return result

        # Cache miss — call the underlying function.
        logger.debug("cloudwatch_cache: MISS %s — calling AWS API", fn_name)
        result = await fn(**kwargs)
        _store[key] = (result, time.monotonic() + ttl_seconds)
        logger.debug("cloudwatch_cache: SET %s ttl=%ds", fn_name, ttl_seconds)
        return result


def invalidate(fn_name: Optional[str] = None) -> int:
    """Invalidate cache entries.

    Args:
        fn_name: When supplied, only entries whose key was computed from this
            function name are removed.  When ``None`` the entire cache is
            cleared.

    Returns:
        Number of entries removed.
    """
    if fn_name is None:
        count = len(_store)
        _store.clear()
        logger.info("cloudwatch_cache: cleared all %d entries", count)
        return count

    # Recompute would require storing the fn_name separately; instead we keep
    # a secondary index by checking the embedded fn name in the payload.
    # We store it as a hashed key, so we need a reverse lookup.  We do this
    # by maintaining a fn_name -> [keys] index lazily.
    to_delete = [k for k, _ in _store.items() if k in _fn_index.get(fn_name, set())]
    for k in to_delete:
        del _store[k]
        _fn_index[fn_name].discard(k)
    logger.info("cloudwatch_cache: invalidated %d entries for '%s'", len(to_delete), fn_name)
    return len(to_delete)


# Secondary index: fn_name -> set of cache keys.  Populated in cached_call.
_fn_index: Dict[str, set] = {}

_SWEEP_COUNTER = 0
_SWEEP_EVERY = 50


def _sweep_expired() -> int:
    """Remove expired store entries and orphaned lock/index metadata."""
    now = time.monotonic()
    expired_keys = [k for k, (_, exp) in _store.items() if exp <= now]
    for k in expired_keys:
        _store.pop(k, None)
        _locks.pop(k, None)
    for fn_name, keys in list(_fn_index.items()):
        keys -= set(expired_keys)
        if not keys:
            _fn_index.pop(fn_name, None)
    return len(expired_keys)


async def cached_call(  # noqa: F811  — intentional redefinition with index tracking
    fn_name: str,
    fn: Callable[..., Any],
    ttl_seconds: int = 120,
    **kwargs: Any,
) -> Any:
    """Call *fn*(**kwargs) and cache the result for *ttl_seconds* seconds.

    This version also maintains a secondary fn_name→keys index so that
    :func:`invalidate` can target entries by function name efficiently.
    """
    if ttl_seconds <= 0:
        return await fn(**kwargs)

    key = _make_key(fn_name, **kwargs)

    async with _get_meta_lock():
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        slot_lock = _locks[key]
        if fn_name not in _fn_index:
            _fn_index[fn_name] = set()
        _fn_index[fn_name].add(key)

    async with slot_lock:
        entry = _store.get(key)
        if entry is not None:
            result, expiry = entry
            remaining = expiry - time.monotonic()
            if remaining > 0:
                logger.debug(
                    "cloudwatch_cache: HIT %s (%.0fs remaining)", fn_name, remaining
                )
                return result

        logger.debug("cloudwatch_cache: MISS %s — calling AWS API", fn_name)
        global _SWEEP_COUNTER
        _SWEEP_COUNTER += 1
        if _SWEEP_COUNTER % _SWEEP_EVERY == 0:
            removed = _sweep_expired()
            if removed:
                logger.debug("cloudwatch_cache: swept %d expired entries", removed)
        result = await fn(**kwargs)
        _store[key] = (result, time.monotonic() + ttl_seconds)
        logger.debug("cloudwatch_cache: SET %s ttl=%ds", fn_name, ttl_seconds)
        return result
