"""Process-local backoff tracker for throttled LLM targets.

Motivation
----------
When Bedrock throttles a given (model, region, credential), blindly retrying the
*same* target wastes a full-context resend and usually throttles again. Phase 1
routing fails over to an alternate target instead — but it also needs to *remember*
which targets are currently cooling down so the fallback-chain resolver can skip
(or de-prioritise) them on the next call, not just the current one.

Design
------
- A target is the tuple ``(provider, region, model_id, key_id)``. Any field may be
  empty; ``key_id`` defaults to ``"default"`` (single-credential setups).
- ``mark_throttled`` records a ``cooldown_until`` wall-clock deadline computed from
  the same decorrelated-jitter curve as ``app.core.resilience.retry`` (base 1s, cap 30s), so a
  target that keeps throttling backs off progressively.
- ``is_cooled`` / ``cooldown_remaining`` are cheap synchronous reads — they are
  called from the (sync) fallback-chain resolver, so they must not require an event
  loop. A ``threading.Lock`` guards the dict.
- Process-local, same scope as ``cloudwatch_ratelimit`` / ``cloudwatch_cache``. If
  agent-api ever runs multiple replicas this would need a shared store.
- A ``time_fn`` injection point keeps cooldown math deterministically unit-testable
  with a fake clock (no real sleeping in tests).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from app.core.resilience.retry import jittered_backoff


@dataclass(frozen=True)
class ThrottleTarget:
    """Identifies one routable LLM endpoint for backoff bookkeeping."""

    provider: str = ""
    region: str = ""
    model_id: str = ""
    key_id: str = "default"

    def as_key(self) -> Tuple[str, str, str, str]:
        return (
            (self.provider or "").lower(),
            (self.region or "").lower(),
            (self.model_id or "").lower(),
            (self.key_id or "default"),
        )


class ModelThrottleTracker:
    """Records and queries per-target cooldown deadlines (process-local)."""

    def __init__(self, time_fn: Callable[[], float] = time.monotonic):
        self._time = time_fn
        self._lock = threading.Lock()
        # key -> (cooldown_until, consecutive_throttles)
        self._state: Dict[Tuple[str, str, str, str], Tuple[float, int]] = {}

    def mark_throttled(self, target: ThrottleTarget) -> float:
        """Record a throttle for *target* and return the cooldown seconds applied.

        Consecutive throttles of the same target widen the cooldown along the
        decorrelated-jitter curve (capped at 30s), so a persistently throttled
        target is avoided for progressively longer.
        """
        key = target.as_key()
        now = self._time()
        with self._lock:
            _until, count = self._state.get(key, (0.0, 0))
            count += 1
            cooldown = jittered_backoff(attempt=count)
            self._state[key] = (now + cooldown, count)
        return cooldown

    def is_cooled(self, target: ThrottleTarget) -> bool:
        """True when *target* is currently in a cooldown window."""
        return self.cooldown_remaining(target) > 0.0

    def cooldown_remaining(self, target: ThrottleTarget) -> float:
        """Seconds until *target* leaves cooldown (0.0 when ready)."""
        key = target.as_key()
        now = self._time()
        with self._lock:
            entry = self._state.get(key)
            if entry is None:
                return 0.0
            until, count = entry
            remaining = until - now
            if remaining <= 0.0:
                # Cooldown elapsed; clear so a future throttle starts fresh.
                del self._state[key]
                return 0.0
            return remaining

    def clear(self, target: Optional[ThrottleTarget] = None) -> None:
        """Drop cooldown state for one target, or all targets when omitted."""
        with self._lock:
            if target is None:
                self._state.clear()
            else:
                self._state.pop(target.as_key(), None)


# Module-level singleton — mirrors the cloudwatch_ratelimit pattern.
_tracker = ModelThrottleTracker()


def mark_throttled(target: ThrottleTarget) -> float:
    return _tracker.mark_throttled(target)


def is_cooled(target: ThrottleTarget) -> bool:
    return _tracker.is_cooled(target)


def cooldown_remaining(target: ThrottleTarget) -> float:
    return _tracker.cooldown_remaining(target)


def reset() -> None:
    """Drop all cooldown state (test isolation)."""
    _tracker.clear()


def _set_time_fn(time_fn: Callable[[], float]) -> None:
    """Swap the clock (unit tests only)."""
    _tracker._time = time_fn  # noqa: SLF001 - deliberate test seam
