"""LangChain LLM factory for ReAct agents.

Generation is **AWS Bedrock only** (``ChatBedrockConverse``). The application
uses Bedrock for every generative LLM call and the vendored local
``snowflake-arctic-embed-s`` ONNX model for embeddings (see
``app.core.code_semantic``) — no other chat providers are supported. A config
that names a non-Bedrock provider is rejected with a clear error rather than
silently constructed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.config import settings

logger = logging.getLogger(__name__)

# Providers that map to the single Bedrock path (aliases included).
_BEDROCK_PROVIDERS = ("bedrock", "aws", "aws_bedrock", "aws bedrock")


def build_llm(llm_config: Dict[str, Any]) -> Any:
    """
    Instantiate a LangChain ``ChatBedrockConverse`` from the resolved config.

    Args:
        llm_config: Resolved LLM configuration dict.

    Returns:
        ChatBedrockConverse instance.

    Raises:
        ValueError: If the config names a non-Bedrock provider (the app is
            Bedrock-only for generation).
        RuntimeError: If ``langchain-aws`` / ``botocore`` are not installed.
    """
    provider = (llm_config.get("provider") or "bedrock").lower().strip()
    if provider not in _BEDROCK_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. This application uses AWS "
            "Bedrock exclusively for generation (plus the local "
            "snowflake-arctic-embed-s model for embeddings). Configure a Bedrock "
            "LLM (provider='bedrock')."
        )

    try:
        from langchain_aws import ChatBedrockConverse
        from botocore.config import Config as BotocoreConfig
    except ImportError as exc:
        raise RuntimeError(
            "Bedrock initialization requires 'langchain-aws' and 'botocore'. "
            "Ensure they are installed in this environment."
        ) from exc

    # ── Core properties ──────────────────────────────────────────────────
    model = llm_config.get("model", "")
    temperature = float(llm_config.get("temperature") or 0.1)
    max_tokens = int(llm_config.get("max_tokens") or settings.agent_max_output_tokens)
    region = llm_config.get("region") or "us-east-1"

    # ── Credentials / profile ────────────────────────────────────────────
    access_key_id = llm_config.get("access_key_id")
    secret_access_key = llm_config.get("secret_access_key")
    session_token = llm_config.get("session_token")
    aws_profile = llm_config.get("aws_profile") or llm_config.get("profile")

    # ── Cross-region inference-profile remapping ─────────────────────────
    # Newer Bedrock models (e.g. Claude 3.5/4.x) require a cross-region inference
    # profile ID instead of the bare model ID for on-demand calls. Prepend the
    # region prefix when the model looks like a plain foundation model ID.
    _INFERENCE_PROFILE_PREFIXES = ("us.", "eu.", "ap.")
    _NEEDS_PROFILE_PROVIDERS = ("anthropic.", "amazon.", "meta.", "mistral.")
    if not any(model.startswith(p) for p in _INFERENCE_PROFILE_PREFIXES) and \
            any(model.startswith(p) for p in _NEEDS_PROFILE_PROVIDERS):
        if region.startswith("eu-"):
            model = f"eu.{model}"
        elif region.startswith("ap-"):
            model = f"ap.{model}"
        else:
            model = f"us.{model}"
        logger.info("ReactStrategy: remapped model to inference profile ID: %s", model)

    logger.info(
        "ReactStrategy: using ChatBedrockConverse model=%s region=%s has_explicit_creds=%s profile=%s",
        model, region, bool(access_key_id), aws_profile,
    )

    # ── Botocore client config ───────────────────────────────────────────
    # read_timeout: botocore's 60s default is too short for a large-context
    # synthesis call (200K+ tokens), which raised "Read timeout … /converse" and
    # killed the run — lift it to a configurable ceiling (default 300s).
    # max_pool_connections: one client serves parallel tool supersteps +
    # delegate_parallel children; botocore's default pool of 10 becomes a hard
    # concurrency ceiling under wide fan-out. ``adaptive`` retry mode adds
    # client-side rate-limit backoff (the fallback-chain still bubbles throttles
    # out for failover). ChatBedrockConverse builds its own boto client from
    # these kwargs; TLS verification is disabled app-wide by the AWS_SSL_VERIFY
    # monkey-patch in app.main (which wraps boto3.client / Session.client), so no
    # explicit ``verify=False`` client is needed here.
    _read_timeout = getattr(settings, "bedrock_read_timeout_seconds", 300)
    _max_pool = getattr(settings, "bedrock_max_pool_connections", 10)
    botocore_config = BotocoreConfig(
        retries={"max_attempts": 3, "mode": "adaptive"},
        read_timeout=_read_timeout,
        connect_timeout=10,
        max_pool_connections=_max_pool,
    )

    # ── Native ChatBedrockConverse construction ──────────────────────────
    bedrock_kwargs: Dict[str, Any] = {
        "model_id": model,
        "region_name": region,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "config": botocore_config,
    }
    if access_key_id and secret_access_key:
        bedrock_kwargs["aws_access_key_id"] = access_key_id
        bedrock_kwargs["aws_secret_access_key"] = secret_access_key
        if session_token:
            bedrock_kwargs["aws_session_token"] = session_token
    elif aws_profile:
        bedrock_kwargs["credentials_profile_name"] = aws_profile

    # Opt-in Bedrock latency-optimized inference. Support is model/region
    # dependent, so it is left per-deployment opt-in (off by default).
    if getattr(settings, "bedrock_latency_optimized", False):
        bedrock_kwargs["performance_config"] = {"latency": "optimized"}

    return ChatBedrockConverse(**bedrock_kwargs)
