"""In-process token-bucket rate limiter for CloudWatch API calls.

Motivation
----------
A wide investigation can fan out many ``StartQuery`` / ``FilterLogEvents``
calls at once (top-N drill-downs × log groups × regions). CloudWatch enforces
per-account request-rate limits; tripping them throttles the *whole* run. This
limiter smooths bursts to a configurable steady rate so the limiter — not AWS —
absorbs the spike.

Design
------
- One token bucket per ``(region, api)`` key, created lazily.
- ``acquire`` refills tokens based on elapsed wall-clock time, then waits until a
  token is available. Generous defaults (e.g. 5 StartQuery/s) mean it only bites
  under heavy fan-out.
- Process-local (same scope as ``cloudwatch_cache``). If agent-api ever runs
  multiple replicas this would need a shared store — see the Phase 5 notes.
- A ``time_fn`` injection point keeps it deterministically unit-testable with a
  fake clock (no real sleeping in tests).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, Tuple

logger = logging.getLogger(__name__)


class TokenBucket:
    """A single async token bucket. Capacity == rps (1 second of burst)."""

    def __init__(self, rps: float, time_fn: Callable[[], float] = time.monotonic):
        self.rps = max(0.001, float(rps))
        self.capacity = self.rps
        self.tokens = self.rps
        self._time = time_fn
        self._updated = time_fn()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._time()
        elapsed = max(0.0, now - self._updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rps)
        self._updated = now

    async def acquire(self) -> float:
        """Consume one token, waiting if necessary. Returns the seconds waited."""
        async with self._lock:
            self._refill()
            waited = 0.0
            if self.tokens < 1.0:
                deficit = 1.0 - self.tokens
                wait = deficit / self.rps
                waited = wait
                await asyncio.sleep(wait)
                self._refill()
            self.tokens = max(0.0, self.tokens - 1.0)
            return waited


_buckets: Dict[Tuple[str, str], TokenBucket] = {}


def _bucket_for(region: str, api: str, rps: float,
                time_fn: Callable[[], float] = time.monotonic) -> TokenBucket:
    key = (region or "default", api or "default")
    bucket = _buckets.get(key)
    if bucket is None:
        bucket = TokenBucket(rps, time_fn=time_fn)
        _buckets[key] = bucket
    return bucket


async def acquire(region: str, api: str, rps: float) -> float:
    """Rate-limit one call to *api* in *region* to *rps* requests/second.

    Returns the seconds spent waiting (0.0 when a token was immediately
    available). When ``rps <= 0`` the limiter is disabled and returns 0.0.
    """
    if rps is None or rps <= 0:
        return 0.0
    bucket = _bucket_for(region, api, rps)
    return await bucket.acquire()


def reset() -> None:
    """Drop all buckets (test isolation)."""
    _buckets.clear()
