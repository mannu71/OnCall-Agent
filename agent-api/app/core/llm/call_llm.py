"""Shared DB-resolved LLM call wrapper.

Resolves the model and transport from the DB-configured LLM (Settings page),
exactly the same way the ReAct workflow strategy does.  Falls back to the
CRAWLER_MODEL / PROVIDER_TRANSPORT env vars only when no DB config exists.

Wraps the project's existing transport layer (Anthropic/Bedrock) with:
  - Postgres prompt cache (app.core.llm.cache)
  - A fixed low-temperature setting appropriate for structured extraction

Used across the app (graders, supervision, memory extraction, session
summaries, semantic memory) — the model is chosen via the "crawler" gateway
role, kept for backward compatibility with existing role assignments.

Usage::

    from app.core.llm.call_llm import call_llm

    response, tokens_in, tokens_out, was_cached = await call_llm(prompt="...")
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from app.config import settings

from app.core.transport.provider import TransportMessage
from app.core.llm.cache import get_cached, put_cached

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.1
_MAX_TOKENS = 4096

# Bedrock cross-region inference-profile remapping. Newer Bedrock models require
# a region-prefixed inference profile ID (e.g. "eu.anthropic.claude-sonnet-4-6")
# instead of the bare foundation model ID for on-demand invocation.
_PROFILE_PREFIXES = ("us.", "eu.", "ap.")
_NEEDS_PROFILE_FOR = ("anthropic.", "amazon.", "meta.", "mistral.")
_BEDROCK_PROVIDERS = ("bedrock", "aws", "aws_bedrock", "aws bedrock")


def _apply_inference_profile(model: str, region: str) -> str:
    """Prefix a bare Bedrock foundation model ID with its region inference profile."""
    if any(model.startswith(p) for p in _PROFILE_PREFIXES):
        return model
    if not any(model.startswith(p) for p in _NEEDS_PROFILE_FOR):
        return model
    if region.startswith("eu-"):
        return f"eu.{model}"
    if region.startswith("ap-"):
        return f"ap.{model}"
    return f"us.{model}"


def _select_model(cfg: Dict[str, Any], explicit: Optional[str]) -> str:
    """Pick the model ID for a call.

    Precedence: an explicit per-call ``model_id`` wins; then the opt-in
    ``crawler_model_override`` setting; otherwise the DB-resolved model (already
    profile-remapped in ``_resolve_llm_config``). The override defaults to None,
    so the DB model is used until an operator opts into a cheaper model — and has
    confirmed that model/inference-profile is enabled in their account. Non-DB
    choices get the same Bedrock inference-profile remap.
    """
    if explicit:
        chosen = explicit
    elif settings.crawler_model_override:
        chosen = settings.crawler_model_override
    else:
        return cfg["model"]

    if cfg.get("provider", "").lower() in _BEDROCK_PROVIDERS:
        chosen = _apply_inference_profile(chosen, cfg.get("region", "us-east-1"))
    return chosen


async def _resolve_llm_config() -> Dict[str, Any]:
    """Return the active LLM config from the DB (Settings page).

    Mirrors the logic in ReactStrategy._resolve_llm_config:
    1. Gateway "crawler" role assignment, else first config row in llm_configs
    2. Enriches Bedrock configs with AWS credentials from model_keys table
    3. Falls back to env vars (CRAWLER_MODEL + PROVIDER_TRANSPORT) if DB is empty
    """
    from app.infrastructure.persistence import (
        llm_config_repository,
        model_key_repository,
        model_role_repository,
    )

    try:
        db_configs = await llm_config_repository.list_all()
        if db_configs:
            _name, cfg = next(iter(db_configs.items()))

            # Gateway: a "crawler" role assignment overrides the first-row default.
            try:
                role_name = await model_role_repository.get("crawler")
                if role_name:
                    role_cfg = db_configs.get(role_name)
                    if role_cfg is None:
                        role_cfg = await llm_config_repository.get_by_name(role_name)
                    if role_cfg:
                        _name, cfg = role_name, role_cfg
                        logger.info("call_llm: using crawler role-assigned config '%s'", role_name)
                    else:
                        logger.warning(
                            "call_llm: crawler role references missing config '%s' — using first row",
                            role_name,
                        )
            except Exception as exc:
                logger.warning("call_llm: crawler role lookup failed: %s", exc)

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
                        mk = await model_key_repository.get_by_provider(key_name, include_secrets=True)
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
                _model = _apply_inference_profile(resolved["model"], resolved["region"])
                if _model != resolved["model"]:
                    resolved["model"] = _model
                    logger.info("call_llm: remapped model to inference profile: %s", _model)

            # Non-Bedrock providers: pull api_key from model_keys table
            else:
                try:
                    mk = await model_key_repository.get_by_provider(resolved["provider"], include_secrets=True)
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
    provider = settings.provider_transport.lower()
    model    = settings.crawler_model
    logger.info("call_llm: no DB config found, using env fallback provider=%s model=%s",
                provider, model)
    return {
        "provider":    provider,
        "model":       model,
        "temperature": _TEMPERATURE,
        "max_tokens":  _MAX_TOKENS,
        "region":      settings.effective_bedrock_region,
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

    # Bedrock-only. Anything else goes through the factory, which raises a clear
    # error rather than silently constructing a non-Bedrock transport.
    from app.core.transport.factory import get_transport
    return get_transport(provider=provider)


async def call_llm(
    prompt: str,
    model_id: Optional[str] = None,
    use_cache: bool = True,
    max_tokens: int = _MAX_TOKENS,
    tier: str = "search",
) -> Tuple[str, int, int, bool]:
    """Call the configured LLM with cache-through.

    The model and transport are resolved from the DB (Settings → LLM Configs),
    so whatever the user configured in the UI is used automatically.

    Args:
        prompt:     The full prompt text.
        model_id:   Override model identifier. When omitted the model follows
                    ``crawler_model_override`` else the DB-resolved model.
        use_cache:  Whether to check/populate the Postgres prompt cache.
        max_tokens: Maximum tokens in the response.
        tier:       Accepted for backward compatibility; no longer affects model
                    selection.

    Returns:
        (response_text, tokens_in, tokens_out, was_cached)
        where *was_cached* is True when the response came from the prompt cache.
    """
    cfg   = await _resolve_llm_config()
    model = _select_model(cfg, model_id)

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

    _cache_read = getattr(response, "cache_read_input_tokens", 0) or 0
    _cache_write = getattr(response, "cache_creation_input_tokens", 0) or 0

    logger.info(
        "call_llm: live tier=%s model=%s in=%d out=%d cache_read=%d cache_write=%d",
        tier, model, tokens_in, tokens_out, _cache_read, _cache_write,
    )

    # Book it against the run ledger. This is the single choke point for every
    # non-agent LLM helper — grader, fact extractor, action supervisor, semantic
    # memory, improvement analyzer, session titles — none of which the agent's
    # TokenUsageCallback can see. A cache HIT returns above and is deliberately
    # not recorded: it costs nothing.
    from app.harness.usage_ledger import record_auxiliary_usage
    record_auxiliary_usage(
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_read_tokens=_cache_read,
        cache_creation_tokens=_cache_write,
        source=f"call_llm:{tier}",
        exclusive=True,   # TransportResponse carries Bedrock's RAW exclusive counters
    )

    if use_cache:
        await put_cached(prompt, model, text_out, tokens_in, tokens_out)

    return text_out, tokens_in, tokens_out, False
