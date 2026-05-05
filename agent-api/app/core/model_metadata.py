"""Model metadata, context lengths, and token estimation utilities.

Pure utility functions with no dependencies on agent state. Used by ContextCompressor
and workflow strategies for pre-flight context checks.

Resolution priority for context length:
1. Explicit configuration override
2. Cached value (model@base_url)
3. Provider API /models endpoint
4. models.dev lookup
5. Hardcoded defaults (fuzzy match)
6. Default fallback (128K)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Minimum context length required for agent workflows
MINIMUM_CONTEXT_LENGTH = 64_000

# Default context length when no detection method succeeds
DEFAULT_FALLBACK_CONTEXT = 128_000

# Context probe tiers for step-down on errors
CONTEXT_PROBE_TIERS = [
    128_000,
    64_000,
    32_000,
    16_000,
    8_000,
]

# Hardcoded context length defaults for common models
DEFAULT_CONTEXT_LENGTHS = {
    # Anthropic Claude
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-opus-4.6": 1_000_000,
    "claude-sonnet-4.6": 1_000_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude": 200_000,
    # OpenAI
    "gpt-5.4": 1_050_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.4-nano": 400_000,
    "gpt-5.1": 128_000,
    "gpt-5": 400_000,
    "gpt-4.1": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    # Google
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.0": 1_048_576,
    "gemini-1.5": 1_048_576,
    "gemini": 1_048_576,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-coder": 64_000,
    "deepseek": 128_000,
    # Meta
    "llama-3": 131_072,
    "llama": 131_072,
    # Qwen
    "qwen3": 1_000_000,
    "qwen2.5": 131_072,
    "qwen": 131_072,
    # Mistral
    "mistral-large": 128_000,
    "mistral": 128_000,
    # Bedrock models
    "anthropic.claude-3-opus": 200_000,
    "anthropic.claude-3-sonnet": 200_000,
    "anthropic.claude-3-haiku": 200_000,
    "amazon.nova-pro": 300_000,
    "amazon.nova": 300_000,
    "amazon.titan": 8_192,
    "meta.llama": 8_192,
}

# Provider names that can appear as a prefix
_PROVIDER_PREFIXES: frozenset[str] = frozenset({
    "openrouter", "nous", "openai-codex", "copilot",
    "gemini", "zai", "kimi-coding", "kimi-coding-cn", "minimax",
    "anthropic", "deepseek", "alibaba", "xiaomi", "arcee",
    "custom", "local", "bedrock", "google", "glm", "moonshot", "claude",
    "qwen", "mimo", "opencode", "kilocode", "ai-gateway",
})

# Local server detection patterns
_LOCAL_SERVER_PATTERNS = {
    "ollama": [
        "localhost:11434",
        "127.0.0.1:11434",
        ":11434",
    ],
    "lm-studio": [
        "localhost:1234",
        "127.0.0.1:1234",
        ":1234",
    ],
    "vllm": [
        "/v1/completions",
        "vllm",
    ],
    "llamacpp": [
        ":8080",
        "llama.cpp",
        "llamacpp",
    ],
}

# models.dev API endpoint
_MODELS_DEV_URL = "https://models.dev/api.json"

# HTTP timeout for API queries
_HTTP_TIMEOUT = 5.0


def _strip_provider_prefix(model: str) -> str:
    """Strip a recognised provider prefix from a model string.

    Examples:
        "local:my-model" -> "my-model"
        "bedrock:anthropic.claude-3-opus" -> "anthropic.claude-3-opus"
        "qwen3.5:27b" -> "qwen3.5:27b" (unchanged - not a provider prefix)
    """
    if ":" not in model:
        return model
    prefix, suffix = model.split(":", 1)
    prefix_lower = prefix.strip().lower()
    if prefix_lower in _PROVIDER_PREFIXES:
        return suffix
    return model


def _make_cache_key(model: str, base_url: str) -> str:
    """Create a cache key from model and base_url.

    Args:
        model: Model identifier
        base_url: API base URL

    Returns:
        Cache key in format "model@base_url" or just "model" if no base_url
    """
    model = _strip_provider_prefix(model)
    if base_url:
        # Normalize URL by removing trailing slash and protocol
        base_url = base_url.rstrip("/")
        return f"{model}@{base_url}"
    return model


def detect_local_server_type(base_url: str) -> Optional[str]:
    """Detect local inference server type from base URL.

    Examines the base URL to determine if it's a known local inference
    server like Ollama, LM Studio, vLLM, or llama.cpp.

    Args:
        base_url: The API base URL to check

    Returns:
        Server type string ("ollama", "lm-studio", "vllm", "llamacpp")
        or None if not a recognized local server
    """
    if not base_url:
        return None

    base_url_lower = base_url.lower()

    for server_type, patterns in _LOCAL_SERVER_PATTERNS.items():
        for pattern in patterns:
            if pattern in base_url_lower:
                return server_type

    # Check for localhost/127.0.0.1 without known port
    if "localhost" in base_url_lower or "127.0.0.1" in base_url_lower:
        return "local"

    return None


async def query_ollama_num_ctx(model: str, base_url: str) -> Optional[int]:
    """Query Ollama server for a model's context length (num_ctx).

    Ollama stores the context length in the model's modelfile as num_ctx.
    This function queries the Ollama API to retrieve this value.

    Args:
        model: The model name as known to Ollama
        base_url: The Ollama server URL (e.g., http://localhost:11434)

    Returns:
        The context length (num_ctx) if found, or None
    """
    if not base_url:
        base_url = "http://localhost:11434"

    # Normalize base URL
    base_url = base_url.rstrip("/")

    # Strip any provider prefix from model name
    model = _strip_provider_prefix(model)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            # Try to get model info from Ollama API
            response = await client.get(f"{base_url}/api/show", params={"name": model})
            if response.status_code == 200:
                data = response.json()
                # Check modelfile for num_ctx parameter
                modelfile = data.get("modelfile", "")
                if modelfile:
                    # Parse num_ctx from modelfile
                    match = re.search(r"num_ctx\s+(\d+)", modelfile)
                    if match:
                        return int(match.group(1))

                # Check parameters dict
                parameters = data.get("parameters", {})
                if "num_ctx" in parameters:
                    return int(parameters["num_ctx"])

                # Check model_info for context_length
                model_info = data.get("model_info", {})
                if "context_length" in model_info:
                    return int(model_info["context_length"])

    except Exception as e:
        logger.debug(f"Failed to query Ollama for num_ctx: {e}")

    return None


async def query_provider_models_endpoint(base_url: str, model: str, api_key: str = "") -> Optional[int]:
    """Query provider's /models endpoint for context length.

    Many providers expose model metadata including context length via
    the OpenAI-compatible /v1/models endpoint.

    Args:
        base_url: The API base URL
        model: The model identifier
        api_key: Optional API key for authentication

    Returns:
        Context length if found, or None
    """
    if not base_url:
        return None

    base_url = base_url.rstrip("/")
    model = _strip_provider_prefix(model)

    # Try OpenAI-compatible /models endpoint
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            # Try /v1/models first (OpenAI-compatible)
            response = await client.get(
                f"{base_url}/v1/models",
                headers=headers if headers else None
            )
            if response.status_code == 200:
                data = response.json()
                models = data.get("data", [])
                for m in models:
                    if m.get("id", "").lower() == model.lower():
                        # Check for context_length in model metadata
                        if "context_length" in m:
                            return int(m["context_length"])
                        if "max_context_length" in m:
                            return int(m["max_context_length"])

            # Try /models without v1 prefix
            response = await client.get(
                f"{base_url}/models",
                headers=headers if headers else None
            )
            if response.status_code == 200:
                data = response.json()
                models = data.get("data", data.get("models", []))
                for m in models if isinstance(models, list) else [models]:
                    if isinstance(m, dict):
                        if m.get("id", m.get("name", "")).lower() == model.lower():
                            for key in ["context_length", "max_context_length", "context_window"]:
                                if key in m:
                                    return int(m[key])

    except Exception as e:
        logger.debug(f"Failed to query provider models endpoint: {e}")

    return None


async def query_models_dev(model: str) -> Optional[int]:
    """Query models.dev API for model context length.

    models.dev is a community-maintained registry of model metadata
    including context lengths.

    Args:
        model: The model identifier

    Returns:
        Context length if found, or None
    """
    model = _strip_provider_prefix(model)
    model_lower = model.lower()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(_MODELS_DEV_URL)
            if response.status_code == 200:
                data = response.json()

                # Navigate the models.dev API structure
                # Structure: {provider: {model_id: {context_length: N}}}
                for provider, models in data.items():
                    if isinstance(models, dict):
                        for model_id, model_data in models.items():
                            if isinstance(model_data, dict):
                                # Check if model ID matches
                                if model_id.lower() == model_lower or model_lower in model_id.lower():
                                    for key in ["context_length", "context_window", "max_context_length"]:
                                        if key in model_data:
                                            return int(model_data[key])

    except Exception as e:
        logger.debug(f"Failed to query models.dev: {e}")

    return None


async def get_cached_context_length(
    model: str,
    base_url: str,
    db_session: Optional["AsyncSession"] = None,
) -> Optional[int]:
    """Look up previously discovered context length from cache.

    Args:
        model: The model identifier
        base_url: The API base URL
        db_session: Optional database session for async operations

    Returns:
        Cached context length if found, or None
    """
    if db_session is None:
        return None

    from app.models.db_models import ContextLengthCacheModel
    from sqlalchemy import select

    cache_key = _make_cache_key(model, base_url)

    try:
        stmt = select(ContextLengthCacheModel).where(
            ContextLengthCacheModel.model_provider_key == cache_key
        )
        result = await db_session.execute(stmt)
        cached = result.scalar_one_or_none()

        if cached:
            return cached.context_length

    except Exception as e:
        logger.debug(f"Failed to get cached context length: {e}")

    return None


async def save_context_length(
    model: str,
    base_url: str,
    length: int,
    db_session: Optional["AsyncSession"] = None,
) -> bool:
    """Persist discovered context length to cache.

    Args:
        model: The model identifier
        base_url: The API base URL
        length: The context length to cache
        db_session: Optional database session for async operations

    Returns:
        True if saved successfully, False otherwise
    """
    if db_session is None:
        return False

    from app.models.db_models import ContextLengthCacheModel
    from sqlalchemy.dialects.postgresql import insert

    cache_key = _make_cache_key(model, base_url)

    try:
        # Use upsert to handle existing entries
        stmt = insert(ContextLengthCacheModel).values(
            model_provider_key=cache_key,
            context_length=length,
            discovered_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["model_provider_key"],
            set_={
                "context_length": length,
                "discovered_at": datetime.now(timezone.utc),
            }
        )
        await db_session.execute(stmt)
        return True

    except Exception as e:
        logger.warning(f"Failed to save context length to cache: {e}")
        return False


def _get_hardcoded_context_length(model: str) -> Optional[int]:
    """Get context length from hardcoded defaults using fuzzy match.

    Args:
        model: The model identifier

    Returns:
        Context length if found, or None
    """
    model = _strip_provider_prefix(model)
    model_lower = model.lower()

    # Longest match first for specificity
    for default_model, length in sorted(
        DEFAULT_CONTEXT_LENGTHS.items(), key=lambda x: len(x[0]), reverse=True
    ):
        if default_model in model_lower:
            return length

    return None


async def get_model_context_length(
    model: str,
    base_url: str = "",
    api_key: str = "",
    config_context_length: int | None = None,
    provider: str = "",
    db_session: Optional["AsyncSession"] = None,
    enforce_minimum: bool = True,
) -> int:
    """Resolve context length from multiple sources.

    Resolution priority:
    1. Explicit config override
    2. Cached value (model@base_url)
    3. Provider API /models endpoint
    4. models.dev lookup
    5. Hardcoded defaults (fuzzy match)
    6. Default fallback (128K)

    Args:
        model: The model identifier
        base_url: Optional API base URL
        api_key: Optional API key for provider queries
        config_context_length: Optional explicit config override
        provider: Optional provider name hint
        db_session: Optional database session for cache operations
        enforce_minimum: If True, enforce MINIMUM_CONTEXT_LENGTH (64K) for agent workflows

    Returns:
        Context length in tokens

    Raises:
        ValueError: If enforce_minimum is True and resolved context length is below minimum
    """
    # 1. Explicit config override
    if config_context_length is not None and isinstance(config_context_length, int) and config_context_length > 0:
        logger.debug(f"Using config override for context length: {config_context_length}")
        resolved = config_context_length
    else:
        # 2. Cached value
        cached = await get_cached_context_length(model, base_url, db_session)
        if cached is not None:
            logger.debug(f"Using cached context length for {model}: {cached}")
            resolved = cached
        else:
            # 3. Check for local server and query appropriately
            server_type = detect_local_server_type(base_url)

            if server_type == "ollama":
                # Query Ollama-specific API
                ollama_ctx = await query_ollama_num_ctx(model, base_url)
                if ollama_ctx:
                    logger.debug(f"Got context length from Ollama for {model}: {ollama_ctx}")
                    await save_context_length(model, base_url, ollama_ctx, db_session)
                    resolved = ollama_ctx
                else:
                    resolved = _resolve_fallback_context_length(model)
            else:
                # 4. Provider API /models endpoint
                provider_ctx = await query_provider_models_endpoint(base_url, model, api_key)
                if provider_ctx:
                    logger.debug(f"Got context length from provider API for {model}: {provider_ctx}")
                    await save_context_length(model, base_url, provider_ctx, db_session)
                    resolved = provider_ctx
                else:
                    # 5. models.dev lookup
                    models_dev_ctx = await query_models_dev(model)
                    if models_dev_ctx:
                        logger.debug(f"Got context length from models.dev for {model}: {models_dev_ctx}")
                        await save_context_length(model, base_url, models_dev_ctx, db_session)
                        resolved = models_dev_ctx
                    else:
                        resolved = _resolve_fallback_context_length(model)

    # Validate minimum context length for agent workflows
    if enforce_minimum and resolved < MINIMUM_CONTEXT_LENGTH:
        logger.warning(
            f"Context length {resolved} for {model} is below minimum {MINIMUM_CONTEXT_LENGTH}. "
            f"This may cause issues with agent workflows. Consider using a model with larger context."
        )

    return resolved


def _resolve_fallback_context_length(model: str) -> int:
    """Resolve context length from hardcoded defaults or fallback.

    Args:
        model: The model identifier

    Returns:
        Context length in tokens
    """
    # 6. Hardcoded defaults
    hardcoded_ctx = _get_hardcoded_context_length(model)
    if hardcoded_ctx:
        logger.debug(f"Using hardcoded context length for {model}: {hardcoded_ctx}")
        return hardcoded_ctx

    # 7. Default fallback
    logger.debug(f"Using default fallback context length for {model}: {DEFAULT_FALLBACK_CONTEXT}")
    return DEFAULT_FALLBACK_CONTEXT


def get_model_context_length_sync(
    model: str,
    base_url: str = "",
    config_context_length: int | None = None,
    enforce_minimum: bool = True,
) -> int:
    """Synchronous version of get_model_context_length for backward compatibility.

    This version only uses config override and hardcoded defaults.
    For full resolution with caching and API queries, use the async version.

    Args:
        model: The model identifier
        base_url: Optional API base URL
        config_context_length: Optional explicit config override
        enforce_minimum: If True, enforce MINIMUM_CONTEXT_LENGTH (64K) for agent workflows

    Returns:
        Context length in tokens
    """
    # 1. Explicit config override
    if config_context_length is not None and isinstance(config_context_length, int) and config_context_length > 0:
        resolved = config_context_length
    else:
        # 2. Hardcoded defaults
        hardcoded_ctx = _get_hardcoded_context_length(model)
        if hardcoded_ctx:
            resolved = hardcoded_ctx
        else:
            # 3. Default fallback
            resolved = DEFAULT_FALLBACK_CONTEXT

    # Validate minimum context length for agent workflows
    if enforce_minimum and resolved < MINIMUM_CONTEXT_LENGTH:
        logger.warning(
            f"Context length {resolved} for {model} is below minimum {MINIMUM_CONTEXT_LENGTH}. "
            f"This may cause issues with agent workflows. Consider using a model with larger context."
        )

    return resolved


def estimate_tokens_rough(text: str) -> int:
    """Rough token estimate (~4 chars/token) for pre-flight checks.

    Uses ceiling division so short texts (1-3 chars) never estimate as
    0 tokens, which would cause systematic undercounting.

    Args:
        text: The text to estimate

    Returns:
        Estimated token count
    """
    if not text:
        return 0
    return (len(text) + 3) // 4


def estimate_messages_tokens_rough(messages: List[Dict[str, Any]]) -> int:
    """Rough token estimate for a message list (pre-flight only).

    Args:
        messages: List of message dictionaries

    Returns:
        Estimated token count
    """
    total_chars = sum(len(str(msg)) for msg in messages)
    return (total_chars + 3) // 4


def estimate_request_tokens_rough(
    messages: List[Dict[str, Any]],
    *,
    system_prompt: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Rough token estimate for a full chat-completions request.

    Includes system prompt, conversation messages, and tool schemas.
    With many tools enabled, schemas alone can add 20-30K tokens.

    Args:
        messages: Conversation messages
        system_prompt: System prompt text
        tools: Optional tool definitions

    Returns:
        Estimated token count
    """
    total_chars = 0
    if system_prompt:
        total_chars += len(system_prompt)
    if messages:
        total_chars += sum(len(str(msg)) for msg in messages)
    if tools:
        total_chars += len(str(tools))
    return (total_chars + 3) // 4


def get_next_probe_tier(current_length: int) -> Optional[int]:
    """Return the next lower probe tier, or None if already at minimum.

    Args:
        current_length: Current context length

    Returns:
        Next lower tier or None
    """
    for tier in CONTEXT_PROBE_TIERS:
        if tier < current_length:
            return tier
    return None


def resolve_context_length_with_probing(
    error: Exception,
    current_context_length: int,
    model: str = "",
    base_url: str = "",
) -> Optional[int]:
    """Resolve a new context length from an error, using probing tiers as fallback.

    This function is designed to be called from error recovery logic when
    a context overflow error occurs. It attempts to:
    1. Parse the actual context limit from the error message
    2. Fall back to the next probe tier if parsing fails
    3. Ensure the result is at least MINIMUM_CONTEXT_LENGTH

    Args:
        error: The exception that occurred (typically context overflow)
        current_context_length: The context length that was being used
        model: Optional model identifier for logging
        base_url: Optional base URL for logging

    Returns:
        New context length to try, or None if no lower tier is available
    """
    # First, try to parse the actual limit from the error message
    error_msg = str(error)
    parsed_limit = parse_context_limit_from_error(error_msg)

    if parsed_limit is not None:
        # Use the parsed limit, but ensure it's lower than current
        if parsed_limit < current_context_length:
            logger.info(
                f"Parsed context limit {parsed_limit} from error for {model}. "
                f"Using this as new context length."
            )
            # Ensure minimum
            if parsed_limit < MINIMUM_CONTEXT_LENGTH:
                logger.warning(
                    f"Parsed limit {parsed_limit} is below minimum {MINIMUM_CONTEXT_LENGTH}. "
                    f"Using minimum instead."
                )
                return MINIMUM_CONTEXT_LENGTH if MINIMUM_CONTEXT_LENGTH < current_context_length else None
            return parsed_limit

    # Fall back to probe tiers
    next_tier = get_next_probe_tier(current_context_length)

    if next_tier is not None:
        logger.info(
            f"Stepping down context length for {model} from {current_context_length} to {next_tier}"
        )
        # Ensure minimum
        if next_tier < MINIMUM_CONTEXT_LENGTH:
            logger.warning(
                f"Next probe tier {next_tier} is below minimum {MINIMUM_CONTEXT_LENGTH}. "
                f"Using minimum instead."
            )
            return MINIMUM_CONTEXT_LENGTH if MINIMUM_CONTEXT_LENGTH < current_context_length else None
        return next_tier

    logger.warning(
        f"No lower context length tier available for {model}. "
        f"Current: {current_context_length}, minimum: {MINIMUM_CONTEXT_LENGTH}"
    )
    return None


def parse_context_limit_from_error(error_msg: str) -> Optional[int]:
    """Try to extract the actual context limit from an API error message.

    Many providers include the limit in their error text, e.g.:
    - "maximum context length is 32768 tokens"
    - "context_length_exceeded: 131072"
    - "Maximum context size 32768 exceeded"

    Args:
        error_msg: The error message text

    Returns:
        Parsed context limit or None
    """
    error_lower = error_msg.lower()
    patterns = [
        r'(?:max(?:imum)?|limit)\s*(?:context\s*)?(?:length|size|window)?\s*(?:is|of|:)?\s*(\d{4,})',
        r'context\s*(?:length|size|window)\s*(?:is|of|:)?\s*(\d{4,})',
        r'(\d{4,})\s*(?:token)?\s*(?:context|limit)',
        r'>\s*(\d{4,})\s*(?:max|limit|token)',
        r'(\d{4,})\s*(?:max(?:imum)?)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, error_lower)
        if match:
            limit = int(match.group(1))
            if 1024 <= limit <= 10_000_000:
                return limit
    return None


def parse_available_output_tokens_from_error(error_msg: str) -> Optional[int]:
    """Detect a "max_tokens too large" error and return available output tokens.

    Different from "prompt too long" - this is when input + requested_output
    exceeds the window. The fix is to reduce max_tokens, not compress context.

    Args:
        error_msg: The error message text

    Returns:
        Available output tokens or None
    """
    error_lower = error_msg.lower()

    # Must look like an output-cap error, not a prompt-length error
    is_output_cap_error = (
        "max_tokens" in error_lower
        and ("available_tokens" in error_lower or "available tokens" in error_lower)
    )
    if not is_output_cap_error:
        return None

    patterns = [
        r'available_tokens[:\s]+(\d+)',
        r'available\s+tokens[:\s]+(\d+)',
        r'=\s*(\d+)\s*$',
    ]
    for pattern in patterns:
        match = re.search(pattern, error_lower)
        if match:
            tokens = int(match.group(1))
            if tokens >= 1:
                return tokens
    return None


def is_context_overflow_error(error: Exception) -> bool:
    """Check if an error indicates context overflow.

    Args:
        error: The exception to check

    Returns:
        True if context overflow error
    """
    error_msg = str(error).lower()
    error_type = type(error).__name__.lower()

    # Direct error type checks
    if "context" in error_type or "token" in error_type:
        return True

    # Message pattern checks
    context_patterns = [
        "context length",
        "context size",
        "maximum context",
        "token limit",
        "too many tokens",
        "reduce the length",
        "exceeds the limit",
        "context window",
        "prompt is too long",
        "prompt exceeds max length",
        "max_tokens",
        "maximum number of tokens",
        "max_model_len",
        "prompt length",
        "input is too long",
        "truncating input",
    ]

    return any(pattern in error_msg for pattern in context_patterns)
