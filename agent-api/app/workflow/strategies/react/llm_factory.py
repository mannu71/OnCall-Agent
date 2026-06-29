"""LangChain LLM factory for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict

from app.config import settings

logger = logging.getLogger(__name__)

def build_llm(llm_config: Dict[str, Any]) -> Any:
    """
    Instantiate a LangChain LLM from the resolved configuration.

    Supports:
    - provider="openai"      → ChatOpenAI
    - provider="anthropic"   → ChatAnthropic
    - provider="google"      → ChatGoogleGenerativeAI
    - provider="groq"        → ChatGroq
    - provider="bedrock"     → ChatBedrockConverse
    - provider="azure"       → AzureChatOpenAI
    - provider="ollama"      → ChatOllama

    Args:
        llm_config: Resolved LLM configuration dict.

    Returns:
        LangChain chat model instance (BaseChatModel).

    Raises:
        ValueError: If the provider is not supported.
    """
    provider = (llm_config.get("provider") or "bedrock").lower()
    # Normalize provider aliases
    if provider == "aws bedrock":
        provider = "bedrock"
    model = llm_config.get("model", "")
    temperature = float(llm_config.get("temperature") or 0.1)
    max_tokens = int(llm_config.get("max_tokens") or settings.agent_max_output_tokens)
    region = llm_config.get("region") or "us-east-1"
    api_key = llm_config.get("api_key")
    base_url = llm_config.get("base_url")

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # core-only / runtime-slim omits this extra
            raise RuntimeError(
                "provider='openai' requires the optional 'openai' extra "
                "(langchain-openai), which is not installed in this (slim) image. "
                "Install it with: pip install '.[openai]', or use a Bedrock LLM node."
            ) from exc
        kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        logger.info("ReactStrategy: using ChatOpenAI model=%s", model)
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        logger.info("ReactStrategy: using ChatAnthropic model=%s", model)
        return ChatAnthropic(**kwargs)

    if provider in ("google", "gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if api_key:
            kwargs["google_api_key"] = api_key
        logger.info("ReactStrategy: using ChatGoogleGenerativeAI model=%s", model)
        return ChatGoogleGenerativeAI(**kwargs)

    if provider == "groq":
        from langchain_groq import ChatGroq
        kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        logger.info("ReactStrategy: using ChatGroq model=%s", model)
        return ChatGroq(**kwargs)

    if provider in ("bedrock", "aws", "aws_bedrock"):
        from langchain_aws import ChatBedrockConverse
        import boto3
        from botocore.config import Config as BotocoreConfig
        access_key_id = llm_config.get("access_key_id")
        secret_access_key = llm_config.get("secret_access_key")
        session_token = llm_config.get("session_token")
        aws_profile = llm_config.get("aws_profile") or llm_config.get("profile")
        # Newer Bedrock models (e.g. Claude 3.5/4.x) require a cross-region
        # inference profile ID instead of the bare model ID for on-demand calls.
        # Automatically prepend the region prefix when the model ID looks like a
        # plain foundation model ID (e.g. "anthropic.claude-*") with no prefix.
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
        if access_key_id and secret_access_key:
            boto_session = boto3.Session(
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                aws_session_token=session_token,
            )
        else:
            boto_session = boto3.Session(region_name=region, profile_name=aws_profile)
        # read_timeout default is botocore's 60s, which is too short for a
        # large-context synthesis call (200K+ tokens) — those legitimately take
        # >60s and would raise "Read timeout on endpoint URL …/converse",
        # killing the run. Lift it to a configurable ceiling (default 300s, the
        # same bound as the agent's non-streaming asyncio.wait_for).
        _read_timeout = getattr(settings, "bedrock_read_timeout_seconds", 300)
        boto_client = boto_session.client(
            "bedrock-runtime",
            region_name=region,
            verify=False,
            config=BotocoreConfig(
                retries={"max_attempts": 3},
                read_timeout=_read_timeout,
                connect_timeout=10,
            ),
        )
        return ChatBedrockConverse(
            model=model,
            region_name=region,
            temperature=temperature,
            max_tokens=max_tokens,
            client=boto_client,
        )

    if provider in ("azure", "azure_openai"):
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError as exc:  # core-only / runtime-slim omits this extra
            raise RuntimeError(
                "provider='azure' requires the optional 'openai' extra "
                "(langchain-openai), which is not installed in this (slim) image. "
                "Install it with: pip install '.[openai]', or use a Bedrock LLM node."
            ) from exc
        kwargs: Dict[str, Any] = {
            "azure_deployment": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["azure_endpoint"] = base_url
        logger.info("ReactStrategy: using AzureChatOpenAI model=%s", model)
        return AzureChatOpenAI(**kwargs)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if base_url:
            kwargs["base_url"] = base_url
        logger.info("ReactStrategy: using ChatOllama model=%s", model)
        return ChatOllama(**kwargs)

    raise ValueError(
        f"Unsupported LLM provider '{provider}'. "
        f"Supported providers: openai, anthropic, google, groq, bedrock, azure, ollama."
    )
