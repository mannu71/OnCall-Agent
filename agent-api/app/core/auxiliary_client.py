"""Auxiliary LLM client for side tasks like summarization and vision analysis.

Provides a secondary LLM client that resolves providers in priority order
and falls back gracefully on payment/credit errors.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.core.error_classifier import classify_error, FailoverReason

logger = logging.getLogger(__name__)


class AuxiliaryClient:
    """Secondary LLM client for side tasks (summarization, compression, vision).
    
    Resolves providers in priority order:
    1. OpenRouter (if OPENROUTER_API_KEY set)
    2. Custom endpoint (if base_url + OPENAI_API_KEY set)
    3. Native Anthropic (if ANTHROPIC_API_KEY set)
    4. None (raises RuntimeError)
    
    Falls back to next provider on payment/credit errors.
    """
    
    def __init__(
        self,
        provider: str = "auto",
        model: str = "",
        base_url: str = "",
        api_key: str = "",
    ):
        """Initialize auxiliary client with provider resolution.
        
        Args:
            provider: Provider mode ("auto", "openrouter", "anthropic", "openai", "custom")
            model: Model to use (empty = auto-select based on provider)
            base_url: Custom base URL for OpenAI-compatible endpoints
            api_key: API key override (uses env vars if not provided)
        """
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._client = None
        self._resolved_provider: Optional[str] = None
        self._resolved_model: Optional[str] = None
        
        # Resolve provider on initialization
        if provider == "auto":
            self._resolve_auto_provider()
        else:
            self._resolved_provider = provider
            self._resolved_model = model or self._get_default_model(provider)
    
    def _resolve_auto_provider(self) -> None:
        """Resolve provider in priority order for auto mode.
        
        Priority: OpenRouter → Custom → Anthropic → None
        """
        # 1. Try OpenRouter
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            self._resolved_provider = "openrouter"
            self._resolved_model = self.model or "anthropic/claude-3-5-haiku-20241022"
            logger.info("Auxiliary client resolved to OpenRouter")
            return
        
        # 2. Try Custom endpoint
        if self.base_url:
            custom_key = self.api_key or os.getenv("OPENAI_API_KEY")
            if custom_key:
                self._resolved_provider = "custom"
                self._resolved_model = self.model or "gpt-4o-mini"
                logger.info(f"Auxiliary client resolved to custom endpoint: {self.base_url}")
                return
        
        # 3. Try Native Anthropic
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            self._resolved_provider = "anthropic"
            self._resolved_model = self.model or "claude-3-5-haiku-20241022"
            logger.info("Auxiliary client resolved to Anthropic")
            return
        
        # 4. No provider available
        self._resolved_provider = None
        self._resolved_model = None
        logger.warning("No auxiliary provider available (no API keys found)")
    
    def _get_default_model(self, provider: str) -> str:
        """Get default model for a provider.
        
        Args:
            provider: Provider name
            
        Returns:
            Default model string for the provider
        """
        defaults = {
            "openrouter": "anthropic/claude-3-5-haiku-20241022",
            "anthropic": "claude-3-5-haiku-20241022",
            "openai": "gpt-4o-mini",
            "custom": "gpt-4o-mini",
        }
        return defaults.get(provider, "gpt-4o-mini")
    
    def _get_client(self):
        """Get or create the LLM client for the resolved provider.
        
        Returns:
            OpenAI or Anthropic client instance
            
        Raises:
            RuntimeError: If no provider is available
        """
        if self._client is not None:
            return self._client
        
        if self._resolved_provider is None:
            raise RuntimeError(
                "No auxiliary LLM provider available. "
                "Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY."
            )
        
        # Create client based on resolved provider
        if self._resolved_provider == "openrouter":
            from openai import AsyncOpenAI
            api_key = self.api_key or os.getenv("OPENROUTER_API_KEY")
            self._client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            logger.debug("Created OpenRouter client for auxiliary tasks")
        
        elif self._resolved_provider == "custom":
            from openai import AsyncOpenAI
            api_key = self.api_key or os.getenv("OPENAI_API_KEY")
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=api_key,
            )
            logger.debug(f"Created custom OpenAI client for auxiliary tasks: {self.base_url}")
        
        elif self._resolved_provider == "openai":
            from openai import AsyncOpenAI
            api_key = self.api_key or os.getenv("OPENAI_API_KEY")
            self._client = AsyncOpenAI(api_key=api_key)
            logger.debug("Created OpenAI client for auxiliary tasks")
        
        elif self._resolved_provider == "anthropic":
            from anthropic import AsyncAnthropic
            api_key = self.api_key or os.getenv("ANTHROPIC_API_KEY")
            self._client = AsyncAnthropic(api_key=api_key)
            logger.debug("Created Anthropic client for auxiliary tasks")
        
        return self._client
    
    async def call(
        self,
        messages: List[Dict[str, Any]],
        task: str = "compression",
        max_tokens: int = 2000,
        **kwargs,
    ) -> Any:
        """Call auxiliary LLM for text-based side task.
        
        Args:
            messages: Message list in OpenAI format
            task: Task type ("compression", "summarization", etc.)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional parameters for the LLM call
            
        Returns:
            LLM response (format depends on provider)
            
        Raises:
            RuntimeError: If no provider is available
        """
        client = self._get_client()
        
        # Use Anthropic Messages API if native Anthropic
        if self._resolved_provider == "anthropic":
            return await self._call_anthropic(client, messages, max_tokens, **kwargs)
        
        # Otherwise use OpenAI-compatible API
        return await self._call_openai_compatible(client, messages, max_tokens, **kwargs)
    
    async def _call_openai_compatible(
        self,
        client,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        **kwargs,
    ) -> Any:
        """Call OpenAI-compatible API.
        
        Args:
            client: OpenAI client instance
            messages: Message list
            max_tokens: Maximum tokens
            **kwargs: Additional parameters
            
        Returns:
            Chat completion response
        """
        response = await client.chat.completions.create(
            model=self._resolved_model,
            messages=messages,
            max_tokens=max_tokens,
            **kwargs,
        )
        return response
    
    async def _call_anthropic(
        self,
        client,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        **kwargs,
    ) -> Any:
        """Call Anthropic Messages API and adapt to OpenAI format.
        
        Args:
            client: Anthropic client instance
            messages: Message list in OpenAI format
            max_tokens: Maximum tokens
            **kwargs: Additional parameters
            
        Returns:
            Response adapted to OpenAI format
        """
        # Extract system message if present
        system_message = None
        anthropic_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })
        
        # Call Anthropic API
        response = await client.messages.create(
            model=self._resolved_model,
            max_tokens=max_tokens,
            system=system_message or "",
            messages=anthropic_messages,
            **kwargs,
        )
        
        # Adapt response to OpenAI format
        return self._adapt_anthropic_response(response)
    
    def _adapt_anthropic_response(self, response) -> Any:
        """Adapt Anthropic response to OpenAI format.
        
        Args:
            response: Anthropic Messages API response
            
        Returns:
            Response object with OpenAI-compatible structure
        """
        # Create a simple object that mimics OpenAI response structure
        class AdaptedResponse:
            def __init__(self, anthropic_response):
                self.id = anthropic_response.id
                self.model = anthropic_response.model
                self.usage = type('Usage', (), {
                    'prompt_tokens': anthropic_response.usage.input_tokens,
                    'completion_tokens': anthropic_response.usage.output_tokens,
                    'total_tokens': anthropic_response.usage.input_tokens + anthropic_response.usage.output_tokens,
                })()
                
                # Extract text content from Anthropic response
                text_content = ""
                for block in anthropic_response.content:
                    if hasattr(block, 'text'):
                        text_content += block.text
                
                self.choices = [
                    type('Choice', (), {
                        'message': type('Message', (), {
                            'role': 'assistant',
                            'content': text_content,
                        })(),
                        'finish_reason': anthropic_response.stop_reason,
                    })()
                ]
        
        return AdaptedResponse(response)
    
    async def call_vision(
        self,
        image_data: str,
        question: str,
        **kwargs,
    ) -> str:
        """Call vision-capable model for image analysis.
        
        Args:
            image_data: Base64-encoded image data or image URL
            question: Question to ask about the image
            **kwargs: Additional parameters
            
        Returns:
            Text response from vision model
            
        Raises:
            RuntimeError: If no provider is available
        """
        client = self._get_client()
        
        # Construct vision message
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}"
                            if not image_data.startswith("http")
                            else image_data
                        },
                    },
                ],
            }
        ]
        
        # Use vision-capable model
        vision_model = self._get_vision_model()
        
        if self._resolved_provider == "anthropic":
            # Anthropic vision format is different
            response = await client.messages.create(
                model=vision_model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_data,
                                },
                            },
                            {"type": "text", "text": question},
                        ],
                    }
                ],
                **kwargs,
            )
            # Extract text from Anthropic response
            text_content = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    text_content += block.text
            return text_content
        
        else:
            # OpenAI-compatible vision
            response = await client.chat.completions.create(
                model=vision_model,
                messages=messages,
                max_tokens=1024,
                **kwargs,
            )
            return response.choices[0].message.content
    
    def _get_vision_model(self) -> str:
        """Get vision-capable model for the resolved provider.
        
        Returns:
            Vision model string
        """
        vision_models = {
            "openrouter": "anthropic/claude-3-5-sonnet-20241022",
            "anthropic": "claude-3-5-sonnet-20241022",
            "openai": "gpt-4o",
            "custom": "gpt-4o",
        }
        return vision_models.get(self._resolved_provider, "gpt-4o")
    
    @property
    def is_available(self) -> bool:
        """Check if auxiliary client has a valid provider.
        
        Returns:
            True if a provider is available, False otherwise
        """
        return self._resolved_provider is not None
    
    @property
    def provider_info(self) -> Dict[str, Any]:
        """Get information about the resolved provider.
        
        Returns:
            Dict with provider, model, and availability info
        """
        return {
            "provider": self._resolved_provider,
            "model": self._resolved_model,
            "available": self.is_available,
        }
    
    def get_fallback_providers(self) -> List[str]:
        """Get list of fallback providers in priority order.
        
        Returns:
            List of provider names that can be used for fallback
        """
        # Define fallback chain based on current provider
        fallback_chains = {
            "openrouter": ["anthropic", "openai"],
            "anthropic": ["openrouter", "openai"],
            "openai": ["openrouter", "anthropic"],
            "custom": ["openrouter", "anthropic", "openai"],
        }
        
        if self._resolved_provider is None:
            return []
        
        return fallback_chains.get(self._resolved_provider, [])
    
    async def call_with_fallback(
        self,
        messages: List[Dict[str, Any]],
        task: str = "compression",
        max_tokens: int = 2000,
        max_retries: int = 2,
        **kwargs,
    ) -> Any:
        """Call auxiliary LLM with automatic fallback on payment/credit errors.
        
        Args:
            messages: Message list in OpenAI format
            task: Task type ("compression", "summarization", etc.)
            max_tokens: Maximum tokens to generate
            max_retries: Maximum number of fallback attempts
            **kwargs: Additional parameters for the LLM call
            
        Returns:
            LLM response (format depends on provider)
            
        Raises:
            RuntimeError: If all providers fail
        """
        last_error = None
        attempted_providers = [self._resolved_provider]
        
        # Try current provider first
        try:
            return await self.call(messages, task, max_tokens, **kwargs)
        except Exception as e:
            classified = classify_error(e)
            
            # Only fallback on billing/credit errors
            if not classified.should_fallback:
                raise
            
            logger.warning(
                f"Auxiliary provider {self._resolved_provider} failed with {classified.reason.value}, "
                f"attempting fallback"
            )
            last_error = e
        
        # Try fallback providers
        fallback_providers = self.get_fallback_providers()
        for fallback_provider in fallback_providers[:max_retries]:
            if fallback_provider in attempted_providers:
                continue
            
            attempted_providers.append(fallback_provider)
            
            # Create new client with fallback provider
            fallback_client = AuxiliaryClient(
                provider=fallback_provider,
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
            )
            
            if not fallback_client.is_available:
                logger.warning(f"Fallback provider {fallback_provider} not available, skipping")
                continue
            
            try:
                logger.info(f"Attempting fallback to provider: {fallback_provider}")
                return await fallback_client.call(messages, task, max_tokens, **kwargs)
            except Exception as e:
                classified = classify_error(e)
                logger.warning(
                    f"Fallback provider {fallback_provider} failed with {classified.reason.value}"
                )
                last_error = e
                
                # If not a fallback-worthy error, raise immediately
                if not classified.should_fallback:
                    raise
        
        # All providers failed
        raise RuntimeError(
            f"All auxiliary providers failed. Attempted: {', '.join(attempted_providers)}"
        ) from last_error
