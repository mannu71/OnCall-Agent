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

    if name in {"bedrock", "aws_bedrock", "aws", "aws bedrock"}:
        from app.core.transport.bedrock_transport import BedrockTransport
        region  = settings.effective_bedrock_region
        profile = settings.aws_profile
        logger.info("ProviderTransport: using AWS Bedrock (region=%s)", region)
        return BedrockTransport(region=region, profile=profile)

    # Bedrock-only. Fail loudly rather than silently routing generation to a
    # provider this deployment does not use — same policy (and wording) as
    # app.workflow.strategies.react.llm_factory.build_llm.
    raise ValueError(
        f"Unsupported provider '{name}'. This application uses AWS Bedrock "
        "exclusively for generation (plus the local snowflake-arctic-embed-s "
        "model for embeddings). Set PROVIDER_TRANSPORT=bedrock."
    )
