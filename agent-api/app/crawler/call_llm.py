"""LLM call wrapper for crawler flows.

Resolves the model and transport from the DB-configured LLM (Settings page),
exactly the same way the ReAct workflow strategy does.  Falls back to the
CRAWLER_MODEL / PROVIDER_TRANSPORT env vars only when no DB config exists.

Wraps the project's existing transport layer (Anthropic/Bedrock) with:
  - Postgres prompt cache (app.crawler.cache)
  - Per-node token accounting for the flow_runs trace
  - A fixed low-temperature setting appropriate for structured extraction

Usage::

    from app.crawler.call_llm import call_llm

    response, tokens_in, tokens_out, was_cached = await call_llm(prompt="...")
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from app.core.transport.provider import TransportMessage
from app.crawler.cache import get_cached, put_cached

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.1
_MAX_TOKENS = 4096


async def _resolve_llm_config() -> Dict[str, Any]:
    """Return the active LLM config from the DB (Settings page).

    Mirrors the logic in ReactStrategy._resolve_llm_config:
    1. First config row in llm_configs table
    2. Enriches Bedrock configs with AWS credentials from model_keys table
    3. Falls back to env vars (CRAWLER_MODEL + PROVIDER_TRANSPORT) if DB is empty
    """
    from app.repositories.db_repository import db_repository

    try:
        db_configs = await db_repository.list_llm_configs()
        if db_configs:
            _name, cfg = next(iter(db_configs.items()))
            resolved: Dict[str, Any] = {
                "provider":    cfg["provider"],
                "model":       cfg["model"],
                "temperature": cfg.get("temperature", _TEMPERATURE),
                "max_tokens":  cfg.get("max_tokens", _MAX_TOKENS),
                "region":      cfg.get("region", "us-east-1"),
                "base_url":    cfg.get("base_url"),
            }

            # Bedrock: pull AWS credentials from model_keys table
            if resolved["provider"].lower() in ("bedrock", "aws", "aws_bedrock", "aws bedrock"):
                try:
                    for key_name in ("AWS Bedrock", "bedrock", "aws bedrock", "aws"):
                        mk = await db_repository.get_model_key(key_name, include_secrets=True)
                        if mk:
                            for field in ("access_key_id", "secret_access_key", "session_token"):
                                if mk.get(field):
                                    resolved[field] = mk[field]
                            if mk.get("region") and resolved["region"] == "us-east-1":
                                resolved["region"] = mk["region"]
                            break
                except Exception as exc:
                    logger.warning("call_llm: could not load Bedrock model_key: %s", exc)

                # Newer Bedrock models require a cross-region inference profile ID
                # (e.g. "eu.anthropic.claude-sonnet-4-6") instead of the bare
                # foundation model ID for on-demand invocation.  Apply the same
                # region-prefix remapping that ReactStrategy._build_llm() uses.
                _PROFILE_PREFIXES  = ("us.", "eu.", "ap.")
                _NEEDS_PROFILE_FOR = ("anthropic.", "amazon.", "meta.", "mistral.")
                _model  = resolved["model"]
                _region = resolved["region"]
                if (not any(_model.startswith(p) for p in _PROFILE_PREFIXES) and
                        any(_model.startswith(p) for p in _NEEDS_PROFILE_FOR)):
                    if _region.startswith("eu-"):
                        _model = f"eu.{_model}"
                    elif _region.startswith("ap-"):
                        _model = f"ap.{_model}"
                    else:
                        _model = f"us.{_model}"
                    resolved["model"] = _model
                    logger.info("call_llm: remapped model to inference profile: %s", _model)

            # Non-Bedrock providers: pull api_key from model_keys table
            else:
                try:
                    mk = await db_repository.get_model_key(resolved["provider"], include_secrets=True)
                    if mk and mk.get("api_key"):
                        resolved["api_key"] = mk["api_key"]
                except Exception as exc:
                    logger.warning("call_llm: could not load api_key for provider=%s: %s",
                                   resolved["provider"], exc)

            logger.debug("call_llm: resolved config provider=%s model=%s",
                         resolved["provider"], resolved["model"])
            return resolved

    except Exception as exc:
        logger.warning("call_llm: DB config lookup failed, falling back to env vars: %s", exc)

    # ── Env-var fallback ──────────────────────────────────────────────────
    provider = os.getenv("PROVIDER_TRANSPORT", "bedrock").lower()
    model    = os.getenv("CRAWLER_MODEL", "anthropic.claude-3-5-haiku-20241022-v1:0")
    logger.info("call_llm: no DB config found, using env fallback provider=%s model=%s",
                provider, model)
    return {
        "provider":    provider,
        "model":       model,
        "temperature": _TEMPERATURE,
        "max_tokens":  _MAX_TOKENS,
        "region":      os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1")),
    }


def _build_transport(cfg: Dict[str, Any]):
    """Construct the right ProviderTransport from a resolved config dict."""
    provider = cfg.get("provider", "").lower()

    if provider in ("bedrock", "aws", "aws_bedrock", "aws bedrock"):
        import boto3
        from app.core.transport.bedrock_transport import BedrockTransport

        region = cfg.get("region", "us-east-1")

        # If explicit AWS credentials were stored in model_keys, use them.
        # Otherwise boto3.Session() picks them up from env / instance profile.
        access_key_id     = cfg.get("access_key_id")
        secret_access_key = cfg.get("secret_access_key")
        session_token     = cfg.get("session_token")

        if access_key_id and secret_access_key:
            session = boto3.Session(
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                aws_session_token=session_token,
                region_name=region,
            )
            transport = BedrockTransport.__new__(BedrockTransport)
            transport._client = session.client("bedrock-runtime", region_name=region)
            transport.default_model = cfg.get("model", "")
            transport._region = region
            return transport

        return BedrockTransport(region=region)

    if provider == "anthropic":
        from app.core.transport.anthropic_transport import AnthropicTransport
        return AnthropicTransport(api_key=cfg.get("api_key", ""))

    # Generic fallback via the existing factory
    from app.core.transport.factory import get_transport
    return get_transport(provider=provider)


async def call_llm(
    prompt: str,
    model_id: Optional[str] = None,
    use_cache: bool = True,
    max_tokens: int = _MAX_TOKENS,
) -> Tuple[str, int, int, bool]:
    """Call the configured LLM with cache-through.

    The model and transport are resolved from the DB (Settings → LLM Configs),
    so whatever the user configured in the UI is used automatically.

    Args:
        prompt:     The full prompt text.
        model_id:   Override model identifier. When omitted the DB-configured
                    model is used.
        use_cache:  Whether to check/populate the Postgres prompt cache.
        max_tokens: Maximum tokens in the response.

    Returns:
        (response_text, tokens_in, tokens_out, was_cached)
        where *was_cached* is True when the response came from the prompt cache.
    """
    cfg   = await _resolve_llm_config()
    model = model_id or cfg["model"]

    if use_cache:
        cached = await get_cached(prompt, model)
        if cached is not None:
            logger.debug("call_llm: cache hit (model=%s, len=%d)", model, len(prompt))
            return cached, 0, 0, True

    transport = _build_transport(cfg)
    messages  = [TransportMessage(role="user", content=prompt)]

    response = await transport.complete(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=cfg.get("temperature", _TEMPERATURE),
    )

    text_out   = response.content
    tokens_in  = response.input_tokens
    tokens_out = response.output_tokens

    logger.debug("call_llm: live (model=%s, in=%d, out=%d)", model, tokens_in, tokens_out)

    if use_cache:
        await put_cached(prompt, model, text_out, tokens_in, tokens_out)

    return text_out, tokens_in, tokens_out, False
