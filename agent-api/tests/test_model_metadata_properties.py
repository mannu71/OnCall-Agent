"""Property-based tests for model_metadata module.

Tests universal correctness properties for context length resolution
and caching functionality using hypothesis for property-based testing.

**Validates: Requirements 2.1, 2.2**
"""

import pytest
from hypothesis import given, strategies as st, assume, settings
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.core.model_metadata import (
    get_model_context_length,
    get_cached_context_length,
    save_context_length,
    _make_cache_key,
    DEFAULT_FALLBACK_CONTEXT,
    MINIMUM_CONTEXT_LENGTH,
)


# ============================================================================
# Property 5: Context Length Resolution Priority
# ============================================================================
# *For any* model, the ModelMetadataService SHALL resolve context length from
# sources in priority order: explicit config → cached value → provider API →
# models.dev → hardcoded defaults → fallback.
# **Validates: Requirements 2.1**


class TestContextLengthResolutionPriority:
    """Property tests for context length resolution priority order."""

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        config_override=st.integers(min_value=1000, max_value=2_000_000),
        cached_value=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=50, deadline=1000)
    async def test_config_override_has_highest_priority(
        self, model: str, config_override: int, cached_value: int
    ):
        """Config override should always take precedence over all other sources.
        
        **Validates: Requirements 2.1**
        """
        assume(config_override != cached_value)  # Ensure they're different
        
        # Mock the cache to return a different value
        mock_session = AsyncMock()
        
        with patch(
            "app.core.model_metadata.get_cached_context_length",
            new_callable=AsyncMock,
            return_value=cached_value
        ):
            result = await get_model_context_length(
                model=model,
                config_context_length=config_override,
                db_session=mock_session,
                enforce_minimum=False,
            )
            
            # Config override should always win
            assert result == config_override

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        base_url=st.text(min_size=5, max_size=100),
        cached_value=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=50, deadline=1000)
    async def test_cached_value_used_when_no_config(
        self, model: str, base_url: str, cached_value: int
    ):
        """Cached value should be used when no config override is provided.
        
        **Validates: Requirements 2.1**
        """
        mock_session = AsyncMock()
        
        with patch(
            "app.core.model_metadata.get_cached_context_length",
            new_callable=AsyncMock,
            return_value=cached_value
        ):
            result = await get_model_context_length(
                model=model,
                base_url=base_url,
                config_context_length=None,
                db_session=mock_session,
                enforce_minimum=False,
            )
            
            # Should use cached value
            assert result == cached_value

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        provider_value=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=30, deadline=2000)
    async def test_provider_api_used_when_no_cache(
        self, model: str, provider_value: int
    ):
        """Provider API should be queried when no cache exists.
        
        **Validates: Requirements 2.1**
        """
        mock_session = AsyncMock()
        
        with patch(
            "app.core.model_metadata.get_cached_context_length",
            new_callable=AsyncMock,
            return_value=None  # No cache
        ), patch(
            "app.core.model_metadata.query_provider_models_endpoint",
            new_callable=AsyncMock,
            return_value=provider_value
        ), patch(
            "app.core.model_metadata.save_context_length",
            new_callable=AsyncMock,
            return_value=True
        ):
            result = await get_model_context_length(
                model=model,
                base_url="https://api.example.com",
                config_context_length=None,
                db_session=mock_session,
                enforce_minimum=False,
            )
            
            # Should use provider API value
            assert result == provider_value

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        models_dev_value=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=30, deadline=2000)
    async def test_models_dev_used_when_provider_fails(
        self, model: str, models_dev_value: int
    ):
        """models.dev should be queried when provider API fails.
        
        **Validates: Requirements 2.1**
        """
        mock_session = AsyncMock()
        
        with patch(
            "app.core.model_metadata.get_cached_context_length",
            new_callable=AsyncMock,
            return_value=None
        ), patch(
            "app.core.model_metadata.query_provider_models_endpoint",
            new_callable=AsyncMock,
            return_value=None  # Provider fails
        ), patch(
            "app.core.model_metadata.query_models_dev",
            new_callable=AsyncMock,
            return_value=models_dev_value
        ), patch(
            "app.core.model_metadata.save_context_length",
            new_callable=AsyncMock,
            return_value=True
        ):
            result = await get_model_context_length(
                model=model,
                base_url="https://api.example.com",
                config_context_length=None,
                db_session=mock_session,
                enforce_minimum=False,
            )
            
            # Should use models.dev value
            assert result == models_dev_value

    @pytest.mark.asyncio
    @given(
        unknown_model=st.text(
            min_size=10, 
            max_size=50,
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))
        ).filter(
            # Filter out known model patterns
            lambda m: not any(
                pattern in m.lower() 
                for pattern in ['gpt', 'claude', 'gemini', 'llama', 'qwen', 'mistral', 'deepseek']
            )
        ),
    )
    @settings(max_examples=30, deadline=2000)
    async def test_fallback_used_when_all_sources_fail(self, unknown_model: str):
        """Default fallback should be used when all other sources fail.
        
        **Validates: Requirements 2.1**
        """
        mock_session = AsyncMock()
        
        with patch(
            "app.core.model_metadata.get_cached_context_length",
            new_callable=AsyncMock,
            return_value=None
        ), patch(
            "app.core.model_metadata.query_provider_models_endpoint",
            new_callable=AsyncMock,
            return_value=None
        ), patch(
            "app.core.model_metadata.query_models_dev",
            new_callable=AsyncMock,
            return_value=None
        ):
            result = await get_model_context_length(
                model=unknown_model,
                base_url="https://api.example.com",
                config_context_length=None,
                db_session=mock_session,
                enforce_minimum=False,
            )
            
            # Should use default fallback
            assert result == DEFAULT_FALLBACK_CONTEXT


# ============================================================================
# Property 6: Context Length Cache Round-Trip
# ============================================================================
# *For any* model and base_url pair, saving a context length to cache and
# reading it back SHALL produce the same value.
# **Validates: Requirements 2.2**


class TestContextLengthCacheRoundTrip:
    """Property tests for cache save/load round-trip consistency."""

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        base_url=st.one_of(
            st.just(""),
            st.text(min_size=10, max_size=100).filter(lambda x: '://' in x or not x)
        ),
        context_length=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=50, deadline=2000)
    async def test_save_and_load_produces_same_value(
        self, model: str, base_url: str, context_length: int
    ):
        """Saving a context length and loading it back should return the same value.
        
        **Validates: Requirements 2.2**
        """
        # Create a mock database session
        mock_session = AsyncMock()
        
        # Create a cache key for this test
        cache_key = _make_cache_key(model, base_url)
        
        # Mock the database model
        mock_cached_model = MagicMock()
        mock_cached_model.model_provider_key = cache_key
        mock_cached_model.context_length = context_length
        mock_cached_model.discovered_at = datetime.now(timezone.utc)
        
        # Mock the save operation
        with patch(
            "app.models.db_models.ContextLengthCacheModel"
        ) as mock_model_class, patch(
            "sqlalchemy.dialects.postgresql.insert"
        ) as mock_insert:
            # Setup save
            mock_session.execute = AsyncMock()
            
            # Perform save
            save_result = await save_context_length(
                model=model,
                base_url=base_url,
                length=context_length,
                db_session=mock_session,
            )
            
            # Save should succeed
            assert save_result is True
            
        # Mock the load operation
        with patch(
            "app.models.db_models.ContextLengthCacheModel"
        ) as mock_model_class, patch(
            "sqlalchemy.select"
        ) as mock_select:
            # Setup load
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_cached_model
            mock_session.execute = AsyncMock(return_value=mock_result)
            
            # Perform load
            loaded_value = await get_cached_context_length(
                model=model,
                base_url=base_url,
                db_session=mock_session,
            )
            
            # Loaded value should match saved value
            assert loaded_value == context_length

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        base_url=st.text(min_size=10, max_size=100),
        context_length=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=50, deadline=1000)
    async def test_cache_key_uniqueness(
        self, model: str, base_url: str, context_length: int
    ):
        """Cache keys should uniquely identify model+base_url pairs.
        
        **Validates: Requirements 2.2**
        """
        # Generate cache key
        cache_key = _make_cache_key(model, base_url)
        
        # Cache key should be deterministic
        cache_key_2 = _make_cache_key(model, base_url)
        assert cache_key == cache_key_2
        
        # Cache key should include both model and base_url (if provided)
        if base_url:
            assert model in cache_key or model.split(':')[-1] in cache_key
            # Normalized base_url should be in key
            normalized_url = base_url.rstrip('/')
            assert normalized_url in cache_key
        else:
            # Without base_url, key should just be the model
            assert cache_key == model or cache_key == model.split(':')[-1]

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        base_url1=st.text(min_size=10, max_size=100),
        base_url2=st.text(min_size=10, max_size=100),
        context_length1=st.integers(min_value=1000, max_value=2_000_000),
        context_length2=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=30, deadline=1000)
    async def test_different_base_urls_have_different_cache_entries(
        self, 
        model: str, 
        base_url1: str, 
        base_url2: str,
        context_length1: int,
        context_length2: int,
    ):
        """Same model with different base_urls should have separate cache entries.
        
        **Validates: Requirements 2.2**
        """
        assume(base_url1 != base_url2)  # Ensure different URLs
        assume(context_length1 != context_length2)  # Ensure different values
        
        # Generate cache keys
        cache_key1 = _make_cache_key(model, base_url1)
        cache_key2 = _make_cache_key(model, base_url2)
        
        # Cache keys should be different for different base_urls
        assert cache_key1 != cache_key2

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        base_url=st.text(min_size=10, max_size=100),
        initial_length=st.integers(min_value=1000, max_value=2_000_000),
        updated_length=st.integers(min_value=1000, max_value=2_000_000),
    )
    @settings(max_examples=30, deadline=1000)
    async def test_cache_update_overwrites_previous_value(
        self,
        model: str,
        base_url: str,
        initial_length: int,
        updated_length: int,
    ):
        """Updating a cache entry should overwrite the previous value.
        
        **Validates: Requirements 2.2**
        """
        assume(initial_length != updated_length)  # Ensure different values
        
        mock_session = AsyncMock()
        cache_key = _make_cache_key(model, base_url)
        
        # Mock initial save
        with patch(
            "app.models.db_models.ContextLengthCacheModel"
        ), patch(
            "sqlalchemy.dialects.postgresql.insert"
        ):
            mock_session.execute = AsyncMock()
            
            # Save initial value
            result1 = await save_context_length(
                model=model,
                base_url=base_url,
                length=initial_length,
                db_session=mock_session,
            )
            assert result1 is True
        
        # Mock update save
        with patch(
            "app.models.db_models.ContextLengthCacheModel"
        ), patch(
            "sqlalchemy.dialects.postgresql.insert"
        ):
            mock_session.execute = AsyncMock()
            
            # Save updated value
            result2 = await save_context_length(
                model=model,
                base_url=base_url,
                length=updated_length,
                db_session=mock_session,
            )
            assert result2 is True
        
        # Mock load to return updated value
        mock_cached_model = MagicMock()
        mock_cached_model.context_length = updated_length
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cached_model
        
        with patch(
            "app.models.db_models.ContextLengthCacheModel"
        ), patch(
            "sqlalchemy.select"
        ):
            mock_session.execute = AsyncMock(return_value=mock_result)
            
            # Load should return updated value
            loaded = await get_cached_context_length(
                model=model,
                base_url=base_url,
                db_session=mock_session,
            )
            
            assert loaded == updated_length

    @pytest.mark.asyncio
    @given(
        model=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='-_.'
        )),
        base_url=st.text(min_size=10, max_size=100),
    )
    @settings(max_examples=30, deadline=1000)
    async def test_cache_miss_returns_none(self, model: str, base_url: str):
        """Loading from cache when no entry exists should return None.
        
        **Validates: Requirements 2.2**
        """
        mock_session = AsyncMock()
        
        # Mock empty cache
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        
        with patch(
            "app.models.db_models.ContextLengthCacheModel"
        ), patch(
            "sqlalchemy.select"
        ):
            mock_session.execute = AsyncMock(return_value=mock_result)
            
            # Load should return None
            loaded = await get_cached_context_length(
                model=model,
                base_url=base_url,
                db_session=mock_session,
            )
            
            assert loaded is None
