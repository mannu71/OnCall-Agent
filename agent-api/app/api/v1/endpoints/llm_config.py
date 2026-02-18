"""LLM Configuration API routes."""
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
import json
from pathlib import Path

router = APIRouter(prefix="/llm-config", tags=["llm-config"])
logger = logging.getLogger(__name__)

# Path to LLM config file
def get_llm_config_path() -> Path:
    """Get the path to the LLM config file."""
    return Path("data") / "config" / "llm-config.json"


class LLMProviderConfig(BaseModel):
    """LLM provider configuration model."""
    provider: str = Field(..., description="Provider name (OpenAI, Anthropic, etc.)")
    model: Optional[str] = Field(None, description="Model name")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Base URL for Ollama")
    temperature: Optional[float] = Field(0.7, description="Temperature setting")
    maxTokens: Optional[int] = Field(4096, description="Max tokens")


class ApiKeyRequest(BaseModel):
    """Request model for setting API key."""
    api_key: str = Field(..., description="API key value")


class LLMConfigCreate(BaseModel):
    """Model for creating a new LLM config."""
    name: str = Field(..., description="LLM configuration name")
    provider: str = Field(..., description="Provider name")
    model: Optional[str] = Field(None, description="Model name")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Base URL for Ollama")
    temperature: Optional[float] = Field(0.7, description="Temperature setting")
    maxTokens: Optional[int] = Field(4096, description="Max tokens")


class LLMConfigUpdate(BaseModel):
    """Model for updating an LLM config."""
    name: Optional[str] = Field(None, description="New name (for rename)")
    provider: Optional[str] = Field(None, description="Provider name")
    model: Optional[str] = Field(None, description="Model name")
    endpoint: Optional[str] = Field(None, description="Custom endpoint URL")
    baseUrl: Optional[str] = Field(None, description="Base URL for Ollama")
    temperature: Optional[float] = Field(None, description="Temperature setting")
    maxTokens: Optional[int] = Field(None, description="Max tokens")


async def load_llm_config() -> Dict[str, Any]:
    """Load LLM configuration from file.
    
    Returns:
        LLM configuration dictionary
    """
    config_path = get_llm_config_path()
    
    if not config_path.exists():
        return {"llms": {}}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading LLM config: {e}")
        return {"llms": {}}


async def save_llm_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Save LLM configuration to file.
    
    Args:
        config: Configuration to save
        
    Returns:
        Saved configuration
    """
    config_path = get_llm_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    
    return config


def mask_api_key(api_key: str) -> str:
    """Mask an API key for display.
    
    Args:
        api_key: The API key to mask
        
    Returns:
        Masked API key
    """
    if not api_key or len(api_key) < 8:
        return "********"
    return f"{api_key[:4]}...{api_key[-4:]}"


@router.get("", response_model=Dict[str, Any])
async def get_llm_config():
    """Get full LLM configuration.
    
    Returns:
        Full LLM configuration with masked API keys
    """
    config = await load_llm_config()
    
    # Mask API keys in response
    llms = config.get("llms", {})
    masked_llms = {}
    
    for name, llm_config in llms.items():
        masked_config = {**llm_config}
        if "apiKey" in masked_config:
            masked_config["apiKey"] = mask_api_key(masked_config["apiKey"])
        masked_llms[name] = masked_config
    
    return {"llms": masked_llms}


@router.get("/{llm_name}", response_model=Dict[str, Any])
async def get_llm_by_name(llm_name: str):
    """Get a specific LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration
        
    Returns:
        LLM configuration with masked API key
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    
    llm_config = {**llms[llm_name]}
    if "apiKey" in llm_config:
        llm_config["apiKey"] = mask_api_key(llm_config["apiKey"])
    
    return {"name": llm_name, **llm_config}


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_llm_config(llm_data: LLMConfigCreate):
    """Create a new LLM configuration.
    
    Args:
        llm_data: LLM configuration data
        
    Returns:
        Created LLM configuration
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_data.name in llms:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"LLM configuration '{llm_data.name}' already exists"
        )
    
    llm_dict = llm_data.model_dump()
    name = llm_dict.pop("name")
    llms[name] = llm_dict
    config["llms"] = llms
    
    await save_llm_config(config)
    logger.info(f"Created LLM configuration: {llm_data.name}")
    
    return {"name": llm_data.name, **llm_dict}


@router.put("/{llm_name}", response_model=Dict[str, Any])
async def update_llm_config(llm_name: str, llm_update: LLMConfigUpdate):
    """Update an existing LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration to update
        llm_update: Updated configuration data
        
    Returns:
        Updated LLM configuration
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    
    update_data = llm_update.model_dump(exclude_unset=True)
    
    # Handle rename
    new_name = update_data.pop("name", None)
    if new_name and new_name != llm_name:
        if new_name in llms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"LLM configuration '{new_name}' already exists"
            )
        # Move to new name
        llm_config = llms.pop(llm_name)
        llm_config.update(update_data)
        llms[new_name] = llm_config
    else:
        llms[llm_name].update(update_data)
    
    config["llms"] = llms
    await save_llm_config(config)
    logger.info(f"Updated LLM configuration: {llm_name}")
    
    return {"name": new_name or llm_name, **llms[new_name or llm_name]}


@router.delete("/{llm_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_config(llm_name: str):
    """Delete an LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration to delete
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    
    del llms[llm_name]
    config["llms"] = llms
    await save_llm_config(config)
    logger.info(f"Deleted LLM configuration: {llm_name}")
    
    return None


@router.post("/{llm_name}/api-key", response_model=Dict[str, Any])
async def set_api_key(llm_name: str, request: ApiKeyRequest):
    """Set the API key for an LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration
        request: API key request
        
    Returns:
        Success status
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        # Create the LLM config if it doesn't exist
        llms[llm_name] = {}
    
    llms[llm_name]["apiKey"] = request.api_key
    config["llms"] = llms
    await save_llm_config(config)
    
    logger.info(f"Set API key for LLM: {llm_name}")
    return {"success": True, "message": f"API key set for {llm_name}"}


@router.get("/{llm_name}/api-key/masked", response_model=Dict[str, Any])
async def get_api_key_masked(llm_name: str):
    """Get the masked API key for an LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration
        
    Returns:
        Masked API key
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    
    api_key = llms[llm_name].get("apiKey", "")
    masked = mask_api_key(api_key) if api_key else ""
    
    return {"success": True, "masked": masked}


@router.get("/{llm_name}/api-key/exists", response_model=Dict[str, Any])
async def check_api_key_exists(llm_name: str):
    """Check if an API key exists for an LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration
        
    Returns:
        Whether API key exists
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        return {"success": True, "exists": False}
    
    api_key = llms[llm_name].get("apiKey", "")
    exists = bool(api_key and len(api_key) > 0)
    
    return {"success": True, "exists": exists}


@router.delete("/{llm_name}/api-key", response_model=Dict[str, Any])
async def delete_api_key(llm_name: str):
    """Delete the API key for an LLM configuration.
    
    Args:
        llm_name: Name of the LLM configuration
        
    Returns:
        Success status
    """
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    if llm_name not in llms:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    
    if "apiKey" in llms[llm_name]:
        del llms[llm_name]["apiKey"]
        config["llms"] = llms
        await save_llm_config(config)
    
    logger.info(f"Deleted API key for LLM: {llm_name}")
    return {"success": True, "message": f"API key deleted for {llm_name}"}


@router.post("/{llm_name}/test", response_model=Dict[str, Any])
async def test_llm_connection(llm_name: str, llm_config: Optional[LLMProviderConfig] = None):
    """Test LLM connection.
    
    Args:
        llm_name: Name of the LLM configuration
        llm_config: Optional LLM config override for testing
        
    Returns:
        Connection test result
    """
    import httpx
    
    config = await load_llm_config()
    llms = config.get("llms", {})
    
    # Use provided config or load from saved config
    if llm_config:
        test_config = llm_config.model_dump()
    elif llm_name in llms:
        test_config = llms[llm_name]
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM configuration '{llm_name}' not found"
        )
    
    provider = test_config.get("provider", "")
    model = test_config.get("model", "")
    endpoint = test_config.get("endpoint", "")
    base_url = test_config.get("baseUrl", "")
    api_key = test_config.get("apiKey", "")
    
    # Build test request based on provider
    headers = {"Content-Type": "application/json"}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == "OpenAI":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={**headers, "Authorization": f"Bearer {api_key}"}
                )
                if resp.ok:
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
                # 400 is expected with empty messages - means auth works
                if resp.ok or resp.status_code == 400:
                    return {"success": True, "message": "Connected to Anthropic"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}
            
            elif provider == "Groq":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={**headers, "Authorization": f"Bearer {api_key}"}
                )
                if resp.ok:
                    return {"success": True, "message": "Connected to Groq"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}
            
            elif provider == "Google":
                if not api_key:
                    return {"success": False, "error": "No API key configured"}
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                    headers=headers
                )
                if resp.ok:
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
                if resp.ok:
                    return {"success": True, "message": "Connected to Azure OpenAI"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}
            
            elif provider == "Ollama":
                ollama_url = base_url or "http://localhost:11434"
                resp = await client.get(f"{ollama_url}/api/tags", headers=headers)
                if resp.ok:
                    return {"success": True, "message": "Connected to Ollama"}
                return {"success": False, "error": f"HTTP {resp.status_code}"}
            
            else:
                return {"success": False, "error": f"Unknown provider: {provider}"}
                
    except httpx.TimeoutException:
        return {"success": False, "error": "Connection timeout"}
    except httpx.ConnectError:
        return {"success": False, "error": "Connection refused - check if service is running"}
    except Exception as e:
        logger.error(f"Error testing LLM connection: {e}")
        return {"success": False, "error": str(e)}
