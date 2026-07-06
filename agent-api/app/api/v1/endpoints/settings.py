"""Settings API endpoints."""
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.api.deps import get_llm_config_repo
from app.core.app_timezone import (
    SETTING_KEY as TZ_SETTING_KEY,
    get_global_timezone_name,
    is_valid_timezone,
    set_cached_timezone,
)
from app.infrastructure.persistence import app_settings_repository

router = APIRouter()


class EmbeddingSettings(BaseModel):
    """Embedding configuration settings."""

    provider: str = Field(description="Embedding provider (bedrock, openai, azure, cohere)")
    model: str = Field(description="Model ID for embeddings")
    region: str = Field(description="AWS region (for Bedrock only)")
    dimensions: int = Field(description="Embedding vector dimensions")


class SettingsResponse(BaseModel):
    """Application settings response."""

    embedding: EmbeddingSettings
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
async def get_settings(llm_repo=Depends(get_llm_config_repo)) -> SettingsResponse:
    """Get current application settings.

    Returns:
        Current settings configuration
    """
    # Try to get embedding config from LLM marked as use_for_embeddings
    embedding_llm = None
    try:
        llm_configs = await llm_repo.list_all()
        for name, config in llm_configs.items():
            if config.get("use_for_embeddings"):
                embedding_llm = config
                break
    except Exception:
        pass

    # Resolve the persisted global timezone (falls back to the cached/default).
    global_tz = await app_settings_repository.get(
        TZ_SETTING_KEY, default=get_global_timezone_name()
    )

    # embedding_provider/model/region are optional config fields; guard with
    # getattr so the endpoint doesn't crash on deployments that don't set them.
    _emb_provider = getattr(settings, 'embedding_provider', 'bedrock')
    _emb_model    = getattr(settings, 'embedding_model', 'amazon.titan-embed-text-v2:0')
    _emb_region   = getattr(settings, 'embedding_region', 'us-east-1')

    # If an LLM is marked for embeddings, use its config
    if embedding_llm:
        return SettingsResponse(
            embedding=EmbeddingSettings(
                provider=embedding_llm.get('provider', _emb_provider),
                model=embedding_llm.get('model', _emb_model),
                region=embedding_llm.get('region', _emb_region),
                dimensions=1024,
            ),
            context_compression_enabled=getattr(settings, 'context_compression_enabled', False),
            context_threshold_percent=getattr(settings, 'context_threshold_percent', 80.0),
            rate_limit_tracking_enabled=getattr(settings, 'rate_limit_tracking_enabled', False),
            agent_recursion_limit=getattr(settings, 'agent_recursion_limit', 12),
            agent_timeout_seconds=getattr(settings, 'agent_timeout_seconds', 180),
            global_timezone=global_tz,
        )

    # Otherwise use default settings
    return SettingsResponse(
        embedding=EmbeddingSettings(
            provider=_emb_provider,
            model=_emb_model,
            region=_emb_region,
            dimensions=1024,
        ),
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


@router.get("/settings/embedding/models")
async def get_embedding_models() -> Dict[str, Any]:
    """Get available embedding models by provider.
    
    Returns:
        Dictionary of providers and their available models
    """
    return {
        "bedrock": [
            {
                "id": "amazon.titan-embed-text-v1",
                "name": "Titan Text Embeddings V1",
                "dimensions": 1536,
                "description": "Amazon Titan text embeddings model"
            },
            {
                "id": "amazon.titan-embed-text-v2:0",
                "name": "Titan Text Embeddings V2",
                "dimensions": 1024,
                "description": "Amazon Titan V2 with improved performance (recommended)"
            },
            {
                "id": "cohere.embed-english-v3",
                "name": "Cohere Embed English V3",
                "dimensions": 1024,
                "description": "Cohere English embeddings via Bedrock"
            },
            {
                "id": "cohere.embed-multilingual-v3",
                "name": "Cohere Embed Multilingual V3",
                "dimensions": 1024,
                "description": "Cohere multilingual embeddings via Bedrock"
            }
        ],
        "openai": [
            {
                "id": "text-embedding-3-small",
                "name": "Text Embedding 3 Small",
                "dimensions": 1536,
                "description": "OpenAI's efficient embedding model"
            },
            {
                "id": "text-embedding-3-large",
                "name": "Text Embedding 3 Large",
                "dimensions": 3072,
                "description": "OpenAI's most capable embedding model"
            },
            {
                "id": "text-embedding-ada-002",
                "name": "Ada 002",
                "dimensions": 1536,
                "description": "Legacy OpenAI embedding model"
            }
        ],
        "azure": [
            {
                "id": "text-embedding-3-small",
                "name": "Text Embedding 3 Small",
                "dimensions": 1536,
                "description": "Azure OpenAI embedding model"
            },
            {
                "id": "text-embedding-3-large",
                "name": "Text Embedding 3 Large",
                "dimensions": 3072,
                "description": "Azure OpenAI large embedding model"
            }
        ],
        "cohere": [
            {
                "id": "embed-english-v3.0",
                "name": "Embed English V3",
                "dimensions": 1024,
                "description": "Cohere English embeddings"
            },
            {
                "id": "embed-multilingual-v3.0",
                "name": "Embed Multilingual V3",
                "dimensions": 1024,
                "description": "Cohere multilingual embeddings"
            }
        ]
    }
