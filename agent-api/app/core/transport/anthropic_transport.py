"""Anthropic direct-API transport implementation.

Uses the anthropic Python SDK.  Applies prompt caching (cache_control
breakpoints) automatically when the system prompt or user messages are
long enough to meet Anthropic's 1024-token minimum.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.transport.provider import (
    ProviderTransport,
    TransportMessage,
    TransportResponse,
    UsageCallback,
)

logger = logging.getLogger(__name__)

# Rough cost per million tokens (Claude Sonnet 4.x pricing as of 2026-05)
_INPUT_COST_PER_M  = 3.00
_OUTPUT_COST_PER_M = 15.00
_CACHE_WRITE_PER_M = 3.75
_CACHE_READ_PER_M  = 0.30


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens  / 1_000_000 * _INPUT_COST_PER_M
        + output_tokens / 1_000_000 * _OUTPUT_COST_PER_M
    )


class AnthropicTransport(ProviderTransport):
    """Transport backed by the direct Anthropic Messages API."""

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-6"):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.default_model = default_model

    def _build_messages(
        self, messages: List[TransportMessage]
    ) -> list[dict]:
        result = []
        for m in messages:
            if m.role == "system":
                continue  # system is passed separately to the API
            result.append({"role": m.role, "content": m.content})
        return result

    def _extract_system(self, messages: List[TransportMessage]) -> Optional[str]:
        for m in messages:
            if m.role == "system":
                return m.content if isinstance(m.content, str) else None
        return None

    async def complete(
        self,
        messages: List[TransportMessage],
        *,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> TransportResponse:
        from app.core.prompt_caching import apply_cache_control

        system_prompt = system or self._extract_system(messages)
        api_messages = self._build_messages(messages)

        # Apply cache_control breakpoints for long prompts
        api_messages, annotated_system = apply_cache_control(
            api_messages, system=system_prompt
        )

        kwargs: dict = dict(
            model=model or self.default_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=api_messages,
        )
        if annotated_system:
            kwargs["system"] = annotated_system
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)
        content = "".join(
            block.text for block in response.content
            if hasattr(block, "text")
        )
        usage   = response.usage
        in_tok  = usage.input_tokens
        out_tok = usage.output_tokens
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0

        logger.info(
            "anthropic.usage input=%d output=%d cache_create=%d cache_read=%d",
            in_tok, out_tok, cache_create, cache_read,
        )

        return TransportResponse(
            content=content,
            model=response.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_input_tokens=cache_create,
            cache_read_input_tokens=cache_read,
            cost_usd=_estimate_cost(in_tok, out_tok),
            raw=response,
        )

    async def complete_stream(
        self,
        messages: List[TransportMessage],
        *,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
        on_usage: Optional[UsageCallback] = None,
    ) -> AsyncGenerator[str, None]:
        from app.core.prompt_caching import apply_cache_control

        system_prompt = system or self._extract_system(messages)
        api_messages  = self._build_messages(messages)
        api_messages, annotated_system = apply_cache_control(
            api_messages, system=system_prompt
        )

        resolved_model = model or self.default_model
        kwargs: dict = dict(
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=api_messages,
        )
        if annotated_system:
            kwargs["system"] = annotated_system

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
            # Final message carries the aggregated usage object — propagate it
            # to the caller's ledger so streaming spend is no longer invisible.
            try:
                final_msg = await stream.get_final_message()
                usage = final_msg.usage
                in_tok  = getattr(usage, "input_tokens", 0) or 0
                out_tok = getattr(usage, "output_tokens", 0) or 0
                cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
                cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
                logger.info(
                    "anthropic.usage input=%d output=%d cache_create=%d cache_read=%d",
                    in_tok, out_tok, cache_create, cache_read,
                )
                if on_usage is not None:
                    try:
                        await on_usage({
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "cache_creation_input_tokens": cache_create,
                            "cache_read_input_tokens": cache_read,
                            "cost_usd": _estimate_cost(in_tok, out_tok),
                            "model": getattr(final_msg, "model", resolved_model),
                        })
                    except Exception:  # noqa: BLE001
                        logger.warning("on_usage callback raised", exc_info=True)
            except Exception:  # noqa: BLE001
                logger.debug("anthropic.usage: final message unavailable", exc_info=True)

    def get_langchain_llm(self, model: str, **kwargs: Any) -> Any:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model or self.default_model,
            anthropic_api_key=self._client.api_key,
            **kwargs,
        )
