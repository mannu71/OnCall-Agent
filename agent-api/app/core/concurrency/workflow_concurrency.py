"""Process-wide workflow execution concurrency limit."""
from __future__ import annotations

import asyncio
from typing import Optional

from app.config import settings

_sem: Optional[asyncio.Semaphore] = None


def workflow_semaphore() -> asyncio.Semaphore:
    """Return the global semaphore (lazy-init from ``max_concurrent_workflows``)."""
    global _sem
    if _sem is None:
        limit = max(1, settings.max_concurrent_workflows)
        _sem = asyncio.Semaphore(limit)
    return _sem
