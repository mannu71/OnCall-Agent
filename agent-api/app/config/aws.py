"""AWS session/credentials and Bedrock model routing.

One slice of :class:`app.config.Settings`. Mixins carry no behaviour of
their own — they exist so 200+ fields are readable in domain-sized files.
Settings inherits every one of them, so the flat ``settings.<field>``
surface every call site already uses is unchanged.
"""
from __future__ import annotations

import os
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from app.config._shared import DEFAULT_DELEGATION_BLOCKED_TOOLS  # noqa: F401


class AwsSettings(BaseSettings):
    """AWS session/credentials and Bedrock model routing."""

    # AWS
    aws_ssl_verify: bool = True

    aws_ca_bundle: Optional[str] = None

    aws_region: str = "us-east-1"

    bedrock_region: Optional[str] = None

    aws_profile: Optional[str] = None

    # Thread pools
    aws_thread_pool_size: int = 16

    # Agent / tools / output limits
    # Bedrock-only: this is the sole generative provider (see
    # app.workflow.strategies.react.llm_factory, which rejects anything else).
    # The default must match that policy — docker-compose sets
    # PROVIDER_TRANSPORT=bedrock, but anything running outside compose (tests,
    # evals on the host, a fresh deploy) falls back to this value.
    provider_transport: str = "bedrock"

    # Bedrock client read timeout (seconds). Botocore's 60s default is too short
    # for large-context synthesis calls (200K+ tokens, including the forced
    # recovery synthesis after a recursion-limit hit), which raised
    # "Read timeout on endpoint URL …/converse" and failed the run.
    # Override via BEDROCK_READ_TIMEOUT_SECONDS.
    bedrock_read_timeout_seconds: int = Field(default=300, validation_alias="BEDROCK_READ_TIMEOUT_SECONDS")

    # botocore HTTP connection-pool size for the shared Bedrock runtime client.
    # Botocore's default is 10; one client serves parallel tool supersteps +
    # delegate_parallel children, so at 10 concurrent LLM calls queue on the
    # pool. Baked at 20 to give headroom for parallel tool fan-out plus the
    # delegation_max_concurrent (=3) children without queueing. Raise further
    # (e.g. 50) for very wide fan-outs. Override via env.
    bedrock_max_pool_connections: int = Field(default=20, validation_alias="BEDROCK_MAX_POOL_CONNECTIONS")

    # Opt into Bedrock latency-optimized inference (performance_config={"latency":
    # "optimized"}). Support is model/region-dependent, so this is off by default
    # and left per-deployment opt-in. Override via env.
    bedrock_latency_optimized: bool = Field(default=False, validation_alias="BEDROCK_LATENCY_OPTIMIZED")

    # ── Bedrock fallback-chain routing ───────────────────────────────────────
    # On a Bedrock ThrottlingException the runner walks a fallback chain
    # (alt credential → alt region → fallback model) instead of hammering the
    # same throttled target. Set False to restore the old single-target retry.
    routing_fallback_enabled: bool = Field(
        default=True, validation_alias="ROUTING_FALLBACK_ENABLED"
    )

    # Concrete AWS regions to fail over to, in order. Each must host the
    # cross-region inference profile for the model; build_llm derives the
    # us./eu./ap. prefix from the region. Comma-separated via env.
    # Defaults to US regions only — most deployments' Bedrock credentials are
    # US-scoped, and same-geography inference profiles avoid cross-region auth
    # failures. Add eu-*/ap-* via env when the credentials have that access.
    bedrock_fallback_regions: List[str] = Field(
        default_factory=lambda: ["us-east-1", "us-west-2"],
        validation_alias="BEDROCK_FALLBACK_REGIONS",
    )

    # Bedrock model IDs to fall back to (e.g. Sonnet → Haiku) after region
    # failover is exhausted. Bare foundation-model IDs; the inference-profile
    # prefix is added per region. Comma-separated via env.
    bedrock_model_fallback: List[str] = Field(
        default_factory=lambda: ["anthropic.claude-haiku-4-5-20251001-v1:0"],
        validation_alias="BEDROCK_MODEL_FALLBACK",
    )

    # Azure DevOps credentials are NOT declared here: app.services.azure_config_manager
    # owns its own config path and reads AZURE_DEVOPS_ENCRYPTION_KEY from the
    # environment directly. Settings copies existed but were never read, and the
    # path they advertised had already drifted from the real one.

    # OTel is likewise environment-only — app.core.observability.telemetry reads
    # OTEL_ENABLED / OTEL_EXPORTER_OTLP_ENDPOINT via os.getenv at startup, before
    # Settings is available. Duplicating them here created a second, silently
    # ineffective place to set them.

    # Read by _load_secret() above, so this one must stay a real field.
    aws_secrets_manager_enabled: bool = False
