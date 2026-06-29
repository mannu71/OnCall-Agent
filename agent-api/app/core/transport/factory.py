"""Transport factory — resolves the active provider from PROVIDER_TRANSPORT env var."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from app.config import settings
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
    name = (provider or settings.provider_transport).lower()

    if name == "anthropic":
        from app.core.transport.anthropic_transport import AnthropicTransport
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY") or ""
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY is not set — AnthropicTransport will fail on first call")
        logger.info("ProviderTransport: using Anthropic direct API")
        return AnthropicTransport(api_key=api_key)

    if name in {"bedrock", "aws_bedrock"}:
        from app.core.transport.bedrock_transport import BedrockTransport
        region  = settings.effective_bedrock_region
        profile = settings.aws_profile
        logger.info("ProviderTransport: using AWS Bedrock (region=%s)", region)
        return BedrockTransport(region=region, profile=profile)

    raise ValueError(
        f"Unknown PROVIDER_TRANSPORT value '{name}'. Supported: 'anthropic', 'bedrock'."
    )
