"""Unit tests for auxiliary_client module.

Tests the AuxiliaryClient class for provider resolution, API calls,
and fallback behavior.

**Validates: Requirements 12.1, 12.2, 12.4, 12.5, 12.6, 12.7**
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
import os

from app.core.auxiliary_client import AuxiliaryClient


class TestAuxiliaryClientProviderResolution:
    """Tests for provider resolution in auto mode."""
    
    def test_openrouter_has_highest_priority(self):
        """OpenRouter should be selected when OPENROUTER_API_KEY is set.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "OPENAI_API_KEY": "test-openai-key",
        }):
            client = AuxiliaryClient(provider="auto")
            
            assert client._resolved_provider == "openrouter"
            assert "claude" in client._resolved_model.lower() or "haiku" in client._resolved_model.lower()
    
    def test_custom_endpoint_second_priority(self):
        """Custom endpoint should be selected when base_url is provided and OpenRouter is not available.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-openai-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        }, clear=True):
            client = AuxiliaryClient(
                provider="auto",
                base_url="https://custom.example.com/v1",
            )
            
            assert client._resolved_provider == "custom"
            assert client._resolved_model == "gpt-4o-mini"
    
    def test_anthropic_third_priority(self):
        """Anthropic should be selected when OpenRouter and custom are not available.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        }, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            assert client._resolved_provider == "anthropic"
            assert "claude" in client._resolved_model.lower()
    
    def test_no_provider_available(self):
        """Client should handle no available providers gracefully.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            assert client._resolved_provider is None
            assert client._resolved_model is None
            assert not client.is_available
    
    def test_explicit_provider_overrides_auto(self):
        """Explicit provider should override auto resolution.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        }):
            client = AuxiliaryClient(provider="anthropic")
            
            # Should use explicit provider, not auto-resolved OpenRouter
            assert client._resolved_provider == "anthropic"
    
    def test_custom_model_override(self):
        """Custom model should override default model selection.
        
        **Validates: Requirements 12.7**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(
                provider="auto",
                model="custom-model-name",
            )
            
            assert client._resolved_model == "custom-model-name"


class TestAuxiliaryClientCalls:
    """Tests for LLM API calls."""
    
    @pytest.mark.asyncio
    async def test_call_with_openai_compatible_provider(self):
        """Should successfully call OpenAI-compatible provider.
        
        **Validates: Requirements 12.2, 12.6**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            
            # Mock the OpenAI client
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(message=MagicMock(content="Test response"))
            ]
            
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client_instance = AsyncMock()
                mock_client_instance.chat.completions.create = AsyncMock(
                    return_value=mock_response
                )
                mock_openai.return_value = mock_client_instance
                
                messages = [{"role": "user", "content": "Test message"}]
                response = await client.call(messages, task="summarization")
                
                assert response is not None
                mock_client_instance.chat.completions.create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_call_with_anthropic_provider(self):
        """Should successfully call Anthropic provider with API adaptation.
        
        **Validates: Requirements 12.2, 12.4, 12.6**
        """
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            # Mock Anthropic response
            mock_response = MagicMock()
            mock_response.id = "msg_123"
            mock_response.model = "claude-3-5-haiku-20241022"
            mock_response.stop_reason = "end_turn"
            mock_response.usage = MagicMock(
                input_tokens=10,
                output_tokens=20,
            )
            mock_content_block = MagicMock()
            mock_content_block.text = "Test response from Anthropic"
            mock_response.content = [mock_content_block]
            
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client_instance = AsyncMock()
                mock_client_instance.messages.create = AsyncMock(
                    return_value=mock_response
                )
                mock_anthropic.return_value = mock_client_instance
                
                messages = [
                    {"role": "system", "content": "You are a helpful assistant"},
                    {"role": "user", "content": "Test message"},
                ]
                response = await client.call(messages, task="compression")
                
                # Response should be adapted to OpenAI format
                assert response is not None
                assert hasattr(response, 'choices')
                assert response.choices[0].message.content == "Test response from Anthropic"
                assert response.usage.prompt_tokens == 10
                assert response.usage.completion_tokens == 20
    
    @pytest.mark.asyncio
    async def test_call_raises_when_no_provider(self):
        """Should raise RuntimeError when no provider is available.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            messages = [{"role": "user", "content": "Test"}]
            
            with pytest.raises(RuntimeError, match="No auxiliary LLM provider available"):
                await client.call(messages)
    
    @pytest.mark.asyncio
    async def test_call_vision_with_openai_compatible(self):
        """Should successfully call vision API with OpenAI-compatible provider.
        
        **Validates: Requirements 12.2, 12.6**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(message=MagicMock(content="Image shows a cat"))
            ]
            
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client_instance = AsyncMock()
                mock_client_instance.chat.completions.create = AsyncMock(
                    return_value=mock_response
                )
                mock_openai.return_value = mock_client_instance
                
                response = await client.call_vision(
                    image_data="base64encodeddata",
                    question="What's in this image?",
                )
                
                assert response == "Image shows a cat"
                mock_client_instance.chat.completions.create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_call_vision_with_anthropic(self):
        """Should successfully call vision API with Anthropic provider.
        
        **Validates: Requirements 12.2, 12.6**
        """
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            # Mock Anthropic vision response
            mock_response = MagicMock()
            mock_content_block = MagicMock()
            mock_content_block.text = "Image shows a dog"
            mock_response.content = [mock_content_block]
            
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client_instance = AsyncMock()
                mock_client_instance.messages.create = AsyncMock(
                    return_value=mock_response
                )
                mock_anthropic.return_value = mock_client_instance
                
                response = await client.call_vision(
                    image_data="base64encodeddata",
                    question="What's in this image?",
                )
                
                assert response == "Image shows a dog"
                mock_client_instance.messages.create.assert_called_once()


class TestAuxiliaryClientProperties:
    """Tests for client properties and info methods."""
    
    def test_is_available_true_when_provider_resolved(self):
        """is_available should return True when provider is resolved.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            assert client.is_available is True
    
    def test_is_available_false_when_no_provider(self):
        """is_available should return False when no provider is available.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {}, clear=True):
            client = AuxiliaryClient(provider="auto")
            assert client.is_available is False
    
    def test_provider_info_returns_correct_data(self):
        """provider_info should return provider, model, and availability.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            info = client.provider_info
            assert info["provider"] == "anthropic"
            assert "claude" in info["model"].lower()
            assert info["available"] is True
    
    def test_provider_info_when_no_provider(self):
        """provider_info should handle no provider gracefully.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            info = client.provider_info
            assert info["provider"] is None
            assert info["model"] is None
            assert info["available"] is False


class TestAuxiliaryClientSyncAsync:
    """Tests for sync and async invocation patterns."""
    
    @pytest.mark.asyncio
    async def test_async_call_pattern(self):
        """Should support async invocation pattern.
        
        **Validates: Requirements 12.5**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="Async response"))]
            
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client_instance = AsyncMock()
                mock_client_instance.chat.completions.create = AsyncMock(
                    return_value=mock_response
                )
                mock_openai.return_value = mock_client_instance
                
                messages = [{"role": "user", "content": "Test"}]
                response = await client.call(messages)
                
                assert response is not None
                # Verify it was called with await
                mock_client_instance.chat.completions.create.assert_awaited_once()


class TestAuxiliaryClientDefaultModels:
    """Tests for default model selection."""
    
    def test_get_default_model_openrouter(self):
        """Should return correct default model for OpenRouter.
        
        **Validates: Requirements 12.7**
        """
        client = AuxiliaryClient(provider="openrouter")
        default = client._get_default_model("openrouter")
        assert "claude" in default.lower() or "haiku" in default.lower()
    
    def test_get_default_model_anthropic(self):
        """Should return correct default model for Anthropic.
        
        **Validates: Requirements 12.7**
        """
        client = AuxiliaryClient(provider="anthropic")
        default = client._get_default_model("anthropic")
        assert "claude" in default.lower()
    
    def test_get_default_model_openai(self):
        """Should return correct default model for OpenAI.
        
        **Validates: Requirements 12.7**
        """
        client = AuxiliaryClient(provider="openai")
        default = client._get_default_model("openai")
        assert "gpt" in default.lower()
    
    def test_get_vision_model(self):
        """Should return vision-capable model for provider.
        
        **Validates: Requirements 12.2, 12.6**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            vision_model = client._get_vision_model()
            
            # Should be a vision-capable model
            assert vision_model is not None
            assert len(vision_model) > 0


class TestAuxiliaryClientFallback:
    """Tests for fallback chain functionality."""
    
    def test_get_fallback_providers_openrouter(self):
        """Should return correct fallback chain for OpenRouter.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            fallbacks = client.get_fallback_providers()
            
            assert isinstance(fallbacks, list)
            assert len(fallbacks) > 0
            assert "anthropic" in fallbacks or "openai" in fallbacks
    
    def test_get_fallback_providers_anthropic(self):
        """Should return correct fallback chain for Anthropic.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            client = AuxiliaryClient(provider="auto")
            fallbacks = client.get_fallback_providers()
            
            assert isinstance(fallbacks, list)
            assert len(fallbacks) > 0
    
    def test_get_fallback_providers_no_provider(self):
        """Should return empty list when no provider is available.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {}, clear=True):
            client = AuxiliaryClient(provider="auto")
            fallbacks = client.get_fallback_providers()
            
            assert fallbacks == []
    
    @pytest.mark.asyncio
    async def test_call_with_fallback_success_on_first_try(self):
        """Should succeed on first try without fallback.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="Success"))]
            
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client_instance = AsyncMock()
                mock_client_instance.chat.completions.create = AsyncMock(
                    return_value=mock_response
                )
                mock_openai.return_value = mock_client_instance
                
                messages = [{"role": "user", "content": "Test"}]
                response = await client.call_with_fallback(messages)
                
                assert response is not None
                # Should only call once (no fallback needed)
                assert mock_client_instance.chat.completions.create.call_count == 1
    
    @pytest.mark.asyncio
    async def test_call_with_fallback_on_billing_error(self):
        """Should fallback to next provider on billing error.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider="openrouter")
            
            # Mock billing error from OpenRouter
            class MockBillingError(Exception):
                def __init__(self):
                    super().__init__("insufficient credits")
                    self.status_code = 402
                    self.response = None
                    self.__module__ = "openai"
            
            # Mock successful response from Anthropic
            mock_anthropic_response = MagicMock()
            mock_content_block = MagicMock()
            mock_content_block.text = "Fallback success"
            mock_anthropic_response.id = "msg_123"
            mock_anthropic_response.model = "claude-3-5-haiku-20241022"
            mock_anthropic_response.stop_reason = "end_turn"
            mock_anthropic_response.usage = MagicMock(input_tokens=10, output_tokens=20)
            mock_anthropic_response.content = [mock_content_block]
            
            # Mock the error classifier to return billing error
            from app.core.error_classifier import ClassifiedError, FailoverReason
            mock_classified = ClassifiedError(
                reason=FailoverReason.BILLING,
                status_code=402,
                message="insufficient credits",
                retryable=False,
                should_fallback=True,
            )
            
            with patch("openai.AsyncOpenAI") as mock_openai, \
                 patch("anthropic.AsyncAnthropic") as mock_anthropic, \
                 patch("app.core.auxiliary_client.classify_error", return_value=mock_classified):
                
                # OpenRouter fails with billing error
                mock_openai_instance = AsyncMock()
                mock_openai_instance.chat.completions.create = AsyncMock(
                    side_effect=MockBillingError()
                )
                mock_openai.return_value = mock_openai_instance
                
                # Anthropic succeeds
                mock_anthropic_instance = AsyncMock()
                mock_anthropic_instance.messages.create = AsyncMock(
                    return_value=mock_anthropic_response
                )
                mock_anthropic.return_value = mock_anthropic_instance
                
                messages = [{"role": "user", "content": "Test"}]
                response = await client.call_with_fallback(messages)
                
                # Should succeed with fallback
                assert response is not None
                # Anthropic should have been called
                mock_anthropic_instance.messages.create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_call_with_fallback_raises_on_non_fallback_error(self):
        """Should raise immediately on non-fallback errors.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            
            # Mock a non-fallback error (e.g., format error)
            class MockFormatError(Exception):
                def __init__(self):
                    super().__init__("bad request")
                    self.status_code = 400
                    self.response = None
                    self.__module__ = "openai"
            
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client_instance = AsyncMock()
                mock_client_instance.chat.completions.create = AsyncMock(
                    side_effect=MockFormatError()
                )
                mock_openai.return_value = mock_client_instance
                
                messages = [{"role": "user", "content": "Test"}]
                
                # Should raise without attempting fallback
                with pytest.raises(MockFormatError):
                    await client.call_with_fallback(messages)
    
    @pytest.mark.asyncio
    async def test_call_with_fallback_raises_when_all_fail(self):
        """Should raise RuntimeError when all providers fail.
        
        **Validates: Requirements 12.3**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider="openrouter")
            
            # Mock billing error for all providers
            class MockBillingError(Exception):
                def __init__(self):
                    super().__init__("insufficient credits")
                    self.status_code = 402
                    self.response = None
                    self.__module__ = "openai"
            
            # Mock the error classifier to return billing error
            from app.core.error_classifier import ClassifiedError, FailoverReason
            mock_classified = ClassifiedError(
                reason=FailoverReason.BILLING,
                status_code=402,
                message="insufficient credits",
                retryable=False,
                should_fallback=True,
            )
            
            with patch("openai.AsyncOpenAI") as mock_openai, \
                 patch("anthropic.AsyncAnthropic") as mock_anthropic, \
                 patch("app.core.auxiliary_client.classify_error", return_value=mock_classified):
                
                # All providers fail
                mock_openai_instance = AsyncMock()
                mock_openai_instance.chat.completions.create = AsyncMock(
                    side_effect=MockBillingError()
                )
                mock_openai.return_value = mock_openai_instance
                
                mock_anthropic_instance = AsyncMock()
                mock_anthropic_instance.messages.create = AsyncMock(
                    side_effect=MockBillingError()
                )
                mock_anthropic.return_value = mock_anthropic_instance
                
                messages = [{"role": "user", "content": "Test"}]
                
                # Should raise RuntimeError after all fallbacks fail
                with pytest.raises(RuntimeError, match="All auxiliary providers failed"):
                    await client.call_with_fallback(messages)
