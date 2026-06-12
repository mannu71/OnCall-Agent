"""AWS Bedrock transport implementation.

Uses boto3 bedrock-runtime to call Claude models hosted on AWS Bedrock.
Falls back gracefully when boto3 is not available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.thread_pools import run_in_aws_pool
from app.core.transport.provider import (
    ProviderTransport,
    TransportMessage,
    TransportResponse,
    UsageCallback,
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

        response = await run_in_aws_pool(_invoke)
        result = json.loads(response["body"].read())

        content = "".join(
            block.get("text", "") for block in result.get("content", [])
        )
        usage   = result.get("usage", {})
        in_tok  = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        # Surface Bedrock prompt-cache accounting so callers (crawler call_llm,
        # token ledgers) can see whether caching is actually engaging.
        cache_read  = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0

        return TransportResponse(
            content=content,
            model=model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
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
        on_usage: Optional[UsageCallback] = None,
    ) -> AsyncGenerator[str, None]:
        model_id = model or self.default_model
        body = self._build_body(messages, max_tokens=max_tokens, temperature=temperature, system=system)

        def _invoke_stream():
            return self._client.invoke_model_with_response_stream(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )

        response = await run_in_aws_pool(_invoke_stream)

        # Read the blocking boto3 stream body in a background thread so token
        # deltas can be yielded without stalling the asyncio event loop.
        loop = asyncio.get_running_loop()
        chunk_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def _read_body() -> None:
            try:
                for event in response.get("body"):
                    chunk = json.loads(event["chunk"]["bytes"])
                    asyncio.run_coroutine_threadsafe(
                        chunk_queue.put(("chunk", chunk)), loop
                    ).result(timeout=120)
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(
                    chunk_queue.put(("error", exc)), loop
                ).result(timeout=5)
            finally:
                asyncio.run_coroutine_threadsafe(
                    chunk_queue.put(("done", None)), loop
                ).result(timeout=5)

        threading.Thread(target=_read_body, daemon=True, name="bedrock-stream").start()

        in_tok = 0
        out_tok = 0
        while True:
            kind, payload = await chunk_queue.get()
            if kind == "done":
                break
            if kind == "error":
                logger.warning("Bedrock stream read failed: %s", payload)
                break

            chunk = payload
            ctype = chunk.get("type")
            if ctype == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    yield delta.get("text", "")
            elif ctype == "message_delta":
                usage = chunk.get("usage") or {}
                in_tok = usage.get("input_tokens", in_tok) or in_tok
                out_tok = usage.get("output_tokens", out_tok) or out_tok
            elif ctype == "message_stop":
                metrics = chunk.get("amazon-bedrock-invocationMetrics") or {}
                in_tok = metrics.get("inputTokenCount", in_tok) or in_tok
                out_tok = metrics.get("outputTokenCount", out_tok) or out_tok

        if on_usage is not None and (in_tok or out_tok):
            try:
                await on_usage({
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "model": model_id,
                })
            except Exception:  # noqa: BLE001
                logger.warning("on_usage callback raised", exc_info=True)

    def get_langchain_llm(self, model: str, **kwargs: Any) -> Any:
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(
            model_id=model or self.default_model,
            region_name=self._region,
            **kwargs,
        )
