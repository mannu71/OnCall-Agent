"""Lightweight in-memory event bus.

Moved from ``app.application.events`` during the DDD-scaffolding collapse
(plan §1.2, option B). The misplaced ``import asyncio`` at the bottom of
the original file is fixed here so ``EventBus.publish`` no longer raises
``NameError`` the first time it is called.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventBus:
    """Simple in-memory event bus for application events.

    Provides a lightweight pub/sub mechanism for decoupled communication
    between layers. Synchronous and async handlers are both supported.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}

    def subscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Subscribe a handler to an event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(
        self, event_type: str, payload: Optional[Dict[str, Any]] = None
    ) -> None:
        """Publish an event to all subscribed handlers."""
        payload = payload or {}
        for handler in self._handlers.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)
            except Exception:
                logger.exception(
                    "Event handler %s failed for %s",
                    getattr(handler, "__name__", handler),
                    event_type,
                )


# Module-level singleton — may be replaced with a proper DI container later.
event_bus = EventBus()
