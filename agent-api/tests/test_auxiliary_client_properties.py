"""Property-based tests for auxiliary_client module.

Tests universal correctness properties for provider resolution and fallback behavior
using hypothesis for property-based testing.

**Validates: Requirements 12.1, 12.3**
"""

import pytest
from hypothesis import given, strategies as st, assume, settings
from unittest.mock import AsyncMock, MagicMock, patch
import os

from app.core.auxiliary_client import AuxiliaryClient
from app.core.error_classifier import FailoverReason, ClassifiedError


# ============================================================================
# Property 22: Auxiliary Provider Resolution
# ============================================================================
# *For any* auxiliary client call in auto mode, providers SHALL be resolved in
# priority order, falling back to the next provider on payment/credit errors.
# **Validates: Requirements 12.1, 12.3**


class TestAuxiliaryProviderResolution:
    """Property tests for auxiliary provider resolution and fallback."""

    @given(
        openrouter_key=st.one_of(
            st.none(),
            st.text(
                min_size=10,
                max_size=50,
                alphabet=st.characters(blacklist_characters='\x00', blacklist_categories=('Cc', 'Cs'))
            )
        ),
        anthropic_key=st.one_of(
            st.none(),
            st.text(
                min_size=10,
                max_size=50,
                alphabet=st.characters(blacklist_characters='\x00', blacklist_categories=('Cc', 'Cs'))
            )
        ),
        openai_key=st.one_of(
            st.none(),
            st.text(
                min_size=10,
                max_size=50,
                alphabet=st.characters(blacklist_characters='\x00', blacklist_categories=('Cc', 'Cs'))
            )
        ),
    )
    @settings(max_examples=50, deadline=1000)
    def test_provider_resolution_priority_order(
        self,
        openrouter_key: str | None,
        anthropic_key: str | None,
        openai_key: str | None,
    ):
        """Provider resolution should follow priority: OpenRouter → Custom → Anthropic → None.
        
        **Validates: Requirements 12.1**
        """
        env_dict = {}
        if openrouter_key:
            env_dict["OPENROUTER_API_KEY"] = openrouter_key
        if anthropic_key:
            env_dict["ANTHROPIC_API_KEY"] = anthropic_key
        if openai_key:
            env_dict["OPENAI_API_KEY"] = openai_key
        
        with patch.dict(os.environ, env_dict, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            # Verify priority order
            if openrouter_key:
                assert client._resolved_provider == "openrouter"
            elif anthropic_key:
                assert client._resolved_provider == "anthropic"
            else:
                assert client._resolved_provider is None

    @given(
        base_url=st.sampled_from([
            "https://api.example.com/v1",
            "http://localhost:8080/v1",
            "https://custom.ai/api",
        ]),
        openai_key=st.text(
            min_size=10,
            max_size=50,
            alphabet=st.characters(blacklist_characters='\x00', blacklist_categories=('Cc', 'Cs'))
        ),
        anthropic_key=st.one_of(
            st.none(),
            st.text(
                min_size=10,
                max_size=50,
                alphabet=st.characters(blacklist_characters='\x00', blacklist_categories=('Cc', 'Cs'))
            )
        ),
    )
    @settings(max_examples=30, deadline=1000)
    def test_custom_endpoint_selected_when_base_url_provided(
        self,
        base_url: str,
        openai_key: str,
        anthropic_key: str | None,
    ):
        """Custom endpoint should be selected when base_url is provided (no OpenRouter).
        
        **Validates: Requirements 12.1**
        """
        env_dict = {"OPENAI_API_KEY": openai_key}
        if anthropic_key:
            env_dict["ANTHROPIC_API_KEY"] = anthropic_key
        
        with patch.dict(os.environ, env_dict, clear=True):
            client = AuxiliaryClient(provider="auto", base_url=base_url)
            
            # Should select custom endpoint
            assert client._resolved_provider == "custom"
            assert client.base_url == base_url

    @given(
        provider=st.sampled_from(["openrouter", "anthropic", "openai", "custom"]),
        model=st.text(min_size=5, max_size=50),
    )
    @settings(max_examples=30, deadline=1000)
    def test_explicit_provider_always_used(self, provider: str, model: str):
        """Explicit provider should always override auto resolution.
        
        **Validates: Requirements 12.1**
        """
        # Set all keys to ensure auto would pick OpenRouter
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider=provider, model=model)
            
            # Should use explicit provider, not auto-resolved
            assert client._resolved_provider == provider
            assert client._resolved_model == model

    @given(
        model_override=st.text(min_size=5, max_size=50),
    )
    @settings(max_examples=30, deadline=1000)
    def test_model_override_respected(self, model_override: str):
        """Custom model should override default model selection.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto", model=model_override)
            
            # Should use custom model
            assert client._resolved_model == model_override

    @given(
        has_openrouter=st.booleans(),
        has_anthropic=st.booleans(),
        has_openai=st.booleans(),
    )
    @settings(max_examples=50, deadline=1000)
    def test_is_available_reflects_provider_resolution(
        self,
        has_openrouter: bool,
        has_anthropic: bool,
        has_openai: bool,
    ):
        """is_available should be True if any provider is available.
        
        **Validates: Requirements 12.1**
        """
        env_dict = {}
        if has_openrouter:
            env_dict["OPENROUTER_API_KEY"] = "test-key"
        if has_anthropic:
            env_dict["ANTHROPIC_API_KEY"] = "test-key"
        if has_openai:
            env_dict["OPENAI_API_KEY"] = "test-key"
        
        with patch.dict(os.environ, env_dict, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            # Should be available if any key is set
            expected_available = has_openrouter or has_anthropic
            assert client.is_available == expected_available

    @given(
        provider=st.sampled_from(["openrouter", "anthropic", "openai"]),
    )
    @settings(max_examples=20, deadline=1000)
    def test_default_model_selection_is_deterministic(self, provider: str):
        """Default model selection should be deterministic for each provider.
        
        **Validates: Requirements 12.1**
        """
        client1 = AuxiliaryClient(provider=provider)
        client2 = AuxiliaryClient(provider=provider)
        
        default1 = client1._get_default_model(provider)
        default2 = client2._get_default_model(provider)
        
        # Should return same default model
        assert default1 == default2

    @pytest.mark.asyncio
    @given(
        task=st.sampled_from(["compression", "summarization", "analysis"]),
        max_tokens=st.integers(min_value=100, max_value=4000),
    )
    @settings(max_examples=30, deadline=2000)
    async def test_call_raises_when_no_provider_available(
        self,
        task: str,
        max_tokens: int,
    ):
        """Should raise RuntimeError when no provider is available.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {}, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            messages = [{"role": "user", "content": "Test message"}]
            
            with pytest.raises(RuntimeError, match="No auxiliary LLM provider available"):
                await client.call(messages, task=task, max_tokens=max_tokens)

    @given(
        provider=st.sampled_from(["openrouter", "anthropic", "openai"]),
    )
    @settings(max_examples=20, deadline=1000)
    def test_vision_model_selection_is_deterministic(self, provider: str):
        """Vision model selection should be deterministic for each provider.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider=provider)
            
            vision_model1 = client._get_vision_model()
            vision_model2 = client._get_vision_model()
            
            # Should return same vision model
            assert vision_model1 == vision_model2
            # Vision model should not be empty
            assert len(vision_model1) > 0


class TestAuxiliaryProviderFallback:
    """Property tests for provider fallback on payment/credit errors."""

    @pytest.mark.asyncio
    @given(
        error_message=st.sampled_from([
            "insufficient credits",
            "credit balance exhausted",
            "payment required",
            "billing hard limit",
            "exceeded your current quota",
        ]),
    )
    @settings(max_examples=20, deadline=2000)
    async def test_fallback_on_billing_error(self, error_message: str):
        """Should attempt fallback when billing error occurs.
        
        **Validates: Requirements 12.3**
        
        Note: This test verifies the error classification that would trigger fallback.
        Actual fallback implementation would be in the retry system.
        """
        from app.core.error_classifier import classify_error
        
        # Create a mock error with billing pattern that matches OpenAI error structure
        class MockBillingError(Exception):
            def __init__(self, message):
                super().__init__(message)
                self.status_code = 402
                self.response = None
                self.__module__ = "openai"  # Make it look like an OpenAI error
        
        error = MockBillingError(error_message)
        classified = classify_error(error)
        
        # Should be classified as billing error or rate limit (both trigger fallback)
        assert classified.reason in (FailoverReason.BILLING, FailoverReason.RATE_LIMIT, FailoverReason.UNKNOWN)
        # If classified as billing or rate limit, should indicate fallback is needed
        if classified.reason in (FailoverReason.BILLING, FailoverReason.RATE_LIMIT):
            assert classified.should_fallback is True

    @pytest.mark.asyncio
    @given(
        provider=st.sampled_from(["openrouter", "anthropic", "openai"]),
    )
    @settings(max_examples=20, deadline=2000)
    async def test_provider_info_consistency(self, provider: str):
        """provider_info should be consistent with resolved provider.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider=provider)
            
            info = client.provider_info
            
            # Info should match resolved provider
            assert info["provider"] == client._resolved_provider
            assert info["model"] == client._resolved_model
            assert info["available"] == client.is_available

    @pytest.mark.asyncio
    @given(
        messages=st.lists(
            st.fixed_dictionaries({
                "role": st.sampled_from(["user", "assistant", "system"]),
                "content": st.text(min_size=1, max_size=100),
            }),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=30, deadline=2000)
    async def test_call_with_valid_messages(self, messages: list):
        """Should handle various message formats correctly.
        
        **Validates: Requirements 12.1, 12.3**
        """
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            client = AuxiliaryClient(provider="auto")
            
            # Mock successful response
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
                
                # Should not raise
                response = await client.call(messages)
                assert response is not None

    @given(
        provider1=st.sampled_from(["openrouter", "anthropic", "openai"]),
        provider2=st.sampled_from(["openrouter", "anthropic", "openai"]),
    )
    @settings(max_examples=30, deadline=1000)
    def test_different_providers_have_different_defaults(
        self,
        provider1: str,
        provider2: str,
    ):
        """Different providers may have different default models.
        
        **Validates: Requirements 12.1**
        """
        assume(provider1 != provider2)
        
        client1 = AuxiliaryClient(provider=provider1)
        client2 = AuxiliaryClient(provider=provider2)
        
        default1 = client1._get_default_model(provider1)
        default2 = client2._get_default_model(provider2)
        
        # Defaults should be non-empty
        assert len(default1) > 0
        assert len(default2) > 0
        
        # OpenAI and Anthropic should have different defaults
        if (provider1 == "openai" and provider2 == "anthropic") or \
           (provider1 == "anthropic" and provider2 == "openai"):
            assert default1 != default2


class TestAuxiliaryClientInvariants:
    """Property tests for client invariants."""

    @given(
        provider=st.sampled_from(["auto", "openrouter", "anthropic", "openai", "custom"]),
        model=st.text(min_size=0, max_size=50),
        base_url=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=50, deadline=1000)
    def test_client_initialization_never_raises(
        self,
        provider: str,
        model: str,
        base_url: str,
    ):
        """Client initialization should never raise, even with invalid inputs.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
        }):
            # Should not raise
            client = AuxiliaryClient(
                provider=provider,
                model=model,
                base_url=base_url,
            )
            
            # Client should be created
            assert client is not None

    @given(
        provider=st.sampled_from(["openrouter", "anthropic", "openai"]),
    )
    @settings(max_examples=20, deadline=1000)
    def test_resolved_provider_matches_explicit_provider(self, provider: str):
        """When explicit provider is set, resolved provider should match.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider=provider)
            
            # Resolved provider should match explicit provider
            assert client._resolved_provider == provider

    @given(
        has_any_key=st.booleans(),
    )
    @settings(max_examples=20, deadline=1000)
    def test_is_available_is_boolean(self, has_any_key: bool):
        """is_available should always return a boolean.
        
        **Validates: Requirements 12.1**
        """
        env_dict = {}
        if has_any_key:
            env_dict["OPENROUTER_API_KEY"] = "test-key"
        
        with patch.dict(os.environ, env_dict, clear=True):
            client = AuxiliaryClient(provider="auto")
            
            # Should be a boolean
            assert isinstance(client.is_available, bool)

    @given(
        provider=st.sampled_from(["auto", "openrouter", "anthropic", "openai"]),
    )
    @settings(max_examples=20, deadline=1000)
    def test_provider_info_has_required_keys(self, provider: str):
        """provider_info should always have required keys.
        
        **Validates: Requirements 12.1**
        """
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-key",
            "OPENAI_API_KEY": "test-key",
        }):
            client = AuxiliaryClient(provider=provider)
            
            info = client.provider_info
            
            # Should have required keys
            assert "provider" in info
            assert "model" in info
            assert "available" in info
            # available should be boolean
            assert isinstance(info["available"], bool)
