"""Settings API endpoints."""
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core.runtime.app_timezone import (
    SETTING_KEY as TZ_SETTING_KEY,
    get_global_timezone_name,
    is_valid_timezone,
    set_cached_timezone,
)
from app.infrastructure.persistence import app_settings_repository

router = APIRouter()


class SettingsResponse(BaseModel):
    """Application settings response."""

    context_compression_enabled: bool
    context_threshold_percent: float
    rate_limit_tracking_enabled: bool
    agent_recursion_limit: int
    agent_timeout_seconds: int
    global_timezone: str = Field(
        default="UTC",
        description="Application-wide IANA timezone for scheduling and timestamp display.",
    )


class GeneralSettingsUpdate(BaseModel):
    """Writable general settings."""

    global_timezone: str = Field(
        description="IANA timezone name, e.g. 'UTC' or 'Asia/Singapore'.",
    )


@router.get("/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    """Get current application settings."""
    # Resolve the persisted global timezone (falls back to the cached/default).
    global_tz = await app_settings_repository.get(
        TZ_SETTING_KEY, default=get_global_timezone_name()
    )

    return SettingsResponse(
        context_compression_enabled=getattr(settings, 'context_compression_enabled', False),
        context_threshold_percent=getattr(settings, 'context_threshold_percent', 80.0),
        rate_limit_tracking_enabled=getattr(settings, 'rate_limit_tracking_enabled', False),
        agent_recursion_limit=getattr(settings, 'agent_recursion_limit', 12),
        agent_timeout_seconds=getattr(settings, 'agent_timeout_seconds', 180),
        global_timezone=global_tz,
    )


@router.put("/settings/general")
async def update_general_settings(payload: GeneralSettingsUpdate) -> Dict[str, Any]:
    """Persist editable general settings (currently the global timezone).

    Validates the IANA timezone, stores it in ``app_settings``, and refreshes
    the in-process cache so scheduling picks it up without a restart.
    """
    tz = (payload.global_timezone or "").strip()
    if not is_valid_timezone(tz):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid IANA timezone: {payload.global_timezone!r}",
        )
    await app_settings_repository.set(TZ_SETTING_KEY, tz)
    set_cached_timezone(tz)
    return {"success": True, "global_timezone": tz}


class FeatureFlagsUpdate(BaseModel):
    """Batch of feature-flag overrides. A null value resets to the env default."""

    updates: Dict[str, Any] = Field(
        description="Map of flag key → new value (bool/int/str), or null to reset.",
    )


@router.get("/settings/features")
async def get_feature_flags() -> Dict[str, Any]:
    """Return the runtime feature-flag catalog with current + default values."""
    from app.core import feature_flags

    return {"flags": feature_flags.effective()}


@router.put("/settings/features")
async def update_feature_flags(payload: FeatureFlagsUpdate) -> Dict[str, Any]:
    """Persist and live-apply feature-flag overrides (no restart required)."""
    from app.core import feature_flags

    try:
        flags = await feature_flags.set_flags(payload.updates)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"success": True, "flags": flags}
