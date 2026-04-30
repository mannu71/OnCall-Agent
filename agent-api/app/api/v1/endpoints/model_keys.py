"""Model Keys API routes - centralized provider API key management."""
import logging
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.repositories.db_repository import db_repository
from app.services.provider_schema_registry import (
    ProviderSchemaRegistry,
    ProviderSchema,
    CredentialFieldSchema
)
from app.services.credential_validator import (
    CredentialValidator,
    CredentialValidationError
)
from app.services.credential_transformer import CredentialTransformer

router = APIRouter(prefix="/model-keys", tags=["model-keys"])
logger = logging.getLogger(__name__)

# Create singleton instances
schema_registry = ProviderSchemaRegistry()
credential_validator = CredentialValidator(schema_registry)


class ModelKeyCreate(BaseModel):
    """Model for creating/updating provider credentials.
    
    Accepts all possible credential fields for all providers.
    The validator will check which fields are valid for each specific provider.
    """
    provider: str = Field(..., description="Provider name (openai, anthropic, bedrock, etc.)")
    # Common fields
    api_key: Optional[str] = Field(None, description="API key for authentication")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    region: Optional[str] = Field(None, description="Region (for AWS services)")
    description: Optional[str] = Field(None, description="Optional description")
    # AWS IAM credentials (support both aws_* and non-prefixed for backward compatibility)
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID (legacy field name)")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key (legacy field name)")
    aws_session_token: Optional[str] = Field(None, description="AWS Session Token (legacy field name)")
    access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    session_token: Optional[str] = Field(None, description="AWS Session Token")
    # Custom provider fields
    secret_key: Optional[str] = Field(None, description="Secret key (for custom providers)")


class ModelKeyUpdate(BaseModel):
    """Model for updating provider credentials.
    
    All fields are optional for updates.
    """
    api_key: Optional[str] = Field(None, description="API key for authentication")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    region: Optional[str] = Field(None, description="Region (for AWS services)")
    description: Optional[str] = Field(None, description="Optional description")
    # AWS IAM credentials (support both aws_* and non-prefixed for backward compatibility)
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID (legacy field name)")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key (legacy field name)")
    aws_session_token: Optional[str] = Field(None, description="AWS Session Token (legacy field name)")
    access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    session_token: Optional[str] = Field(None, description="AWS Session Token")
    # Custom provider fields
    secret_key: Optional[str] = Field(None, description="Secret key (for custom providers)")


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
    # Also mask non-prefixed AWS fields
    if masked.get("access_key_id"):
        masked["access_key_id"] = mask_value(masked["access_key_id"])
    if masked.get("secret_access_key"):
        masked["secret_access_key"] = mask_value(masked["secret_access_key"])
    if masked.get("session_token"):
        masked["session_token"] = mask_value(masked["session_token"])
    return masked


def get_configured_fields(data: Dict[str, Any]) -> List[str]:
    """Get list of configured (non-null, non-empty) credential fields."""
    configured = []
    credential_fields = [
        "api_key", "secret_key", "access_key_id", "secret_access_key",
        "session_token", "region", "endpoint"
    ]
    for field in credential_fields:
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or value.strip()):
            configured.append(field)
    return configured


@router.get("", response_model=Dict[str, Any])
async def list_model_keys():
    keys = await db_repository.list_model_keys(include_secrets=True)
    
    # Enhance each key with schema and configured_fields
    enhanced_keys = []
    for key in keys:
        masked_key = mask_model_key(key)
        
        # Fetch provider schema
        provider = key.get("provider")
        if provider:
            provider_schema = schema_registry.get_schema(provider)
            if provider_schema:
                masked_key["schema"] = provider_schema.model_dump()
        
        # Add configured_fields list
        masked_key["configured_fields"] = get_configured_fields(key)
        
        enhanced_keys.append(masked_key)
    
    return {"keys": enhanced_keys}


@router.get("/schemas", response_model=Dict[str, Any])
async def list_provider_schemas():
    """
    List all provider credential schemas.
    
    Returns:
        Dictionary containing array of all provider schemas with their
        credential requirements, field descriptions, and documentation links.
    """
    schemas = schema_registry.list_schemas()
    return {
        "schemas": [schema.model_dump() for schema in schemas]
    }


@router.get("/schemas/{provider}", response_model=Dict[str, Any])
async def get_provider_schema(provider: str):
    """
    Get credential schema for a specific provider.
    
    Args:
        provider: Provider identifier (e.g., 'openai', 'anthropic', 'bedrock')
        
    Returns:
        Provider schema with credential requirements and field descriptions.
        
    Raises:
        HTTPException: 404 if provider schema not found
    """
    schema = schema_registry.get_schema(provider)
    if schema is None:
        supported = schema_registry.get_supported_providers()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schema for provider '{provider}' not found. Supported providers: {', '.join(supported)}"
        )
    return schema.model_dump()


@router.get("/{provider}", response_model=Dict[str, Any])
async def get_model_key(provider: str):
    key = await db_repository.get_model_key(provider, include_secrets=True)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    
    # Mask secrets
    masked_key = mask_model_key(key)
    
    # Fetch provider schema
    provider_schema = schema_registry.get_schema(provider)
    if provider_schema:
        masked_key["schema"] = provider_schema.model_dump()
    
    # Add configured_fields list
    masked_key["configured_fields"] = get_configured_fields(key)
    
    return masked_key


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_model_key(data: ModelKeyCreate):
    if await db_repository.model_key_exists(data.provider):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Model key for provider '{data.provider}' already exists"
        )
    
    # Normalize input data (handle aws_* prefixed fields)
    # Use exclude_none=True to exclude fields that weren't provided in the request
    input_dict = data.model_dump(exclude_none=True)
    normalized_data = CredentialTransformer.normalize_input(input_dict)
    
    # Remove 'provider' from normalized_data before validation
    # (provider is used to identify the schema, not a credential field)
    validation_data = {k: v for k, v in normalized_data.items() if k != 'provider'}
    
    # Validate credentials against provider schema
    try:
        credential_validator.validate(data.provider, validation_data)
    except CredentialValidationError as e:
        # Return 400 Bad Request with detailed validation error
        error_response = e.to_dict()
        # Add example of correct structure
        schema = e.schema
        example = {"provider": data.provider}
        for field in schema.fields:
            if field.example:
                example[field.name] = field.example
        error_response["example"] = example
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response
        )
    
    # Create the model key in database
    result = await db_repository.create_model_key(input_dict)
    logger.info("Created model key for provider: %s", data.provider)
    
    # Mask secrets and add schema information to response
    masked_result = mask_model_key(result)
    
    # Fetch provider schema
    provider_schema = schema_registry.get_schema(data.provider)
    if provider_schema:
        masked_result["schema"] = provider_schema.model_dump()
    
    # Add configured_fields list
    masked_result["configured_fields"] = get_configured_fields(result)
    
    return masked_result


@router.put("/{provider}", response_model=Dict[str, Any])
async def update_model_key(provider: str, data: ModelKeyUpdate):
    update_data = data.model_dump(exclude_unset=True)
    if not await db_repository.model_key_exists(provider):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    
    # Normalize input data (handle aws_* prefixed fields)
    normalized_update = CredentialTransformer.normalize_input(update_data)
    
    # Fetch existing credentials from database
    existing_creds = await db_repository.get_model_key(provider, include_secrets=True)
    if not existing_creds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model key for provider '{provider}' not found"
        )
    
    # Merge update data with existing credentials (for partial updates)
    merged_data = {**existing_creds, **normalized_update}
    # Ensure provider is set
    merged_data["provider"] = provider
    
    # Remove 'provider' from merged_data before validation
    # (provider is used to identify the schema, not a credential field)
    validation_data = {k: v for k, v in merged_data.items() if k != 'provider'}
    
    # Validate merged credentials against provider schema
    try:
        credential_validator.validate(provider, validation_data)
    except CredentialValidationError as e:
        # Return 400 Bad Request with detailed validation error
        error_response = e.to_dict()
        # Add example of correct structure
        schema = e.schema
        example = {"provider": provider}
        for field in schema.fields:
            if field.example:
                example[field.name] = field.example
        error_response["example"] = example
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response
        )
    
    # Update the model key in database
    result = await db_repository.update_model_key(provider, update_data)
    logger.info("Updated model key for provider: %s", provider)
    
    # Mask secrets and add schema information to response
    masked_result = mask_model_key(result)
    
    # Fetch provider schema
    provider_schema = schema_registry.get_schema(provider)
    if provider_schema:
        masked_result["schema"] = provider_schema.model_dump()
    
    # Add configured_fields list
    masked_result["configured_fields"] = get_configured_fields(result)
    
    return masked_result


@router.post("/upsert", response_model=Dict[str, Any])
async def upsert_model_key(data: ModelKeyCreate):
    # Normalize input data (handle aws_* prefixed fields)
    # Use exclude_none=True to exclude fields that weren't provided in the request
    input_dict = data.model_dump(exclude_none=True)
    normalized_data = CredentialTransformer.normalize_input(input_dict)
    
    # Remove 'provider' from normalized_data before validation
    # (provider is used to identify the schema, not a credential field)
    validation_data = {k: v for k, v in normalized_data.items() if k != 'provider'}
    
    # Validate credentials against provider schema
    try:
        credential_validator.validate(data.provider, validation_data)
    except CredentialValidationError as e:
        # Return 400 Bad Request with detailed validation error
        error_response = e.to_dict()
        # Add example of correct structure
        schema = e.schema
        example = {"provider": data.provider}
        for field in schema.fields:
            if field.example:
                example[field.name] = field.example
        error_response["example"] = example
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response
        )
    
    # Upsert the model key in database
    result = await db_repository.upsert_model_key(data.provider, input_dict)
    logger.info("Upserted model key for provider: %s", data.provider)
    
    # Mask secrets and add schema information to response
    masked_result = mask_model_key(result)
    
    # Fetch provider schema
    provider_schema = schema_registry.get_schema(data.provider)
    if provider_schema:
        masked_result["schema"] = provider_schema.model_dump()
    
    # Add configured_fields list
    masked_result["configured_fields"] = get_configured_fields(result)
    
    return masked_result


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
