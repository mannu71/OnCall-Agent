"""Model metadata, context lengths, and token estimation utilities.

Pure utility functions with no dependencies on agent state. Used by ContextCompressor
and workflow strategies for pre-flight context checks.

Resolution priority for context length:
1. Explicit configuration override
2. Provider API /models endpoint
3. models.dev lookup
4. Hardcoded defaults (fuzzy match)
5. Default fallback (128K)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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


def window_size_for_model(
    model: str,
    *,
    default: int = 200_000,
    config_context_length: int | None = None,
) -> int:
    """Best-effort context window (tokens) for *model*, for compaction sizing.

    Sizes the compaction budget to the actual model rather than a fixed
    constant, so long-context
    models (e.g. 1M Claude) compact later and small ones (≤128K) compact in time.
    Synchronous + offline (hardcoded table + config override only) so it is safe
    on the hot path; returns *default* for an empty/unknown model.
    """
    if not model:
        return default
    try:
        return get_model_context_length_sync(
            model,
            config_context_length=config_context_length,
            enforce_minimum=False,
        )
    except Exception:  # noqa: BLE001 — sizing must never break the agent
        return default


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
