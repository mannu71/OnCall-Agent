"""AWS Bedrock transport implementation.

Uses boto3 bedrock-runtime to call Claude models hosted on AWS Bedrock.
Falls back gracefully when boto3 is not available.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.transport.provider import (
    ProviderTransport,
    TransportMessage,
    TransportResponse,
)

logger = logging.getLogger(__name__)

_DEFAULT_BEDROCK_MODEL = "anthropic.claude-sonnet-4-6-20251001-v1:0"
_ANTHROPIC_VERSION = "bedrock-2023-05-31"


class BedrockTransport(ProviderTransport):
    """Transport backed by AWS Bedrock (boto3 bedrock-runtime)."""

    def __init__(
        self,
        region: str = "us-east-1",
        profile: Optional[str] = None,
        default_model: str = _DEFAULT_BEDROCK_MODEL,
    ):
        import boto3

        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self._client = session.client("bedrock-runtime", region_name=region)
        self.default_model = default_model
        self._region = region

    def _build_body(
        self,
        messages: List[TransportMessage],
        *,
        max_tokens: int,
        temperature: float,
        system: Optional[str],
    ) -> dict:
        api_messages = []
        for m in messages:
            if m.role == "system":
                continue
            content = m.content if isinstance(m.content, list) else [{"type": "text", "text": m.content}]
            api_messages.append({"role": m.role, "content": content})

        body: dict = {
            "anthropic_version": _ANTHROPIC_VERSION,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": api_messages,
        }
        if system:
            body["system"] = system
        return body

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
        import asyncio

        model_id = model or self.default_model
        body = self._build_body(messages, max_tokens=max_tokens, temperature=temperature, system=system)
        if tools:
            body["tools"] = tools

        def _invoke():
            return self._client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _invoke)
        result = json.loads(response["body"].read())

        content = "".join(
            block.get("text", "") for block in result.get("content", [])
        )
        usage   = result.get("usage", {})
        in_tok  = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)

        return TransportResponse(
            content=content,
            model=model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            raw=result,
        )

    async def complete_stream(
        self,
        messages: List[TransportMessage],
        *,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        import asyncio

        model_id = model or self.default_model
        body = self._build_body(messages, max_tokens=max_tokens, temperature=temperature, system=system)

        def _invoke_stream():
            return self._client.invoke_model_with_response_stream(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _invoke_stream)

        for event in response.get("body"):
            chunk = json.loads(event["chunk"]["bytes"])
            if chunk.get("type") == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield delta.get("text", "")

    def get_langchain_llm(self, model: str, **kwargs: Any) -> Any:
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(
            model_id=model or self.default_model,
            region_name=self._region,
            **kwargs,
        )
