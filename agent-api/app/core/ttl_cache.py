"""Tiny in-process TTL cache — no external dependency.

Used for bounded catalogs (DB table lists, code repo maps) that are expensive
to recompute but change rarely, so the agent can do a cheap lookup instead of
dumping a whole schema / codebase into context on every run. Per-process only
(not distributed); values are cached by an explicit key and expire after a TTL.
Callers that key by content hash (e.g. a repo's ``files_sha256``) also get
automatic invalidation when the underlying content changes.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class TTLCache:
    def __init__(self, ttl_seconds: float = 300.0, maxsize: int = 256) -> None:
        self._ttl = float(ttl_seconds)
        self._maxsize = int(maxsize)
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < now:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                # Evict the entry closest to expiry to bound memory.
                oldest = min(self._store.items(), key=lambda kv: kv[1][0], default=None)
                if oldest is not None:
                    self._store.pop(oldest[0], None)
            self._store[key] = (now + self._ttl, value)

    def clear(self, prefix: Optional[str] = None) -> None:
        """Drop all entries, or only those whose key starts with *prefix*."""
        with self._lock:
            if prefix is None:
                self._store.clear()
            else:
                for k in [k for k in self._store if k.startswith(prefix)]:
                    self._store.pop(k, None)
