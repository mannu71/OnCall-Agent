"""Model Keys API routes - centralized provider API key management."""
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import AliasChoices, BaseModel, Field

from app.infrastructure.persistence import model_key_repository

router = APIRouter(prefix="/model-keys", tags=["model-keys"])
logger = logging.getLogger(__name__)


class ModelKeyCreate(BaseModel):
    provider: str = Field(..., description="Provider name (OpenAI, Anthropic, etc.)")
    api_key: Optional[str] = Field(None, description="API key")
    secret_key: Optional[str] = Field(None, description="Secret key")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    region: Optional[str] = Field(None, description="Region (for AWS)")
    access_key_id: Optional[str] = Field(
        None,
        description="AWS Access Key ID",
        validation_alias=AliasChoices("access_key_id", "aws_access_key_id"),
    )
    secret_access_key: Optional[str] = Field(
        None,
        description="AWS Secret Access Key",
        validation_alias=AliasChoices("secret_access_key", "aws_secret_access_key"),
    )
    session_token: Optional[str] = Field(
        None,
        description="AWS Session Token",
        validation_alias=AliasChoices("session_token", "aws_session_token"),
    )
    description: Optional[str] = Field(None, description="Description")


class ModelKeyUpdate(BaseModel):
    api_key: Optional[str] = Field(None, description="API key")
    secret_key: Optional[str] = Field(None, description="Secret key")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    region: Optional[str] = Field(None, description="Region")
    access_key_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("access_key_id", "aws_access_key_id"),
    )
    secret_access_key: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("secret_access_key", "aws_secret_access_key"),
    )
    session_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("session_token", "aws_session_token"),
    )
    description: Optional[str] = Field(None, description="Description")


def mask_value(val: Optional[str]) -> Optional[str]:
    if not val or len(val) < 8:
        return "********" if val else None
    return f"{val[:4]}...{val[-4:]}"


def mask_model_key(data: Dict[str, Any]) -> Dict[str, Any]:
    masked = {**data}
    if masked.get("api_key"):
        masked["api_key"] = mask_value(masked["api_key"])
    if masked.get("secret_key"):
        masked["secret_key"] = mask_value(masked["secret_key"])
    for field in ("access_key_id", "secret_access_key", "session_token"):
        if masked.get(field):
            masked[field] = mask_value(masked[field])
    if masked.get("aws_access_key_id"):
        masked["aws_access_key_id"] = mask_value(masked["aws_access_key_id"])
    if masked.get("aws_secret_access_key"):
        masked["aws_secret_access_key"] = mask_value(masked["aws_secret_access_key"])
    if masked.get("aws_session_token"):
        masked["aws_session_token"] = mask_value(masked["aws_session_token"])
    return masked


@router.get("", response_model=Dict[str, Any])
async def list_model_keys():
    keys = await model_key_repository.list_all(include_secrets=True)
    masked = [mask_model_key(k) for k in keys]
    return {"keys": masked}


@router.get("/{provider}", response_model=Dict[str, Any])
async def get_model_key(provider: str):
    key = await model_key_repository.get_by_provider(provider, include_secrets=True)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    return mask_model_key(key)


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_model_key(data: ModelKeyCreate):
    if await model_key_repository.exists(data.provider):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Model key for provider '{data.provider}' already exists"
        )
    payload = data.model_dump(exclude_unset=True, exclude_none=True)
    result = await model_key_repository.create(payload)
    logger.info("Created model key for provider: %s", data.provider)
    return mask_model_key(result)


@router.put("/{provider}", response_model=Dict[str, Any])
async def update_model_key(provider: str, data: ModelKeyUpdate):
    update_data = data.model_dump(exclude_unset=True, exclude_none=True)
    if not await model_key_repository.exists(provider):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    result = await model_key_repository.update(provider, update_data)
    logger.info("Updated model key for provider: %s", provider)
    return mask_model_key(result)


@router.post("/upsert", response_model=Dict[str, Any])
async def upsert_model_key(data: ModelKeyCreate):
    payload = data.model_dump(exclude_unset=True, exclude_none=True)
    result = await model_key_repository.upsert(payload["provider"], payload)
    logger.info("Upserted model key for provider: %s", data.provider)
    return mask_model_key(result)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_key(provider: str):
    deleted = await model_key_repository.delete(provider)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    logger.info("Deleted model key for provider: %s", provider)
    return None
