"""Transport factory — resolves the active provider from PROVIDER_TRANSPORT env var."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from app.core.transport.provider import ProviderTransport

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_transport(provider: Optional[str] = None) -> ProviderTransport:
    """Return a cached ProviderTransport instance.

    Args:
        provider: Override provider name. Defaults to PROVIDER_TRANSPORT env var
            (then falls back to "anthropic").

    Returns:
        Concrete ProviderTransport implementation.

    Raises:
        ValueError: For unknown provider names.
    """
    import os
    name = (provider or os.getenv("PROVIDER_TRANSPORT", "anthropic")).lower()

    if name == "anthropic":
        from app.core.transport.anthropic_transport import AnthropicTransport
        api_key = os.getenv("ANTHROPIC_API_KEY") or ""
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY is not set — AnthropicTransport will fail on first call")
        logger.info("ProviderTransport: using Anthropic direct API")
        return AnthropicTransport(api_key=api_key)

    if name in {"bedrock", "aws_bedrock"}:
        from app.core.transport.bedrock_transport import BedrockTransport
        region  = os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1"))
        profile = os.getenv("AWS_PROFILE")
        logger.info("ProviderTransport: using AWS Bedrock (region=%s)", region)
        return BedrockTransport(region=region, profile=profile)

    raise ValueError(
        f"Unknown PROVIDER_TRANSPORT value '{name}'. Supported: 'anthropic', 'bedrock'."
    )
