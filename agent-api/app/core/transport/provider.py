"""ProviderTransport ABC — wraps all LLM provider calls behind one interface.

All direct SDK calls (Anthropic, Bedrock) must go through a concrete
implementation of this class.  Callers never import anthropic or boto3
directly — they call transport.complete() and transport.complete_stream().

Reference: hermes-agent/agent/transports/ pattern.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional


@dataclass
class TransportMessage:
    """A single message in the conversation (provider-agnostic)."""
    role: str                # "user" | "assistant" | "system"
    content: str | list      # str or list of content blocks (for tool use)


@dataclass
class TransportResponse:
    """Response from a provider complete() call."""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: Any = field(default=None, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ProviderTransport(ABC):
    """Abstract base class for LLM provider transports.

    Concrete implementations:
    - AnthropicTransport  (direct Anthropic API)
    - BedrockTransport    (AWS Bedrock with boto3)
    """

    @abstractmethod
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
        """Send a completion request and return the full response.

        Args:
            messages: Conversation history.
            model: Provider-specific model identifier.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.
            system: Optional system prompt (extracted separately for providers
                that treat it specially, e.g. Anthropic).
            tools: Optional tool schemas in provider-agnostic format.

        Returns:
            TransportResponse with content and token counts.
        """
        ...

    @abstractmethod
    async def complete_stream(
        self,
        messages: List[TransportMessage],
        *,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a completion response, yielding text tokens as they arrive.

        Args:
            messages: Conversation history.
            model: Provider-specific model identifier.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.
            system: Optional system prompt.

        Yields:
            Text token strings.
        """
        ...

    @abstractmethod
    def get_langchain_llm(self, model: str, **kwargs: Any) -> Any:
        """Return a LangChain-compatible LLM instance for this provider.

        Used by ReactStrategy to build LangGraph agents.  Each transport
        knows which LangChain class maps to it.

        Args:
            model: Model identifier.
            **kwargs: Additional model parameters (temperature, etc.)

        Returns:
            A LangChain BaseChatModel instance.
        """
        ...
