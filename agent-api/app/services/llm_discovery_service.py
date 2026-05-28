"""LLM Discovery Service — provider-specific model discovery and connection testing.

Moved from ``app.domain.services.llm_discovery_service`` during the DDD-scaffolding
collapse (plan §1.2, option B). Public class name (``LLMDiscoveryService``) is
unchanged so any future caller can import it from the new path
``app.services.llm_discovery_service``.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import boto3
import httpx
from botocore.exceptions import ClientError as BotoClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ICON_MAP: Dict[str, str] = {
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


def _pick_icon(model_id: str) -> str:
    model_lower = model_id.lower()
    return next((v for k, v in _ICON_MAP.items() if k in model_lower), "🤖")


def _ssl_verify() -> bool:
    return os.environ.get("AWS_SSL_VERIFY", "true").lower() not in ("false", "0", "no")


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class LLMDiscoveryService:
    """Encapsulates provider-specific model discovery and connection testing.

    Methods that require persistence (checking whether a model already exists in the
    database) accept an ``already_exists`` callback with signature::

        async def already_exists(name: str) -> bool: ...
    """

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover_models(
        self,
        provider: str,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Discover available models for *provider* using the provided credentials."""
        handler = self._discovery_handler(provider.lower())
        return await handler(credentials, already_exists)

    def _discovery_handler(self, provider_lower: str):
        _handlers = {
            "bedrock": self._discover_bedrock,
            "aws bedrock": self._discover_bedrock,
            "openai": self._discover_openai,
            "anthropic": self._discover_anthropic,
            "google": self._discover_google,
            "groq": self._discover_groq,
            "azure openai": self._discover_azure_openai,
            "ollama": self._discover_ollama,
        }
        if provider_lower not in _handlers:
            raise ValueError(f"Unsupported provider: {provider_lower}")
        return _handlers[provider_lower]

    # -- Bedrock --

    async def _discover_bedrock(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        region = credentials.get("region") or "us-east-1"
        access_key_id = credentials.get("access_key_id")
        secret_access_key = credentials.get("secret_access_key")
        session_token = credentials.get("session_token")

        kwargs: Dict[str, Any] = {"region_name": region}
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
            if session_token:
                kwargs["aws_session_token"] = session_token
        if os.environ.get("AWS_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
            from botocore.config import Config as BotoConfig

            kwargs["config"] = BotoConfig(retries={"max_attempts": 3})
            kwargs["verify"] = False

        client = boto3.client("bedrock", **kwargs)
        response = await asyncio.get_running_loop().run_in_executor(
            None, client.list_foundation_models
        )
        models = response.get("modelSummaries", [])

        discovered: List[Dict[str, Any]] = []
        for m in models:
            model_id = m.get("modelId", "")
            provider_name = m.get("providerName", "")
            model_name = m.get("modelName", "")
            if not model_id:
                continue
            display_name = model_name or (model_id.split(".")[-1] if "." in model_id else model_id)
            exists = await already_exists(display_name) if already_exists else False
            discovered.append(
                {
                    "name": display_name,
                    "model": model_id,
                    "provider": "AWS Bedrock",
                    "region": region,
                    "icon": _pick_icon(model_id),
                    "description": f"{provider_name} - {model_id}" if provider_name else model_id,
                    "already_exists": exists,
                }
            )

        logger.info("Discovered %d Bedrock models in %s", len(discovered), region)
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "AWS Bedrock",
            "models": discovered,
        }

    # -- OpenAI --

    async def _discover_openai(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        api_key = credentials.get("api_key")
        if not api_key:
            raise ValueError("Missing API key for OpenAI")

        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=30.0, verify=ssl) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        discovered: List[Dict[str, Any]] = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            if not model_id:
                continue
            exists = await already_exists(model_id) if already_exists else False
            discovered.append(
                {
                    "name": model_id,
                    "model": model_id,
                    "provider": "OpenAI",
                    "icon": _pick_icon(model_id),
                    "description": model_id,
                    "already_exists": exists,
                }
            )

        discovered.sort(key=lambda x: x["name"])
        logger.info("Discovered %d OpenAI models", len(discovered))
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "OpenAI",
            "models": discovered,
        }

    # -- Anthropic --

    async def _discover_anthropic(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        api_key = credentials.get("api_key")
        if not api_key:
            raise ValueError("Missing API key for Anthropic")

        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=30.0, verify=ssl) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        discovered: List[Dict[str, Any]] = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            display_name = m.get("display_name", model_id)
            if not model_id:
                continue
            exists = await already_exists(display_name) if already_exists else False
            discovered.append(
                {
                    "name": display_name,
                    "model": model_id,
                    "provider": "Anthropic",
                    "icon": _pick_icon(model_id),
                    "description": display_name,
                    "already_exists": exists,
                }
            )

        discovered.sort(key=lambda x: x["name"])
        logger.info("Discovered %d Anthropic models", len(discovered))
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "Anthropic",
            "models": discovered,
        }

    # -- Google --

    async def _discover_google(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        api_key = credentials.get("api_key")
        if not api_key:
            raise ValueError("Missing API key for Google")

        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=30.0, verify=ssl) as client:
            resp = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            )
            resp.raise_for_status()
            data = resp.json()

        discovered: List[Dict[str, Any]] = []
        for m in data.get("models", []):
            model_id = m.get("name", "").replace("models/", "")
            display_name = m.get("displayName", model_id)
            if not model_id:
                continue
            exists = await already_exists(display_name) if already_exists else False
            discovered.append(
                {
                    "name": display_name,
                    "model": model_id,
                    "provider": "Google",
                    "icon": _pick_icon(model_id),
                    "description": m.get("description", display_name),
                    "already_exists": exists,
                }
            )

        discovered.sort(key=lambda x: x["name"])
        logger.info("Discovered %d Google models", len(discovered))
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "Google",
            "models": discovered,
        }

    # -- Groq --

    async def _discover_groq(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        api_key = credentials.get("api_key")
        if not api_key:
            raise ValueError("Missing API key for Groq")

        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=30.0, verify=ssl) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        discovered: List[Dict[str, Any]] = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            if not model_id:
                continue
            exists = await already_exists(model_id) if already_exists else False
            discovered.append(
                {
                    "name": model_id,
                    "model": model_id,
                    "provider": "Groq",
                    "icon": _pick_icon(model_id),
                    "description": model_id,
                    "already_exists": exists,
                }
            )

        discovered.sort(key=lambda x: x["name"])
        logger.info("Discovered %d Groq models", len(discovered))
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "Groq",
            "models": discovered,
        }

    # -- Azure OpenAI --

    async def _discover_azure_openai(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        api_key = credentials.get("api_key")
        endpoint = credentials.get("endpoint")
        if not api_key:
            raise ValueError("Missing API key for Azure OpenAI")
        if not endpoint:
            raise ValueError("Missing endpoint for Azure OpenAI")

        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=30.0, verify=ssl) as client:
            resp = await client.get(
                f"{endpoint}/openai/models?api-version=2024-02-01",
                headers={"api-key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()

        discovered: List[Dict[str, Any]] = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            if not model_id:
                continue
            exists = await already_exists(model_id) if already_exists else False
            discovered.append(
                {
                    "name": model_id,
                    "model": model_id,
                    "provider": "Azure OpenAI",
                    "endpoint": endpoint,
                    "icon": _pick_icon(model_id),
                    "description": f"{model_id} @ {endpoint}",
                    "already_exists": exists,
                }
            )

        discovered.sort(key=lambda x: x["name"])
        logger.info("Discovered %d Azure OpenAI models", len(discovered))
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "Azure OpenAI",
            "models": discovered,
        }

    # -- Ollama --

    async def _discover_ollama(
        self,
        credentials: Dict[str, Any],
        already_exists: Optional[Any] = None,
    ) -> Dict[str, Any]:
        ollama_url = credentials.get("endpoint") or "http://localhost:11434"
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=30.0, verify=ssl) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()

        discovered: List[Dict[str, Any]] = []
        for m in data.get("models", []):
            model_id = m.get("name", "")
            if not model_id:
                continue
            exists = await already_exists(model_id) if already_exists else False
            discovered.append(
                {
                    "name": model_id,
                    "model": model_id,
                    "provider": "Ollama",
                    # Bug fix during move: original file had typo ``oollama_url``.
                    "baseUrl": ollama_url,
                    "icon": _pick_icon(model_id),
                    "description": f"{model_id} ({m.get('size', '')})" if m.get("size") else model_id,
                    "already_exists": exists,
                }
            )

        discovered.sort(key=lambda x: x["name"])
        logger.info("Discovered %d Ollama models", len(discovered))
        return {
            "success": True,
            "discovered": len(discovered),
            "provider": "Ollama",
            "models": discovered,
        }

    # ------------------------------------------------------------------
    # Connection testing
    # ------------------------------------------------------------------

    async def test_connection(self, provider: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Test connectivity to *provider* using the configuration dict."""
        handler = self._test_handler(provider)
        try:
            return await handler(config)
        except httpx.TimeoutException:
            return {"success": False, "error": "Connection timeout"}
        except httpx.ConnectError:
            return {
                "success": False,
                "error": "Connection refused - check if service is running",
            }
        except Exception as exc:
            logger.error("Error testing LLM connection: %s", exc)
            return {"success": False, "error": str(exc)}

    def _test_handler(self, provider: str):
        handlers = {
            "openai": self._test_openai,
            "anthropic": self._test_anthropic,
            "groq": self._test_groq,
            "google": self._test_google,
            "azure openai": self._test_azure_openai,
            "ollama": self._test_ollama,
            "aws bedrock": self._test_bedrock,
            "bedrock": self._test_bedrock,
        }
        key = provider.lower()
        if key not in handlers:
            raise ValueError(f"Unsupported provider: {provider}")
        return handlers[key]

    async def _test_openai(self, config: Dict[str, Any]) -> Dict[str, Any]:
        api_key = config.get("api_key")
        if not api_key:
            return {"success": False, "error": "No API key configured"}
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=10.0, verify=ssl) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.is_success:
                return {"success": True, "message": "Connected to OpenAI"}
            return {"success": False, "error": f"HTTP {resp.status_code}"}

    async def _test_anthropic(self, config: Dict[str, Any]) -> Dict[str, Any]:
        api_key = config.get("api_key")
        model = config.get("model")
        if not api_key:
            return {"success": False, "error": "No API key configured"}
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=10.0, verify=ssl) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model or "claude-3-5-sonnet-20241022",
                    "max_tokens": 1,
                    "messages": [],
                },
            )
            if resp.is_success or resp.status_code == 400:
                return {"success": True, "message": "Connected to Anthropic"}
            return {"success": False, "error": f"HTTP {resp.status_code}"}

    async def _test_groq(self, config: Dict[str, Any]) -> Dict[str, Any]:
        api_key = config.get("api_key")
        if not api_key:
            return {"success": False, "error": "No API key configured"}
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=10.0, verify=ssl) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.is_success:
                return {"success": True, "message": "Connected to Groq"}
            return {"success": False, "error": f"HTTP {resp.status_code}"}

    async def _test_google(self, config: Dict[str, Any]) -> Dict[str, Any]:
        api_key = config.get("api_key")
        if not api_key:
            return {"success": False, "error": "No API key configured"}
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=10.0, verify=ssl) as client:
            resp = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            )
            if resp.is_success:
                return {"success": True, "message": "Connected to Google"}
            return {"success": False, "error": f"HTTP {resp.status_code}"}

    async def _test_azure_openai(self, config: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = config.get("endpoint")
        api_key = config.get("api_key")
        if not endpoint:
            return {"success": False, "error": "Azure endpoint URL is required"}
        if not api_key:
            return {"success": False, "error": "No API key configured"}
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=10.0, verify=ssl) as client:
            resp = await client.get(
                f"{endpoint}/openai/models?api-version=2024-02-01",
                headers={"api-key": api_key},
            )
            if resp.is_success:
                return {"success": True, "message": "Connected to Azure OpenAI"}
            return {"success": False, "error": f"HTTP {resp.status_code}"}

    async def _test_ollama(self, config: Dict[str, Any]) -> Dict[str, Any]:
        base_url = config.get("base_url") or config.get("baseUrl") or "http://localhost:11434"
        ssl = _ssl_verify()
        async with httpx.AsyncClient(timeout=10.0, verify=ssl) as client:
            resp = await client.get(f"{base_url}/api/tags")
            if resp.is_success:
                return {"success": True, "message": "Connected to Ollama"}
            return {"success": False, "error": f"HTTP {resp.status_code}"}

    async def _test_bedrock(self, config: Dict[str, Any]) -> Dict[str, Any]:
        region = config.get("region") or "us-east-1"
        access_key_id = config.get("access_key_id")
        secret_access_key = config.get("secret_access_key")
        session_token = config.get("session_token")

        kwargs: Dict[str, Any] = {"region_name": region}
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
            if session_token:
                kwargs["aws_session_token"] = session_token
        if os.environ.get("AWS_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
            kwargs["verify"] = False

        try:
            bedrock = boto3.client("bedrock", **kwargs)
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: bedrock.list_foundation_models(byProvider="anthropic"),
            )
            return {"success": True, "message": f"Connected to AWS Bedrock ({region})"}
        except BotoClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if "ExpiredToken" in str(exc) or error_code == "ExpiredTokenException":
                return {
                    "success": False,
                    "error": "AWS credentials expired. Please refresh your AWS credentials and try again.",
                }
            if "AccessDenied" in str(exc) or error_code == "AccessDeniedException":
                return {
                    "success": False,
                    "error": "Access denied. Check that your AWS credentials have Bedrock permissions.",
                }
            return {"success": False, "error": f"AWS Bedrock error: {exc}"}
