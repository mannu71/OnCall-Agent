"""Global application timezone — single read point.

The global timezone is an operator-editable app setting (``app_settings`` table,
key ``global_timezone``) configured from the Settings page. It governs:

  * scheduling — converting a Schedule node's local HH:MM into a UTC cron, and
    the APScheduler default timezone;
  * timestamp display — the UI localizes UTC timestamps using the same value
    (the UI reads it from ``GET /settings``).

CloudWatch *query windows* must stay UTC — epoch math is timezone-independent —
so this module is intentionally NOT used in the AWS query path.

Reads must be cheap and synchronous (the scheduler reads it outside an event
loop), so the resolved name is cached in a module global. ``refresh_*`` reloads
it from the database (call at startup and after a settings write).
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

logger = logging.getLogger(__name__)

SETTING_KEY = "global_timezone"

# Cache starts from the env-configured scheduler timezone so reads work before
# the DB-backed value has been loaded.
_cached_tz_name: str = settings.scheduler_timezone or "UTC"


def is_valid_timezone(name: str) -> bool:
    """Return True if *name* is a resolvable IANA timezone."""
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def get_global_timezone_name() -> str:
    """Return the cached global timezone name (sync, cheap)."""
    return _cached_tz_name


def get_global_timezone() -> ZoneInfo:
    """Return the cached global timezone as a ``ZoneInfo`` (falls back to UTC)."""
    try:
        return ZoneInfo(_cached_tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def set_cached_timezone(name: str) -> None:
    """Update the in-process cache (call after a validated settings write)."""
    global _cached_tz_name
    if is_valid_timezone(name):
        _cached_tz_name = name
        logger.info("Global timezone cache updated to %s", name)
    else:
        logger.warning("Ignoring invalid global timezone %r; keeping %s", name, _cached_tz_name)


async def refresh_global_timezone() -> str:
    """Reload the global timezone from the database into the cache.

    Best-effort: any failure leaves the existing cache intact. Returns the
    effective (cached) timezone name.
    """
    try:
        from app.infrastructure.persistence import app_settings_repository

        value = await app_settings_repository.get(SETTING_KEY)
        if value:
            set_cached_timezone(value)
    except Exception as exc:  # pragma: no cover - DB may be unavailable at boot
        logger.warning("Could not load global timezone from DB (%s); using %s", exc, _cached_tz_name)
    return _cached_tz_name
