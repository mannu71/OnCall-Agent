"""Model Keys API routes - centralized provider API key management."""
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.repositories.db_repository import db_repository

router = APIRouter(prefix="/model-keys", tags=["model-keys"])
logger = logging.getLogger(__name__)


class ModelKeyCreate(BaseModel):
    provider: str = Field(..., description="Provider name (OpenAI, Anthropic, etc.)")
    api_key: Optional[str] = Field(None, description="API key")
    secret_key: Optional[str] = Field(None, description="Secret key")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    region: Optional[str] = Field(None, description="Region (for AWS)")
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    aws_session_token: Optional[str] = Field(None, description="AWS Session Token")
    description: Optional[str] = Field(None, description="Description")


class ModelKeyUpdate(BaseModel):
    api_key: Optional[str] = Field(None, description="API key")
    secret_key: Optional[str] = Field(None, description="Secret key")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    region: Optional[str] = Field(None, description="Region")
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    aws_session_token: Optional[str] = Field(None, description="AWS Session Token")
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
    if masked.get("aws_access_key_id"):
        masked["aws_access_key_id"] = mask_value(masked["aws_access_key_id"])
    if masked.get("aws_secret_access_key"):
        masked["aws_secret_access_key"] = mask_value(masked["aws_secret_access_key"])
    if masked.get("aws_session_token"):
        masked["aws_session_token"] = mask_value(masked["aws_session_token"])
    return masked


@router.get("", response_model=Dict[str, Any])
async def list_model_keys():
    keys = await db_repository.list_model_keys(include_secrets=True)
    masked = [mask_model_key(k) for k in keys]
    return {"keys": masked}


@router.get("/{provider}", response_model=Dict[str, Any])
async def get_model_key(provider: str):
    key = await db_repository.get_model_key(provider, include_secrets=True)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    return mask_model_key(key)


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_model_key(data: ModelKeyCreate):
    if await db_repository.model_key_exists(data.provider):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Model key for provider '{data.provider}' already exists"
        )
    result = await db_repository.create_model_key(data.model_dump())
    logger.info("Created model key for provider: %s", data.provider)
    return mask_model_key(result)


@router.put("/{provider}", response_model=Dict[str, Any])
async def update_model_key(provider: str, data: ModelKeyUpdate):
    update_data = data.model_dump(exclude_unset=True)
    if not await db_repository.model_key_exists(provider):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    result = await db_repository.update_model_key(provider, update_data)
    logger.info("Updated model key for provider: %s", provider)
    return mask_model_key(result)


@router.post("/upsert", response_model=Dict[str, Any])
async def upsert_model_key(data: ModelKeyCreate):
    result = await db_repository.upsert_model_key(data.provider, data.model_dump())
    logger.info("Upserted model key for provider: %s", data.provider)
    return mask_model_key(result)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_key(provider: str):
    deleted = await db_repository.delete_model_key(provider)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    logger.info("Deleted model key for provider: %s", provider)
    return None
