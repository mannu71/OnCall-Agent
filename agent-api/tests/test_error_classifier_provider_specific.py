"""Tests for provider-specific error patterns in error classifier.

Tests for:
- Anthropic thinking signature error detection
- Anthropic long-context tier gate detection
- vLLM, Ollama, llama.cpp context overflow patterns
"""
import pytest
import sys
from types import ModuleType
from app.core.error_classifier import classify_error, FailoverReason


# Set up mock anthropic module
if 'anthropic' not in sys.modules:
    sys.modules['anthropic'] = ModuleType('anthropic')
if 'anthropic.errors' not in sys.modules:
    sys.modules['anthropic.errors'] = ModuleType('anthropic.errors')


# Create mock error class with proper module
class AnthropicAPIError(Exception):
    """Mock Anthropic API error for testing."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# Set the module after class definition
AnthropicAPIError.__module__ = 'anthropic.errors'


class TestAnthropicThinkingSignature:
    """Test Anthropic thinking signature error detection."""
    
    def test_thinking_signature_error_400(self):
        """400 with 'signature' + 'thinking' → THINKING_SIGNATURE."""
        error = AnthropicAPIError(
            "thinking block has invalid signature",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.THINKING_SIGNATURE
        assert result.retryable is True
        assert result.should_compress is False
    
    def test_thinking_signature_error_case_insensitive(self):
        """Thinking signature detection is case-insensitive."""
        error = AnthropicAPIError(
            "Thinking block has Invalid Signature",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.THINKING_SIGNATURE
    
    def test_signature_without_thinking_not_thinking_signature(self):
        """'signature' alone without 'thinking' → not THINKING_SIGNATURE."""
        error = AnthropicAPIError(
            "invalid signature",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason != FailoverReason.THINKING_SIGNATURE
    
    def test_thinking_without_signature_not_thinking_signature(self):
        """'thinking' alone without 'signature' → not THINKING_SIGNATURE."""
        error = AnthropicAPIError(
            "thinking block is invalid",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason != FailoverReason.THINKING_SIGNATURE


class TestAnthropicLongContextTier:
    """Test Anthropic long-context tier gate detection."""
    
    def test_long_context_tier_error_429(self):
        """429 with 'extra usage' + 'long context' → LONG_CONTEXT_TIER."""
        error = AnthropicAPIError(
            "Extra usage is required for long context requests over 200k tokens",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.LONG_CONTEXT_TIER
        assert result.retryable is True
        assert result.should_compress is True
    
    def test_long_context_tier_case_insensitive(self):
        """Long context tier detection is case-insensitive."""
        error = AnthropicAPIError(
            "extra usage is required for long context requests",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.LONG_CONTEXT_TIER
    
    def test_extra_usage_without_long_context_not_tier_gate(self):
        """'extra usage' alone without 'long context' → not LONG_CONTEXT_TIER."""
        error = AnthropicAPIError(
            "extra usage required",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason != FailoverReason.LONG_CONTEXT_TIER
    
    def test_long_context_without_extra_usage_not_tier_gate(self):
        """'long context' alone without 'extra usage' → not LONG_CONTEXT_TIER."""
        error = AnthropicAPIError(
            "long context requests are not supported",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason != FailoverReason.LONG_CONTEXT_TIER
    
    def test_normal_429_not_long_context_tier(self):
        """Normal 429 without tier gate patterns → RATE_LIMIT."""
        error = AnthropicAPIError(
            "Too Many Requests",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.RATE_LIMIT


class TestLocalInferenceServerContextOverflow:
    """Test context overflow patterns for vLLM, Ollama, llama.cpp."""
    
    def test_vllm_max_model_len_pattern(self):
        """vLLM 'exceeds the max_model_len' → CONTEXT_OVERFLOW."""
        error = AnthropicAPIError(
            "prompt length 150000 exceeds the max_model_len 128000",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
        assert result.should_compress is True
    
    def test_ollama_truncating_input_pattern(self):
        """Ollama 'truncating input' → CONTEXT_OVERFLOW."""
        error = AnthropicAPIError(
            "context length exceeded, truncating input",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
    
    def test_llama_cpp_slot_context_pattern(self):
        """llama.cpp 'slot context' → CONTEXT_OVERFLOW."""
        error = AnthropicAPIError(
            "slot context: 8192 tokens, prompt 10000 tokens",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
    
    def test_llama_cpp_n_ctx_slot_pattern(self):
        """llama.cpp 'n_ctx_slot' → CONTEXT_OVERFLOW."""
        error = AnthropicAPIError(
            "n_ctx_slot exceeded",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
    
    def test_generic_prompt_length_pattern(self):
        """Generic 'prompt length' → CONTEXT_OVERFLOW."""
        error = AnthropicAPIError(
            "prompt length exceeds maximum",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW


class TestProviderSpecificPriority:
    """Test that provider-specific patterns have correct priority."""
    
    def test_thinking_signature_takes_priority_over_generic_400(self):
        """Thinking signature error should not fall through to generic 400."""
        error = AnthropicAPIError(
            "thinking block has invalid signature",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.THINKING_SIGNATURE
        # Should NOT be FORMAT_ERROR or UNKNOWN
        assert result.reason not in (FailoverReason.FORMAT_ERROR, FailoverReason.UNKNOWN)
    
    def test_long_context_tier_takes_priority_over_rate_limit(self):
        """Long context tier should not fall through to generic rate limit."""
        error = AnthropicAPIError(
            "extra usage is required for long context requests",
            status_code=429
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.LONG_CONTEXT_TIER
        # Should NOT be RATE_LIMIT
        assert result.reason != FailoverReason.RATE_LIMIT
    
    def test_vllm_context_overflow_detected(self):
        """vLLM-specific patterns should be detected."""
        error = AnthropicAPIError(
            "exceeds the max_model_len",
            status_code=400
        )
        result = classify_error(error)
        assert result.reason == FailoverReason.CONTEXT_OVERFLOW
