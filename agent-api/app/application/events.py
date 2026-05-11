"""Application-level security and events utilities."""
from typing import Any, Dict, Optional, Callable, List
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """Simple in-memory event bus for application events.

    Provides a light-weight pub/sub mechanism for decoupled
    communication between layers.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}

    def subscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Subscribe a handler to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def publish(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Publish an event to all subscribed handlers."""
        payload = payload or {}
        for handler in self._handlers.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)  # type: ignore
                else:
                    handler(payload)
            except Exception as exc:
                logger.exception("Event handler %s failed for %s", handler.__name__, event_type)


# Global event bus instance (may be replaced by a proper DI container)
event_bus = EventBus()


import asyncio