"""Body-handle registry shared across crawler flows.

A body handle is an opaque string (``fn:<12-hex-chars>``) that encodes a
repo + file + line range.  It is stable for the lifetime of the process and
is passed from crawler_find_symbol → crawler_get_body so the agent never has
to construct raw file paths itself.

The in-memory dict is capped at 1 024 entries with FIFO eviction.  Handles
that fall out of the cache simply trigger a ``"body_handle expired"`` error in
the getBodyFlow; callers can re-run findSymbolFlow to obtain a fresh handle.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Module-level cache  (FIFO-eviction, cap = 1 024 entries)
# ---------------------------------------------------------------------------

_BODY_HANDLE_CACHE: Dict[str, Dict[str, Any]] = {}
_BODY_HANDLE_MAX: int = 1024


def make_body_handle(repo: str, file: str, line_start: int, line_end: int) -> str:
    """Create an opaque body-fetch handle from repo + file + line range."""
    key = f"{repo}:{file}:{line_start}:{line_end}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    handle = f"fn:{digest}"

    if handle not in _BODY_HANDLE_CACHE:
        if len(_BODY_HANDLE_CACHE) >= _BODY_HANDLE_MAX:
            oldest = next(iter(_BODY_HANDLE_CACHE))
            del _BODY_HANDLE_CACHE[oldest]
        _BODY_HANDLE_CACHE[handle] = {
            "repo": repo,
            "file": file,
            "line_start": line_start,
            "line_end": line_end,
        }
    return handle


def resolve_body_handle(handle: str) -> Optional[Dict[str, Any]]:
    """Resolve a body handle to its stored metadata, or None if expired/unknown."""
    return _BODY_HANDLE_CACHE.get(handle)


# ---------------------------------------------------------------------------
# Private aliases kept for any code that still uses the underscore names
# (transitional — remove once all callers are updated)
# ---------------------------------------------------------------------------
_make_body_handle = make_body_handle
_resolve_body_handle = resolve_body_handle
