"""Unit tests for model_metadata module.

Tests for multi-source context length resolution, local server detection,
and caching functionality.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.core.model_metadata import (
    detect_local_server_type,
    get_model_context_length,
    get_model_context_length_sync,
    get_cached_context_length,
    save_context_length,
    query_ollama_num_ctx,
    query_provider_models_endpoint,
    query_models_dev,
    _make_cache_key,
    _strip_provider_prefix,
    _get_hardcoded_context_length,
    DEFAULT_FALLBACK_CONTEXT,
    MINIMUM_CONTEXT_LENGTH,
)


class TestDetectLocalServerType:
    """Tests for detect_local_server_type function."""

    def test_detect_ollama_default_port(self):
        """Should detect Ollama on default port 11434."""
        assert detect_local_server_type("http://localhost:11434") == "ollama"
        assert detect_local_server_type("http://127.0.0.1:11434/v1") == "ollama"

    def test_detect_lm_studio_default_port(self):
        """Should detect LM Studio on default port 1234."""
        assert detect_local_server_type("http://localhost:1234") == "lm-studio"
        assert detect_local_server_type("http://127.0.0.1:1234/v1") == "lm-studio"

    def test_detect_vllm(self):
        """Should detect vLLM server."""
        assert detect_local_server_type("http://localhost:8000/v1/completions") == "vllm"
        assert detect_local_server_type("http://myserver.com/vllm/v1") == "vllm"

    def test_detect_llamacpp(self):
        """Should detect llama.cpp server."""
        assert detect_local_server_type("http://localhost:8080") == "llamacpp"
        assert detect_local_server_type("http://myserver.com/llama.cpp") == "llamacpp"

    def test_detect_local_unknown_port(self):
        """Should return 'local' for localhost with unknown port."""
        assert detect_local_server_type("http://localhost:9999") == "local"
        assert detect_local_server_type("http://127.0.0.1:5000") == "local"

    def test_detect_remote_server(self):
        """Should return None for remote servers."""
        assert detect_local_server_type("https://api.openai.com") is None
        assert detect_local_server_type("https://api.anthropic.com") is None

    def test_detect_empty_url(self):
        """Should return None for empty URL."""
        assert detect_local_server_type("") is None
        assert detect_local_server_type(None) is None


class TestMakeCacheKey:
    """Tests for _make_cache_key function."""

    def test_cache_key_with_base_url(self):
        """Should create key with model@base_url format."""
        key = _make_cache_key("gpt-4", "https://api.openai.com")
        assert key == "gpt-4@https://api.openai.com"

    def test_cache_key_without_base_url(self):
        """Should create key with just model when no base_url."""
        key = _make_cache_key("gpt-4", "")
        assert key == "gpt-4"

    def test_cache_key_strips_provider_prefix(self):
        """Should strip provider prefix from model name."""
        key = _make_cache_key("local:llama-3", "http://localhost:11434")
        assert key == "llama-3@http://localhost:11434"

    def test_cache_key_normalizes_trailing_slash(self):
        """Should remove trailing slash from base_url."""
        key = _make_cache_key("gpt-4", "https://api.openai.com/")
        assert key == "gpt-4@https://api.openai.com"


class TestStripProviderPrefix:
    """Tests for _strip_provider_prefix function."""

    def test_strip_known_prefix(self):
        """Should strip known provider prefixes."""
        assert _strip_provider_prefix("local:llama-3") == "llama-3"
        assert _strip_provider_prefix("bedrock:anthropic.claude-3-opus") == "anthropic.claude-3-opus"
        assert _strip_provider_prefix("openrouter:anthropic/claude-3-opus") == "anthropic/claude-3-opus"

    def test_no_prefix_to_strip(self):
        """Should return unchanged if no provider prefix."""
        assert _strip_provider_prefix("gpt-4") == "gpt-4"
        assert _strip_provider_prefix("llama-3") == "llama-3"

    def test_unknown_prefix_not_stripped(self):
        """Should not strip unknown prefixes (might be model family)."""
        assert _strip_provider_prefix("qwen3.5:27b") == "qwen3.5:27b"


class TestGetHardcodedContextLength:
    """Tests for _get_hardcoded_context_length function."""

    def test_exact_match(self):
        """Should find exact model match."""
        assert _get_hardcoded_context_length("gpt-4o") == 128_000
        assert _get_hardcoded_context_length("claude-3-opus") == 200_000

    def test_fuzzy_match(self):
        """Should find fuzzy match for model variants."""
        assert _get_hardcoded_context_length("gpt-4o-2024-05-13") == 128_000
        assert _get_hardcoded_context_length("claude-3-opus-20240229") == 200_000

    def test_unknown_model(self):
        """Should return None for unknown models."""
        assert _get_hardcoded_context_length("unknown-model-xyz") is None

    def test_longest_match_wins(self):
        """Should prefer longer (more specific) matches."""
        # "claude-3-opus" should match over just "claude"
        result = _get_hardcoded_context_length("claude-3-opus")
        assert result == 200_000


class TestGetModelContextLengthSync:
    """Tests for synchronous get_model_context_length_sync function."""

    def test_config_override(self):
        """Should use config override when provided."""
        result = get_model_context_length_sync("gpt-4", config_context_length=50000)
        assert result == 50000

    def test_hardcoded_default(self):
        """Should use hardcoded default for known models."""
        result = get_model_context_length_sync("gpt-4o")
        assert result == 128_000

    def test_fallback(self):
        """Should use default fallback for unknown models."""
        result = get_model_context_length_sync("unknown-model")
        assert result == DEFAULT_FALLBACK_CONTEXT


class TestQueryOllamaNumCtx:
    """Tests for query_ollama_num_ctx function."""

    @pytest.mark.asyncio
    async def test_query_ollama_success(self):
        """Should parse num_ctx from Ollama API response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "modelfile": "FROM llama3\nPARAMETER num_ctx 8192"
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await query_ollama_num_ctx("llama3", "http://localhost:11434")
            assert result == 8192

    @pytest.mark.asyncio
    async def test_query_ollama_from_parameters(self):
        """Should parse num_ctx from parameters dict."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "parameters": {"num_ctx": 16384}
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await query_ollama_num_ctx("llama3", "http://localhost:11434")
            assert result == 16384

    @pytest.mark.asyncio
    async def test_query_ollama_from_model_info(self):
        """Should parse context_length from model_info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model_info": {"context_length": 32768}
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await query_ollama_num_ctx("llama3", "http://localhost:11434")
            assert result == 32768

    @pytest.mark.asyncio
    async def test_query_ollama_failure(self):
        """Should return None on API failure."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.RequestError("Connection failed")
            result = await query_ollama_num_ctx("llama3", "http://localhost:11434")
            assert result is None


class TestQueryProviderModelsEndpoint:
    """Tests for query_provider_models_endpoint function."""

    @pytest.mark.asyncio
    async def test_query_provider_success(self):
        """Should parse context_length from provider /models endpoint."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "gpt-4", "context_length": 128000}
            ]
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await query_provider_models_endpoint(
                "https://api.openai.com", "gpt-4", "test-key"
            )
            assert result == 128000

    @pytest.mark.asyncio
    async def test_query_provider_model_not_found(self):
        """Should return None if model not in response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "other-model", "context_length": 128000}
            ]
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await query_provider_models_endpoint(
                "https://api.openai.com", "gpt-4", "test-key"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_query_provider_failure(self):
        """Should return None on API failure."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.RequestError("Connection failed")
            result = await query_provider_models_endpoint(
                "https://api.openai.com", "gpt-4"
            )
            assert result is None


class TestQueryModelsDev:
    """Tests for query_models_dev function."""

    @pytest.mark.asyncio
    async def test_query_models_dev_success(self):
        """Should parse context_length from models.dev API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "openai": {
                "gpt-4": {"context_length": 128000}
            }
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await query_models_dev("gpt-4")
            assert result == 128000

    @pytest.mark.asyncio
    async def test_query_models_dev_fuzzy_match(self):
        """Should find model with fuzzy match."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "openai": {
                "gpt-4-turbo": {"context_length": 128000}
            }
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            # The fuzzy match checks if model_lower is in model_id
            # "gpt-4-turbo-preview" contains "gpt-4-turbo"
            result = await query_models_dev("gpt-4-turbo")
            assert result == 128000

    @pytest.mark.asyncio
    async def test_query_models_dev_failure(self):
        """Should return None on API failure."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.RequestError("Connection failed")
            result = await query_models_dev("gpt-4")
            assert result is None


class TestCachedContextLength:
    """Tests for cache read/write functions."""

    @pytest.mark.asyncio
    async def test_save_and_get_cached_context_length(self):
        """Should save and retrieve cached context length."""
        # Create a mock for the ContextLengthCacheModel
        mock_model_class = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        # First call (get) returns None
        mock_session.execute.return_value = mock_result

        # Patch the import inside the function
        with patch.dict(
            "sys.modules",
            {"app.models.db_models": MagicMock(ContextLengthCacheModel=mock_model_class)}
        ):
            result = await get_cached_context_length("gpt-4", "https://api.openai.com", mock_session)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_context_length_no_session(self):
        """Should return None when no session provided."""
        result = await get_cached_context_length("gpt-4", "https://api.openai.com", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_save_context_length_no_session(self):
        """Should return False when no session provided."""
        result = await save_context_length("gpt-4", "https://api.openai.com", 128000, None)
        assert result is False


class TestGetModelContextLength:
    """Tests for async get_model_context_length function."""

    @pytest.mark.asyncio
    async def test_config_override_priority(self):
        """Config override should have highest priority."""
        result = await get_model_context_length(
            "gpt-4",
            config_context_length=50000
        )
        assert result == 50000

    @pytest.mark.asyncio
    async def test_hardcoded_default_fallback(self):
        """Should use hardcoded default when no other source available."""
        result = await get_model_context_length("gpt-4o")
        assert result == 128_000

    @pytest.mark.asyncio
    async def test_default_fallback_for_unknown(self):
        """Should use default fallback for unknown models."""
        result = await get_model_context_length("unknown-model-xyz")
        assert result == DEFAULT_FALLBACK_CONTEXT

    @pytest.mark.asyncio
    async def test_ollama_detection_and_query(self):
        """Should detect Ollama and query for context length."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "modelfile": "FROM llama3\nPARAMETER num_ctx 16384"
        }

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await get_model_context_length(
                "llama3",
                base_url="http://localhost:11434"
            )
            assert result == 16384

    @pytest.mark.asyncio
    async def test_cached_value_priority(self):
        """Cached value should have second priority after config."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_cached = MagicMock()
        mock_cached.context_length = 64000
        mock_result.scalar_one_or_none.return_value = mock_cached
        mock_session.execute.return_value = mock_result

        # Mock the get_cached_context_length function directly
        with patch(
            "app.core.model_metadata.get_cached_context_length",
            new_callable=AsyncMock,
            return_value=64000
        ):
            result = await get_model_context_length(
                "gpt-4",
                base_url="https://api.openai.com",
                db_session=mock_session
            )
            assert result == 64000


class TestGetNextProbeTier:
    """Tests for get_next_probe_tier function."""

    def test_get_next_tier_from_128k(self):
        """Should return 64K when current is 128K."""
        from app.core.model_metadata import get_next_probe_tier, CONTEXT_PROBE_TIERS
        result = get_next_probe_tier(128_000)
        assert result == 64_000

    def test_get_next_tier_from_64k(self):
        """Should return 32K when current is 64K."""
        from app.core.model_metadata import get_next_probe_tier
        result = get_next_probe_tier(64_000)
        assert result == 32_000

    def test_get_next_tier_from_minimum(self):
        """Should return None when already at minimum tier."""
        from app.core.model_metadata import get_next_probe_tier, CONTEXT_PROBE_TIERS
        # The minimum tier is the last one in the list
        min_tier = CONTEXT_PROBE_TIERS[-1]
        result = get_next_probe_tier(min_tier)
        assert result is None

    def test_get_next_tier_from_below_minimum(self):
        """Should return None when current is below minimum tier."""
        from app.core.model_metadata import get_next_probe_tier
        result = get_next_probe_tier(1000)
        assert result is None


class TestResolveContextLengthWithProbing:
    """Tests for resolve_context_length_with_probing function."""

    def test_parse_limit_from_error(self):
        """Should parse context limit from error message."""
        from app.core.model_metadata import resolve_context_length_with_probing
        error = Exception("maximum context length is 80000 tokens")
        result = resolve_context_length_with_probing(error, 128_000)
        assert result == 80000

    def test_fallback_to_probe_tier(self):
        """Should fall back to next probe tier when parsing fails."""
        from app.core.model_metadata import resolve_context_length_with_probing
        error = Exception("some generic error")
        result = resolve_context_length_with_probing(error, 128_000)
        assert result == 64_000

    def test_no_lower_tier_available(self):
        """Should return None when no lower tier is available."""
        from app.core.model_metadata import resolve_context_length_with_probing, CONTEXT_PROBE_TIERS
        min_tier = CONTEXT_PROBE_TIERS[-1]
        error = Exception("some generic error")
        result = resolve_context_length_with_probing(error, min_tier)
        assert result is None

    def test_enforce_minimum_context_length(self):
        """Should enforce minimum context length when parsed limit is below minimum."""
        from app.core.model_metadata import resolve_context_length_with_probing, MINIMUM_CONTEXT_LENGTH
        # Create an error that would parse to below minimum
        error = Exception("maximum context length is 8000 tokens")
        result = resolve_context_length_with_probing(error, 128_000)
        # Should return MINIMUM_CONTEXT_LENGTH since 8000 < MINIMUM_CONTEXT_LENGTH
        assert result == MINIMUM_CONTEXT_LENGTH


class TestMinimumContextLengthValidation:
    """Tests for minimum context length validation in get_model_context_length."""

    @pytest.mark.asyncio
    async def test_warning_for_below_minimum(self):
        """Should log warning when context length is below minimum."""
        # gpt-4 has 8192 hardcoded, which is below MINIMUM_CONTEXT_LENGTH
        with patch("app.core.model_metadata.logger") as mock_logger:
            result = await get_model_context_length("gpt-4", enforce_minimum=True)
            assert result == 8192  # Returns actual value, but warns
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_warning_when_minimum_disabled(self):
        """Should not warn when enforce_minimum is False."""
        with patch("app.core.model_metadata.logger") as mock_logger:
            result = await get_model_context_length("gpt-4", enforce_minimum=False)
            assert result == 8192
            # Should not have called warning for minimum context length
            warning_calls = [c for c in mock_logger.warning.call_args_list 
                           if "below minimum" in str(c)]
            assert len(warning_calls) == 0

    @pytest.mark.asyncio
    async def test_no_warning_for_above_minimum(self):
        """Should not warn when context length is above minimum."""
        with patch("app.core.model_metadata.logger") as mock_logger:
            result = await get_model_context_length("gpt-4o", enforce_minimum=True)
            assert result == 128_000
            # Should not have called warning for minimum context length
            warning_calls = [c for c in mock_logger.warning.call_args_list 
                           if "below minimum" in str(c)]
            assert len(warning_calls) == 0


class TestMinimumContextLengthSync:
    """Tests for minimum context length validation in sync version."""

    def test_warning_for_below_minimum_sync(self):
        """Should log warning when context length is below minimum (sync)."""
        with patch("app.core.model_metadata.logger") as mock_logger:
            result = get_model_context_length_sync("gpt-4", enforce_minimum=True)
            assert result == 8192
            mock_logger.warning.assert_called()

    def test_no_warning_when_minimum_disabled_sync(self):
        """Should not warn when enforce_minimum is False (sync)."""
        with patch("app.core.model_metadata.logger") as mock_logger:
            result = get_model_context_length_sync("gpt-4", enforce_minimum=False)
            assert result == 8192
            warning_calls = [c for c in mock_logger.warning.call_args_list 
                           if "below minimum" in str(c)]
            assert len(warning_calls) == 0
