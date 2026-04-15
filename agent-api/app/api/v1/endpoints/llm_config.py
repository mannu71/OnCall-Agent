"""LLM Configuration API routes."""
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.repositories.db_repository import db_repository

router = APIRouter(prefix="/llm-config", tags=["llm-config"])
logger = logging.getLogger(__name__)


class LLMProviderConfig(BaseModel):
    provider: str = Field(..., description="Provider name (OpenAI, Anthropic, etc.)")
    model: Optional[str] = Field(None, description="Model name")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Base URL for Ollama")
    temperature: Optional[float] = Field(0.7, description="Temperature setting")
    maxTokens: Optional[int] = Field(4096, description="Max tokens")


class ApiKeyRequest(BaseModel):
    api_key: str = Field(..., description="API key value")


class DiscoverModelsRequest(BaseModel):
    provider: str = Field(..., description="Provider name from Model Keys (OpenAI, Anthropic, etc.)")


@router.post("/discover/models", response_model=Dict[str, Any])
async def discover_provider_models(request: DiscoverModelsRequest):
    """Discover available models for a configured provider.

    Looks up the provider's credentials from Model Keys and calls
    the provider's list-models API. Supports: OpenAI, Anthropic,
    Google, Groq, Azure OpenAI, Ollama, AWS Bedrock.
    """
    import httpx
    import boto3
    import os
    from botocore.exceptions import ClientError as BotoClientError

    ssl_verify = os.environ.get("AWS_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

    provider = request.provider
    mk = await db_repository.get_model_key(provider, include_secrets=True)
    if not mk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Model Key configured for provider '{provider}'. Configure it in Model Keys first.",
        )

    api_key = mk.get("api_key")
    secret_key = mk.get("secret_key")
    endpoint = mk.get("endpoint")
    region = mk.get("region")
    aws_access_key_id = mk.get("aws_access_key_id")
    aws_secret_access_key = mk.get("aws_secret_access_key")
    aws_session_token = mk.get("aws_session_token")

    ICON_MAP = {
        "gpt": "🧠",
        "o1": "🧠",
        "o3": "🧠",
        "o4": "🧠",
        "claude": "🤖",
        "gemini": "✨",
        "llama": "🦙",
        "mistral": "⚡",
        "mixtral": "⚡",
        "gemma": "💎",
        "phi": "🔮",
        "qwen": "🐉",
        "deepseek": "🔍",
        "command": "🔵",
        "titan": "📝",
        "cohere": "🔵",
        "ai21": "🟣",
        "stability": "🎨",
        "dall": "🎨",
        "whisper": "🎙️",
        "tts": "🔊",
        "dify": "🔗",
    }

    def pick_icon(model_id: str) -> str:
        model_lower = model_id.lower()
        return next((v for k, v in ICON_MAP.items() if k in model_lower), "🤖")

    try:
        if provider == "AWS Bedrock":
            bedrock_region = region or "us-east-1"
            kwargs = {"region_name": bedrock_region}
            if aws_access_key_id and aws_secret_access_key:
                kwargs["aws_access_key_id"] = aws_access_key_id
                kwargs["aws_secret_access_key"] = aws_secret_access_key
                if aws_session_token:
                    kwargs["aws_session_token"] = aws_session_token
            import os
            if os.environ.get("AWS_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
                from botocore.config import Config
                kwargs["config"] = Config(retries={"max_attempts": 3})
                kwargs["verify"] = False
            try:
                client = boto3.client("bedrock", **kwargs)
                response = client.list_foundation_models()
            except BotoClientError as e:
                raise HTTPException(status_code=502, detail=f"Failed to query AWS Bedrock: {e}")
            models = response.get("modelSummaries", [])
            discovered = []
            for m in models:
                model_id = m.get("modelId", "")
                provider_name = m.get("providerName", "")
                model_name = m.get("modelName", "")
                if not model_id:
                    continue
                display_name = model_name or (model_id.split(".")[-1] if "." in model_id else model_id)
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "AWS Bedrock",
                    "region": bedrock_region,
                    "icon": pick_icon(model_id),
                    "description": f"{provider_name} - {model_id}" if provider_name else model_id,
                    "already_exists": existing is not None,
                })
            logger.info("Discovered %d Bedrock models in %s", len(discovered), bedrock_region)
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        elif provider == "OpenAI":
            if not api_key:
                raise HTTPException(status_code=400, detail="No API key configured for OpenAI")
            async with httpx.AsyncClient(timeout=30.0, verify=ssl_verify) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail=f"OpenAI API error: HTTP {resp.status_code}")
                data = resp.json()
            discovered = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if not model_id:
                    continue
                display_name = model_id
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "OpenAI",
                    "icon": pick_icon(model_id),
                    "description": model_id,
                    "already_exists": existing is not None,
                })
            discovered.sort(key=lambda x: x["name"])
            logger.info("Discovered %d OpenAI models", len(discovered))
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        elif provider == "Anthropic":
            if not api_key:
                raise HTTPException(status_code=400, detail="No API key configured for Anthropic")
            async with httpx.AsyncClient(timeout=30.0, verify=ssl_verify) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail=f"Anthropic API error: HTTP {resp.status_code}")
                data = resp.json()
            discovered = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                display_name = m.get("display_name", model_id)
                if not model_id:
                    continue
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "Anthropic",
                    "icon": pick_icon(model_id),
                    "description": display_name,
                    "already_exists": existing is not None,
                })
            discovered.sort(key=lambda x: x["name"])
            logger.info("Discovered %d Anthropic models", len(discovered))
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        elif provider == "Google":
            if not api_key:
                raise HTTPException(status_code=400, detail="No API key configured for Google")
            async with httpx.AsyncClient(timeout=30.0, verify=ssl_verify) as client:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                )
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail=f"Google API error: HTTP {resp.status_code}")
                data = resp.json()
            discovered = []
            for m in data.get("models", []):
                model_id = m.get("name", "").replace("models/", "")
                display_name = m.get("displayName", model_id)
                if not model_id:
                    continue
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "Google",
                    "icon": pick_icon(model_id),
                    "description": m.get("description", display_name),
                    "already_exists": existing is not None,
                })
            discovered.sort(key=lambda x: x["name"])
            logger.info("Discovered %d Google models", len(discovered))
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        elif provider == "Groq":
            if not api_key:
                raise HTTPException(status_code=400, detail="No API key configured for Groq")
            async with httpx.AsyncClient(timeout=30.0, verify=ssl_verify) as client:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail=f"Groq API error: HTTP {resp.status_code}")
                data = resp.json()
            discovered = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if not model_id:
                    continue
                display_name = model_id
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "Groq",
                    "icon": pick_icon(model_id),
                    "description": model_id,
                    "already_exists": existing is not None,
                })
            discovered.sort(key=lambda x: x["name"])
            logger.info("Discovered %d Groq models", len(discovered))
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        elif provider == "Azure OpenAI":
            if not api_key:
                raise HTTPException(status_code=400, detail="No API key configured for Azure OpenAI")
            if not endpoint:
                raise HTTPException(status_code=400, detail="No endpoint configured for Azure OpenAI")
            async with httpx.AsyncClient(timeout=30.0, verify=ssl_verify) as client:
                resp = await client.get(
                    f"{endpoint}/openai/models?api-version=2024-02-01",
                    headers={"api-key": api_key},
                )
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail=f"Azure OpenAI API error: HTTP {resp.status_code}")
                data = resp.json()
            discovered = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                display_name = m.get("id", model_id)
                if not model_id:
                    continue
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "Azure OpenAI",
                    "endpoint": endpoint,
                    "icon": pick_icon(model_id),
                    "description": f"{model_id} @ {endpoint}",
                    "already_exists": existing is not None,
                })
            discovered.sort(key=lambda x: x["name"])
            logger.info("Discovered %d Azure OpenAI models", len(discovered))
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        elif provider == "Ollama":
            ollama_url = endpoint or "http://localhost:11434"
            async with httpx.AsyncClient(timeout=30.0, verify=ssl_verify) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail=f"Ollama API error: HTTP {resp.status_code}")
                data = resp.json()
            discovered = []
            for m in data.get("models", []):
                model_id = m.get("name", "")
                if not model_id:
                    continue
                display_name = model_id
                existing = await db_repository.get_llm_config(display_name)
                discovered.append({
                    "name": display_name,
                    "model": model_id,
                    "provider": "Ollama",
                    "baseUrl": ollama_url,
                    "icon": pick_icon(model_id),
                    "description": f"{model_id} ({m.get('size', '')})" if m.get("size") else model_id,
                    "already_exists": existing is not None,
                })
            discovered.sort(key=lambda x: x["name"])
            logger.info("Discovered %d Ollama models", len(discovered))
            return {"success": True, "discovered": len(discovered), "provider": provider, "models": discovered}

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Model discovery not supported for provider '{provider}'. Supported: OpenAI, Anthropic, Google, Groq, Azure OpenAI, Ollama, AWS Bedrock",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error discovering models for %s: %s", provider, e)
        raise HTTPException(status_code=500, detail=str(e))


class AWSDiscoverRequest(BaseModel):
    region: Optional[str] = Field("us-east-1", description="AWS region")
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    aws_session_token: Optional[str] = Field(None, description="AWS Session Token")


class LLMConfigCreate(BaseModel):
    name: str = Field(..., description="LLM configuration name")
    provider: str = Field(..., description="Provider name")
    model: Optional[str] = Field(None, description="Model name")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Base URL for Ollama")
    temperature: Optional[float] = Field(0.7, description="Temperature setting")
    maxTokens: Optional[int] = Field(4096, description="Max tokens")
    region: Optional[str] = Field(None, description="AWS region")
    icon: Optional[str] = Field(None, description="Icon")
    description: Optional[str] = Field(None, description="Description")


class LLMConfigUpdate(BaseModel):
    name: Optional[str] = Field(None, description="New name (for rename)")
    provider: Optional[str] = Field(None, description="Provider name")
    model: Optional[str] = Field(None, description="Model name")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Base URL for Ollama")
    temperature: Optional[float] = Field(None, description="Temperature setting")
    maxTokens: Optional[int] = Field(None, description="Max tokens")
    region: Optional[str] = Field(None, description="AWS region")
    icon: Optional[str] = Field(None, description="Icon")
    description: Optional[str] = Field(None, description="Description")


def mask_api_key(api_key: str) -> str:
    if not api_key or len(api_key) < 8:
        return "********"
    return f"{api_key[:4]}...{api_key[-4:]}"


@router.post("/discover", response_model=Dict[str, Any])
async def discover_bedrock_models(request: Optional[AWSDiscoverRequest] = None):
    """Discover available AWS Bedrock models without saving.

    Calls the Bedrock ListFoundationModels API and returns the list
    for the user to select which models to add.

    Accepts optional AWS credentials. If not provided, falls back to
    the default credential chain (env vars, ~/.aws, IAM role, etc.).

    Returns:
        List of discovered models with metadata
    """
    import boto3
    from botocore.exceptions import ClientError

    region = (request.region if request else None) or "us-east-1"
    aws_access_key_id = request.aws_access_key_id if request else None
    aws_secret_access_key = request.aws_secret_access_key if request else None
    aws_session_token = request.aws_session_token if request else None

    kwargs = {"region_name": region}
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            kwargs["aws_session_token"] = aws_session_token

    import os
    if os.environ.get("AWS_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
        from botocore.config import Config
        kwargs["config"] = Config(retries={"max_attempts": 3})
        kwargs["verify"] = False

    try:
        client = boto3.client("bedrock", **kwargs)
        response = client.list_foundation_models()
    except ClientError as e:
        logger.error("Failed to list Bedrock models: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to query AWS Bedrock: {e}",
        )

    models = response.get("modelSummaries", [])
    discovered = []

    ICON_MAP = {
        "claude": "🤖",
        "titan": "📝",
        "llama": "🦙",
        "mistral": "⚡",
        "cohere": "🔵",
        "ai21": "🟣",
        "stability": "🎨",
    }

    for m in models:
        model_id = m.get("modelId", "")
        provider_name = m.get("providerName", "")
        model_name = m.get("modelName", "")

        if not model_id:
            continue

        icon = next(
            (v for k, v in ICON_MAP.items() if k in model_id.lower()),
            "☁️",
        )

        display_name = model_name or model_id.split(".")[-1] if "." in model_id else model_id

        existing = await db_repository.get_llm_config(display_name)
        discovered.append({
            "name": display_name,
            "model": model_id,
            "provider": "AWS Bedrock",
            "region": region,
            "icon": icon,
            "description": f"{provider_name} - {model_id}" if provider_name else model_id,
            "already_exists": existing is not None,
        })

    logger.info("Discovered %d Bedrock models in %s", len(discovered), region)
    return {
        "success": True,
        "discovered": len(discovered),
        "region": region,
        "models": discovered,
    }


class AddDiscoveredModelsRequest(BaseModel):
    models: list[Dict[str, Any]] = Field(..., description="List of model configs to add")
    region: Optional[str] = Field("us-east-1", description="AWS region")


@router.post("/discover/add", response_model=Dict[str, Any])
async def add_discovered_models(request: AddDiscoveredModelsRequest):
    """Save selected discovered models to the database."""
    added = []
    skipped = []
    for model in request.models:
        name = model.get("name")
        if not name:
            continue
        if await db_repository.llm_config_exists(name):
            skipped.append(name)
            continue
        config_data = {
            "provider": model.get("provider", "AWS Bedrock"),
            "model": model.get("model", ""),
            "region": model.get("region", request.region),
            "icon": model.get("icon", "☁️"),
            "description": model.get("description", ""),
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        await db_repository.create_llm_config({**config_data, "name": name})
        added.append(name)

    logger.info("Added %d discovered models, skipped %d existing", len(added), len(skipped))
    return {
        "success": True,
        "added": added,
        "skipped": skipped,
        "added_count": len(added),
        "skipped_count": len(skipped),
    }


@router.get("", response_model=Dict[str, Any])
async def get_llm_config():
    configs = await db_repository.list_llm_configs(include_api_key=True)
    masked_llms = {}
    for name, cfg in configs.items():
        masked = {**cfg}
        if masked.get("api_key"):
            masked["api_key"] = mask_api_key(masked["api_key"])
            masked["apiKey"] = mask_api_key(masked["apiKey"])
        if masked.get("aws_access_key_id"):
            masked["aws_access_key_id"] = mask_api_key(masked["aws_access_key_id"])
        if masked.get("aws_secret_access_key"):
            masked["aws_secret_access_key"] = mask_api_key(masked["aws_secret_access_key"])
        if masked.get("aws_session_token"):
            masked["aws_session_token"] = mask_api_key(masked["aws_session_token"])
        masked_llms[name] = masked
    return {"llms": masked_llms}


@router.get("/{llm_name}", response_model=Dict[str, Any])
async def get_llm_by_name(llm_name: str):
    cfg = await db_repository.get_llm_config(llm_name, include_api_key=True)
    if not cfg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    if cfg.get("api_key"):
        cfg["api_key"] = mask_api_key(cfg["api_key"])
        cfg["apiKey"] = mask_api_key(cfg["apiKey"])
    if cfg.get("aws_access_key_id"):
        cfg["aws_access_key_id"] = mask_api_key(cfg["aws_access_key_id"])
    if cfg.get("aws_secret_access_key"):
        cfg["aws_secret_access_key"] = mask_api_key(cfg["aws_secret_access_key"])
    if cfg.get("aws_session_token"):
        cfg["aws_session_token"] = mask_api_key(cfg["aws_session_token"])
    return {"name": llm_name, **cfg}


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_llm_config(llm_data: LLMConfigCreate):
    if await db_repository.llm_config_exists(llm_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"LLM configuration '{llm_data.name}' already exists"
        )
    created = await db_repository.create_llm_config(llm_data.model_dump())
    logger.info("Created LLM configuration: %s", llm_data.name)
    return {"name": llm_data.name, **created}


@router.put("/{llm_name}", response_model=Dict[str, Any])
async def update_llm_config(llm_name: str, llm_update: LLMConfigUpdate):
    update_data = llm_update.model_dump(exclude_unset=True)
    new_name = update_data.get("name") or llm_name
    if new_name != llm_name and await db_repository.llm_config_exists(new_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"LLM configuration '{new_name}' already exists"
        )
    updated = await db_repository.update_llm_config(llm_name, update_data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    logger.info("Updated LLM configuration: %s", llm_name)
    return {"name": new_name, **updated}


@router.delete("/{llm_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_config(llm_name: str):
    deleted = await db_repository.delete_llm_config(llm_name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    logger.info("Deleted LLM configuration: %s", llm_name)
    return None


class BulkDeleteRequest(BaseModel):
    names: list[str] = Field(..., description="List of LLM config names to delete")


@router.post("/bulk-delete", response_model=Dict[str, Any])
async def bulk_delete_llm_configs(request: BulkDeleteRequest):
    """Delete multiple LLM configurations by name."""
    deleted = []
    not_found = []
    for name in request.names:
        if await db_repository.delete_llm_config(name):
            deleted.append(name)
        else:
            not_found.append(name)
    logger.info("Bulk deleted %d LLM configs, %d not found", len(deleted), len(not_found))
    return {
        "success": True,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "not_found": not_found,
        "not_found_count": len(not_found),
    }


@router.post("/{llm_name}/api-key", response_model=Dict[str, Any])
async def set_api_key(llm_name: str, request: ApiKeyRequest):
    result = await db_repository.set_llm_api_key(llm_name, request.api_key)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    logger.info("Set API key for LLM: %s", llm_name)
    return {"success": True, "message": f"API key set for {llm_name}"}


@router.get("/{llm_name}/api-key/masked", response_model=Dict[str, Any])
async def get_api_key_masked(llm_name: str):
    api_key = await db_repository.get_llm_api_key(llm_name)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    masked = mask_api_key(api_key) if api_key else ""
    return {"success": True, "masked": masked}


@router.get("/{llm_name}/api-key/exists", response_model=Dict[str, Any])
async def check_api_key_exists(llm_name: str):
    api_key = await db_repository.get_llm_api_key(llm_name)
    exists = bool(api_key)
    return {"success": True, "exists": exists}


@router.delete("/{llm_name}/api-key", response_model=Dict[str, Any])
async def delete_api_key(llm_name: str):
    deleted = await db_repository.delete_llm_api_key(llm_name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    logger.info("Deleted API key for LLM: %s", llm_name)
    return {"success": True, "message": f"API key deleted for {llm_name}"}


class LLMTestRequest(BaseModel):
    provider: Optional[str] = Field(None, description="Override provider")
    model: Optional[str] = Field(None, description="Override model")
    endpoint: Optional[str] = Field(None, description="Override endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Override base URL")
    api_key: Optional[str] = Field(None, description="API key for non-AWS providers")
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    aws_session_token: Optional[str] = Field(None, description="AWS Session Token")
    region: Optional[str] = Field(None, description="AWS region override")


@router.post("/{llm_name}/test", response_model=Dict[str, Any])
async def test_llm_connection(llm_name: str, request: Optional[LLMTestRequest] = None):
    import httpx

    cfg = await db_repository.get_llm_config(llm_name, include_api_key=True)
    if not cfg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )

    test_config = cfg
    if request:
        if request.provider:
            test_config["provider"] = request.provider
        if request.model:
            test_config["model"] = request.model
        if request.endpoint:
            test_config["endpoint"] = request.endpoint
        if request.baseUrl:
            test_config["base_url"] = request.baseUrl
        if request.api_key:
            test_config["api_key"] = request.api_key
        if request.aws_access_key_id:
            test_config["aws_access_key_id"] = request.aws_access_key_id
        if request.aws_secret_access_key:
            test_config["aws_secret_access_key"] = request.aws_secret_access_key
        if request.aws_session_token:
            test_config["aws_session_token"] = request.aws_session_token
        if request.region:
            test_config["region"] = request.region

    provider = test_config.get("provider", "")
    model = test_config.get("model", "")
    endpoint = test_config.get("endpoint", "")
    base_url = test_config.get("base_url") or test_config.get("baseUrl", "")
    api_key = test_config.get("api_key") or test_config.get("apiKey", "")

    if not api_key and provider not in ("AWS Bedrock", "Bedrock", "bedrock", "Ollama"):
        mk = await db_repository.get_model_key(provider, include_secrets=True)
        if mk:
            if mk.get("api_key") and not api_key:
                api_key = mk["api_key"]
            if mk.get("endpoint") and not endpoint:
                endpoint = mk["endpoint"]
            if mk.get("region") and not test_config.get("region"):
                test_config["region"] = mk["region"]

    import os
    ssl_verify = os.environ.get("AWS_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0, verify=ssl_verify) as client:
            if provider == "OpenAI":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={**headers, "Authorization": f"Bearer {api_key}"}
                )
                if resp.is_success:
                    return {"success": True, "message": "Connected to OpenAI"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif provider == "Anthropic":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        **headers,
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01"
                    },
                    json={"model": model or "claude-3-5-sonnet-20241022", "max_tokens": 1, "messages": []}
                )
                if resp.is_success or resp.status_code == 400:
                    return {"success": True, "message": "Connected to Anthropic"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif provider == "Groq":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={**headers, "Authorization": f"Bearer {api_key}"}
                )
                if resp.is_success:
                    return {"success": True, "message": "Connected to Groq"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif provider == "Google":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                    headers=headers
                )
                if resp.is_success:
                    return {"success": True, "message": "Connected to Google"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif provider == "Azure OpenAI":
                if not endpoint:
                    return {"success": False, "error": "Azure endpoint URL is required"}
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    f"{endpoint}/openai/models?api-version=2024-02-01",
                    headers={**headers, "api-key": api_key}
                )
                if resp.is_success:
                    return {"success": True, "message": "Connected to Azure OpenAI"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif provider == "Ollama":
                ollama_url = base_url or "http://localhost:11434"
                resp = await client.get(f"{ollama_url}/api/tags", headers=headers)
                if resp.is_success:
                    return {"success": True, "message": "Connected to Ollama"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif provider in ("AWS Bedrock", "Bedrock", "bedrock"):
                import boto3
                from botocore.exceptions import ClientError as BotoClientError

                region = test_config.get("region", "us-east-1")
                aws_access_key_id = test_config.get("aws_access_key_id")
                aws_secret_access_key = test_config.get("aws_secret_access_key")
                aws_session_token = test_config.get("aws_session_token")

                mk = await db_repository.get_model_key("AWS Bedrock", include_secrets=True)
                if mk:
                    if not aws_access_key_id and mk.get("aws_access_key_id"):
                        aws_access_key_id = mk["aws_access_key_id"]
                    if not aws_secret_access_key and mk.get("aws_secret_access_key"):
                        aws_secret_access_key = mk["aws_secret_access_key"]
                    if not aws_session_token and mk.get("aws_session_token"):
                        aws_session_token = mk["aws_session_token"]
                    if (not region or region == "us-east-1") and mk.get("region"):
                        region = mk["region"]

                boto_kwargs = {"region_name": region}
                if aws_access_key_id and aws_secret_access_key:
                    boto_kwargs["aws_access_key_id"] = aws_access_key_id
                    boto_kwargs["aws_secret_access_key"] = aws_secret_access_key
                    if aws_session_token:
                        boto_kwargs["aws_session_token"] = aws_session_token
                if os.environ.get("AWS_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
                    boto_kwargs["verify"] = False

                try:
                    bedrock = boto3.client("bedrock", **boto_kwargs)
                    bedrock.list_foundation_models(byProvider="anthropic")
                    return {"success": True, "message": f"Connected to AWS Bedrock ({region})"}
                except BotoClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if "ExpiredToken" in str(e) or error_code == "ExpiredTokenException":
                        return {"success": False, "error": "AWS credentials expired. Please refresh your AWS credentials and try again."}
                    if "AccessDenied" in str(e) or error_code == "AccessDeniedException":
                        return {"success": False, "error": "Access denied. Check that your AWS credentials have Bedrock permissions."}
                    return {"success": False, "error": f"AWS Bedrock error: {e}"}

            else:
                return {"success": False, "error": f"Unknown provider: {provider}"}

    except httpx.TimeoutException:
        return {"success": False, "error": "Connection timeout"}
    except httpx.ConnectError:
        return {"success": False, "error": "Connection refused - check if service is running"}
    except Exception as e:
        logger.error("Error testing LLM connection: %s", e)
        return {"success": False, "error": str(e)}
