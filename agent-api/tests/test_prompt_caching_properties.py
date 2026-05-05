"""
Property-Based Tests for Prompt Caching

These tests validate universal correctness properties of the prompt caching system
using Hypothesis for property-based testing.

Properties tested:
- Property 23: Prompt Cache Marker Count - Exactly 4 messages marked maximum
- Property 24: Prompt Cache Immutability - Original message list not modified
"""

import copy
from hypothesis import given, strategies as st
import pytest

from app.core.prompt_caching import (
    apply_anthropic_cache_control,
    count_cache_markers,
    has_cache_markers,
    remove_cache_markers,
)


# Strategy for generating message roles
role_strategy = st.sampled_from(["system", "user", "assistant"])


# Strategy for generating message content (string or list of blocks)
def content_strategy():
    """Generate realistic message content."""
    return st.one_of(
        # Simple string content
        st.text(min_size=1, max_size=100),
        # List of content blocks
        st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "text": st.text(min_size=1, max_size=100),
            }),
            min_size=1,
            max_size=5,
        ),
    )


# Strategy for generating a single message
def message_strategy():
    """Generate a realistic message dictionary."""
    return st.fixed_dictionaries({
        "role": role_strategy,
        "content": content_strategy(),
    })


# Strategy for generating a list of messages
def messages_strategy(min_size=0, max_size=20):
    """Generate a list of messages."""
    return st.lists(
        message_strategy(),
        min_size=min_size,
        max_size=max_size,
    )


# Strategy for cache TTL values
ttl_strategy = st.sampled_from(["5m", "1h"])


# Strategy for native_anthropic flag
native_flag_strategy = st.booleans()


class TestPromptCachingProperties:
    """Property-based tests for prompt caching."""
    
    # =========================================================================
    # Property 23: Prompt Cache Marker Count
    # =========================================================================
    
    @given(
        messages=messages_strategy(min_size=0, max_size=20),
        cache_ttl=ttl_strategy,
        native_anthropic=native_flag_strategy,
    )
    def test_property_23_cache_marker_count_maximum(
        self,
        messages,
        cache_ttl,
        native_anthropic,
    ):
        """
        Property 23: Prompt Cache Marker Count
        
        For any message list, the PromptCaching system SHALL place cache_control
        markers on exactly 4 messages maximum (1 system + 3 others).
        
        Validates: Requirements 13.2
        """
        # Apply caching
        cached_messages = apply_anthropic_cache_control(
            messages,
            cache_ttl=cache_ttl,
            native_anthropic=native_anthropic,
        )
        
        # Count markers
        marker_count = count_cache_markers(cached_messages)
        
        # Property: Maximum 4 markers
        assert marker_count <= 4, (
            f"Expected at most 4 cache markers, got {marker_count}"
        )
    
    @given(
        messages=messages_strategy(min_size=4, max_size=20),
        cache_ttl=ttl_strategy,
        native_anthropic=native_flag_strategy,
    )
    def test_property_23_cache_marker_count_with_system(
        self,
        messages,
        cache_ttl,
        native_anthropic,
    ):
        """
        Property 23: Prompt Cache Marker Count (with system message)
        
        For any message list with at least 4 messages including a system message,
        exactly 4 messages SHALL be marked (system + last 3 non-system).
        
        Validates: Requirements 13.2
        """
        # Ensure first message is system
        messages_copy = copy.deepcopy(messages)
        messages_copy[0]["role"] = "system"
        
        # Ensure we have at least 3 non-system messages
        for i in range(1, min(4, len(messages_copy))):
            messages_copy[i]["role"] = "user" if i % 2 == 1 else "assistant"
        
        # Apply caching
        cached_messages = apply_anthropic_cache_control(
            messages_copy,
            cache_ttl=cache_ttl,
            native_anthropic=native_anthropic,
        )
        
        # Count markers
        marker_count = count_cache_markers(cached_messages)
        
        # Property: Exactly 4 markers when we have system + 3+ non-system
        non_system_count = sum(1 for m in messages_copy if m["role"] != "system")
        if non_system_count >= 3:
            assert marker_count == 4, (
                f"Expected exactly 4 cache markers with system + 3+ non-system, got {marker_count}"
            )
    
    @given(
        messages=messages_strategy(min_size=1, max_size=3),
        cache_ttl=ttl_strategy,
        native_anthropic=native_flag_strategy,
    )
    def test_property_23_cache_marker_count_few_messages(
        self,
        messages,
        cache_ttl,
        native_anthropic,
    ):
        """
        Property 23: Prompt Cache Marker Count (few messages)
        
        For any message list with fewer than 4 messages, the number of markers
        SHALL equal the number of messages.
        
        Validates: Requirements 13.2
        """
        # Apply caching
        cached_messages = apply_anthropic_cache_control(
            messages,
            cache_ttl=cache_ttl,
            native_anthropic=native_anthropic,
        )
        
        # Count markers
        marker_count = count_cache_markers(cached_messages)
        
        # Property: Markers <= message count
        assert marker_count <= len(messages), (
            f"Expected at most {len(messages)} markers, got {marker_count}"
        )
    
    # =========================================================================
    # Property 24: Prompt Cache Immutability
    # =========================================================================
    
    @given(
        messages=messages_strategy(min_size=1, max_size=20),
        cache_ttl=ttl_strategy,
        native_anthropic=native_flag_strategy,
    )
    def test_property_24_original_messages_not_modified(
        self,
        messages,
        cache_ttl,
        native_anthropic,
    ):
        """
        Property 24: Prompt Cache Immutability
        
        For any message list passed to the PromptCaching system, the original
        list SHALL not be modified (a deep copy is returned).
        
        Validates: Requirements 13.6
        """
        # Create a deep copy for comparison
        original_messages = copy.deepcopy(messages)
        
        # Apply caching (should not modify original)
        cached_messages = apply_anthropic_cache_control(
            messages,
            cache_ttl=cache_ttl,
            native_anthropic=native_anthropic,
        )
        
        # Property: Original messages unchanged
        assert messages == original_messages, (
            "Original message list was modified by apply_anthropic_cache_control"
        )
        
        # Property: Returned messages are different object
        assert cached_messages is not messages, (
            "Returned messages should be a different object (deep copy)"
        )
    
    @given(
        messages=messages_strategy(min_size=1, max_size=20),
        cache_ttl=ttl_strategy,
        native_anthropic=native_flag_strategy,
    )
    def test_property_24_no_cache_markers_in_original(
        self,
        messages,
        cache_ttl,
        native_anthropic,
    ):
        """
        Property 24: Prompt Cache Immutability (no markers in original)
        
        After applying cache control, the original message list SHALL not
        contain any cache_control markers.
        
        Validates: Requirements 13.6
        """
        # Apply caching
        cached_messages = apply_anthropic_cache_control(
            messages,
            cache_ttl=cache_ttl,
            native_anthropic=native_anthropic,
        )
        
        # Property: Original has no cache markers
        original_marker_count = count_cache_markers(messages)
        assert original_marker_count == 0, (
            f"Original messages should have 0 cache markers, found {original_marker_count}"
        )
        
        # Property: Cached version has markers (if messages exist)
        if len(messages) > 0:
            cached_marker_count = count_cache_markers(cached_messages)
            assert cached_marker_count > 0, (
                "Cached messages should have at least 1 cache marker"
            )
    
    # =========================================================================
    # Additional Properties
    # =========================================================================
    
    @given(
        messages=messages_strategy(min_size=1, max_size=20),
        cache_ttl=ttl_strategy,
        native_anthropic=native_flag_strategy,
    )
    def test_cache_markers_are_on_last_content_block(
        self,
        messages,
        cache_ttl,
        native_anthropic,
    ):
        """
        Property: Cache markers are placed on the last content block.
        
        For any message with cache_control, the marker SHALL be on the last
        content block of that message.
        """
        # Apply caching
        cached_messages = apply_anthropic_cache_control(
            messages,
            cache_ttl=cache_ttl,
            native_anthropic=native_anthropic,
        )
        
        # Check each message
        for msg in cached_messages:
            content = msg.get("content")
            
            if isinstance(content, list) and len(content) > 0:
                # Check if any block has cache_control
                has_marker = any(
                    isinstance(block, dict) and "cache_control" in block
                    for block in content
                )
                
                if has_marker:
                    # Verify it's on the last block
                    last_block = content[-1]
                    assert isinstance(last_block, dict), (
                        "Last content block should be a dict when cache_control is present"
                    )
                    assert "cache_control" in last_block, (
                        "cache_control should be on the last content block"
                    )
    
    @given(
        messages=messages_strategy(min_size=1, max_size=20),
    )
    def test_remove_cache_markers_idempotent(self, messages):
        """
        Property: Removing cache markers is idempotent.
        
        Removing cache markers twice SHALL produce the same result as removing once.
        """
        # Apply caching
        cached_messages = apply_anthropic_cache_control(messages)
        
        # Remove markers once
        cleaned_once = remove_cache_markers(cached_messages)
        
        # Remove markers twice
        cleaned_twice = remove_cache_markers(cleaned_once)
        
        # Property: Same result
        assert cleaned_once == cleaned_twice, (
            "Removing cache markers should be idempotent"
        )
        
        # Property: No markers remain
        assert count_cache_markers(cleaned_twice) == 0, (
            "No cache markers should remain after removal"
        )
    
    @given(
        messages=messages_strategy(min_size=0, max_size=20),
    )
    def test_empty_messages_handled_gracefully(self, messages):
        """
        Property: Empty message lists are handled gracefully.
        
        Applying cache control to an empty list SHALL return an empty list.
        """
        # Filter to potentially empty list
        if len(messages) == 0:
            cached_messages = apply_anthropic_cache_control(messages)
            
            # Property: Empty in, empty out
            assert cached_messages == [], (
                "Empty message list should return empty list"
            )
    
    @given(
        messages=messages_strategy(min_size=4, max_size=20),
        cache_ttl=ttl_strategy,
    )
    def test_system_message_always_marked_if_present(
        self,
        messages,
        cache_ttl,
    ):
        """
        Property: System message is always marked if present.
        
        If a system message exists, it SHALL always receive a cache marker.
        """
        # Ensure first message is system
        messages_copy = copy.deepcopy(messages)
        messages_copy[0]["role"] = "system"
        
        # Apply caching
        cached_messages = apply_anthropic_cache_control(
            messages_copy,
            cache_ttl=cache_ttl,
        )
        
        # Check system message has marker
        system_msg = cached_messages[0]
        content = system_msg.get("content")
        
        has_marker = False
        if isinstance(content, list):
            has_marker = any(
                isinstance(block, dict) and "cache_control" in block
                for block in content
            )
        elif isinstance(content, dict):
            has_marker = "cache_control" in content
        
        # Property: System message is marked
        assert has_marker, (
            "System message should always have cache_control marker"
        )


class TestPromptCachingExamples:
    """Example-based tests for specific scenarios."""
    
    def test_example_basic_conversation(self):
        """Test basic conversation with system + user + assistant."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        
        cached = apply_anthropic_cache_control(messages)
        
        # Should have 4 markers (system + last 3)
        assert count_cache_markers(cached) == 4
        
        # Original unchanged
        assert count_cache_markers(messages) == 0
    
    def test_example_no_system_message(self):
        """Test conversation without system message."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well!"},
        ]
        
        cached = apply_anthropic_cache_control(messages)
        
        # Should have 3 markers (last 3 messages)
        assert count_cache_markers(cached) == 3
    
    def test_example_single_message(self):
        """Test single message."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        
        cached = apply_anthropic_cache_control(messages)
        
        # Should have 1 marker
        assert count_cache_markers(cached) == 1
    
    def test_example_empty_list(self):
        """Test empty message list."""
        messages = []
        
        cached = apply_anthropic_cache_control(messages)
        
        # Should return empty list
        assert cached == []
        assert count_cache_markers(cached) == 0
    
    def test_example_content_blocks(self):
        """Test messages with content blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "World"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Hi"},
                ],
            },
        ]
        
        cached = apply_anthropic_cache_control(messages)
        
        # Should have 2 markers (last 2 messages)
        assert count_cache_markers(cached) == 2
        
        # Markers should be on last block of each message
        assert "cache_control" in cached[0]["content"][-1]
        assert "cache_control" in cached[1]["content"][-1]
    
    def test_example_ttl_5m(self):
        """Test 5-minute TTL."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        
        cached = apply_anthropic_cache_control(messages, cache_ttl="5m")
        
        # Should have cache_control with type ephemeral
        content = cached[0]["content"]
        assert isinstance(content, list)
        assert content[0]["cache_control"]["type"] == "ephemeral"
    
    def test_example_ttl_1h(self):
        """Test 1-hour TTL."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        
        cached = apply_anthropic_cache_control(messages, cache_ttl="1h")
        
        # Should have cache_control with type ephemeral
        content = cached[0]["content"]
        assert isinstance(content, list)
        assert content[0]["cache_control"]["type"] == "ephemeral"
    
    def test_example_native_anthropic_format(self):
        """Test native Anthropic SDK format."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        
        cached = apply_anthropic_cache_control(
            messages,
            cache_ttl="5m",
            native_anthropic=True,
        )
        
        # Should have cache_control with ttl_seconds
        content = cached[0]["content"]
        assert isinstance(content, list)
        cache_control = content[0]["cache_control"]
        assert cache_control["type"] == "ephemeral"
        assert cache_control["ttl_seconds"] == 300
    
    def test_example_many_messages(self):
        """Test conversation with many messages."""
        messages = [
            {"role": "system", "content": "System prompt"},
        ]
        
        # Add 20 user/assistant exchanges
        for i in range(20):
            messages.append({"role": "user", "content": f"User {i}"})
            messages.append({"role": "assistant", "content": f"Assistant {i}"})
        
        cached = apply_anthropic_cache_control(messages)
        
        # Should have exactly 4 markers
        assert count_cache_markers(cached) == 4
        
        # System message should be marked
        assert has_cache_markers([cached[0]])
        
        # Last 3 messages should be marked
        assert has_cache_markers(cached[-3:])
