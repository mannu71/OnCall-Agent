"""Property-based tests for ContextCompressor.

Tests universal correctness properties for context compression using
hypothesis for property-based testing.

**Validates: Requirements 1.1, 1.2, 1.3, 1.5**
"""

import pytest
from hypothesis import given, strategies as st, assume, settings, HealthCheck
from typing import Any, Dict, List

from app.core.context_compression import (
    ContextCompressor,
    MIN_TAIL_MESSAGES,
)
from app.core.model_metadata import estimate_messages_tokens_rough


# ============================================================================
# Hypothesis Strategies for Message Generation
# ============================================================================

def message_role_strategy():
    """Strategy for generating message roles."""
    return st.sampled_from(["system", "user", "assistant", "tool"])


def message_content_strategy():
    """Strategy for generating message content with varying sizes."""
    return st.one_of(
        st.text(min_size=10, max_size=100),  # Short messages
        st.text(min_size=100, max_size=500),  # Medium messages
        st.text(min_size=500, max_size=2000),  # Long messages
    )


def simple_message_strategy():
    """Strategy for generating simple messages (system, user, assistant)."""
    return st.builds(
        dict,
        role=st.sampled_from(["system", "user", "assistant"]),
        content=message_content_strategy(),
    )


def tool_call_strategy():
    """Strategy for generating tool calls."""
    return st.builds(
        dict,
        id=st.text(min_size=5, max_size=20, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'
        )),
        function=st.builds(
            dict,
            name=st.text(min_size=3, max_size=20, alphabet=st.characters(
                whitelist_categories=('Lu', 'Ll'), whitelist_characters='_'
            )),
            arguments=st.just("{}"),
        ),
    )


def tool_call_message_strategy():
    """Strategy for generating assistant messages with tool calls."""
    return st.builds(
        dict,
        role=st.just("assistant"),
        content=st.none(),
        tool_calls=st.lists(tool_call_strategy(), min_size=1, max_size=3),
    )


def tool_result_strategy(tool_call_id: str):
    """Strategy for generating tool result messages for a specific tool call."""
    return st.builds(
        dict,
        role=st.just("tool"),
        tool_call_id=st.just(tool_call_id),
        content=message_content_strategy(),
    )


def message_list_strategy(min_size: int = 5, max_size: int = 50):
    """Strategy for generating lists of messages."""
    return st.lists(
        simple_message_strategy(),
        min_size=min_size,
        max_size=max_size,
    )


def large_message_list_strategy():
    """Strategy for generating large message lists that exceed compression threshold."""
    # Generate enough messages to exceed typical thresholds
    return st.lists(
        st.builds(
            dict,
            role=st.sampled_from(["user", "assistant"]),
            content=st.text(min_size=500, max_size=2000),  # Large messages
        ),
        min_size=100,
        max_size=500,
    )


# ============================================================================
# Property 1: Compression Threshold Trigger
# ============================================================================
# *For any* conversation with token count exceeding the configured threshold
# percentage, the ContextCompressor SHALL initiate compression.
# **Validates: Requirements 1.1**


class TestCompressionThresholdTrigger:
    """Property tests for compression threshold trigger behavior."""

    @given(
        threshold_percent=st.floats(min_value=0.1, max_value=0.9),
        token_multiplier=st.floats(min_value=1.1, max_value=3.0),
    )
    @settings(max_examples=50, deadline=2000)
    def test_compression_triggers_above_threshold(
        self, threshold_percent: float, token_multiplier: float
    ):
        """Compression should trigger when tokens exceed threshold.
        
        **Validates: Requirements 1.1**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=threshold_percent,
        )
        
        # Calculate token count above threshold
        tokens_above = int(compressor.threshold_tokens * token_multiplier)
        
        # Should trigger compression
        assert compressor.should_compress(tokens_above) is True

    @given(
        threshold_percent=st.floats(min_value=0.1, max_value=0.9),
        token_fraction=st.floats(min_value=0.1, max_value=0.99),
    )
    @settings(max_examples=50, deadline=2000)
    def test_compression_does_not_trigger_below_threshold(
        self, threshold_percent: float, token_fraction: float
    ):
        """Compression should not trigger when tokens are below threshold.
        
        **Validates: Requirements 1.1**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=threshold_percent,
        )
        
        # Calculate token count below threshold
        tokens_below = int(compressor.threshold_tokens * token_fraction)
        
        # Should not trigger compression
        assert compressor.should_compress(tokens_below) is False

    @given(
        threshold_percent=st.floats(min_value=0.1, max_value=0.9),
    )
    @settings(max_examples=30, deadline=2000)
    def test_compression_does_not_trigger_at_exact_threshold(
        self, threshold_percent: float
    ):
        """Compression should not trigger at exactly the threshold.
        
        **Validates: Requirements 1.1**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=threshold_percent,
        )
        
        # At exact threshold
        tokens_at_threshold = compressor.threshold_tokens
        
        # Should not trigger (must be greater than, not equal to)
        assert compressor.should_compress(tokens_at_threshold) is False


# ============================================================================
# Property 2: Head Message Protection
# ============================================================================
# *For any* compression operation, the first N messages (system prompt +
# initial exchange) SHALL be preserved unchanged.
# **Validates: Requirements 1.2**


class TestHeadMessageProtection:
    """Property tests for head message protection."""

    @given(
        protect_first_n=st.integers(min_value=1, max_value=10),
        total_messages=st.integers(min_value=5, max_value=100),
    )
    @settings(max_examples=50, deadline=3000)
    def test_first_n_messages_preserved_unchanged(
        self, protect_first_n: int, total_messages: int
    ):
        """First N messages should be preserved unchanged after compression.
        
        **Validates: Requirements 1.2**
        """
        assume(total_messages > protect_first_n)
        
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=protect_first_n,
            threshold_percent=0.10,  # Low threshold to trigger compression
        )
        
        # Generate messages with large content to trigger compression
        messages = []
        for i in range(total_messages):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} " * 200,  # Large content
            })
        
        compressed = compressor.compress(messages)
        
        # First N messages should be unchanged
        for i in range(min(protect_first_n, len(compressed))):
            assert compressed[i] == messages[i], f"Message {i} was modified"

    @given(
        protect_first_n=st.integers(min_value=1, max_value=10),
        message_count=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=30, deadline=2000)
    def test_all_messages_protected_when_fewer_than_n(
        self, protect_first_n: int, message_count: int
    ):
        """When total messages < N, all should be protected.
        
        **Validates: Requirements 1.2**
        """
        assume(message_count < protect_first_n)
        
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=protect_first_n,
            threshold_percent=0.10,
        )
        
        messages = []
        for i in range(message_count):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i}",
            })
        
        compressed = compressor.compress(messages)
        
        # All messages should be present (no compression possible)
        assert len(compressed) == len(messages)
        assert compressed == messages

    @given(
        protect_first_n=st.integers(min_value=2, max_value=5),
    )
    @settings(
        max_examples=10,
        deadline=3000,
        suppress_health_check=[HealthCheck.data_too_large, HealthCheck.too_slow]
    )
    def test_head_protection_independent_of_content_size(
        self, protect_first_n: int
    ):
        """Head protection should work regardless of message content size.
        
        **Validates: Requirements 1.2**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=protect_first_n,
            threshold_percent=0.10,
        )
        
        # Create messages with varying sizes
        messages = []
        for i in range(30):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} " * (50 * (i + 1)),  # Increasing size
            })
        
        compressed = compressor.compress(messages)
        
        # First N messages should be unchanged
        for i in range(min(protect_first_n, len(compressed))):
            assert compressed[i] == messages[i]


# ============================================================================
# Property 3: Token-Budget Tail Protection
# ============================================================================
# *For any* compression operation, the most recent messages SHALL be
# protected based on token budget, not fixed message count.
# **Validates: Requirements 1.3**


class TestTokenBudgetTailProtection:
    """Property tests for token-budget based tail protection."""

    @given(
        tail_budget_percent=st.floats(min_value=0.15, max_value=0.40),
        message_count=st.integers(min_value=15, max_value=40),
    )
    @settings(max_examples=20, deadline=3000)
    def test_tail_protected_by_token_budget_not_count(
        self, tail_budget_percent: float, message_count: int
    ):
        """Tail protection should be based on token budget, not message count.
        
        **Validates: Requirements 1.3**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=2,
            threshold_percent=0.10,
            tail_budget_percent=tail_budget_percent,
        )
        
        # Create messages with varying sizes
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Start"},
        ]
        
        # Add middle messages with large content
        for i in range(message_count - 4):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Middle message {i} " * 200,
            })
        
        # Add recent messages with unique markers (should be in tail)
        messages.append({"role": "user", "content": "UNIQUE_RECENT_QUESTION_MARKER"})
        messages.append({"role": "assistant", "content": "UNIQUE_RECENT_ANSWER_MARKER"})
        
        compressed = compressor.compress(messages)
        
        # The key property: recent messages should be preserved
        # (This validates token-budget protection works)
        all_content = " ".join(m.get("content", "") or "" for m in compressed)
        
        # Recent messages should be in the result
        assert "UNIQUE_RECENT_QUESTION_MARKER" in all_content, \
            "Recent user message should be preserved in tail"
        assert "UNIQUE_RECENT_ANSWER_MARKER" in all_content, \
            "Recent assistant message should be preserved in tail"

    @given(
        message_count=st.integers(min_value=10, max_value=50),
    )
    @settings(max_examples=30, deadline=3000)
    def test_minimum_tail_messages_preserved(self, message_count: int):
        """At least MIN_TAIL_MESSAGES should be preserved in tail.
        
        **Validates: Requirements 1.3**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
            tail_budget_percent=0.01,  # Very small budget
        )
        
        messages = [{"role": "system", "content": "System"}]
        
        # Add many messages to trigger compression
        for i in range(message_count):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} " * 100,
            })
        
        compressed = compressor.compress(messages)
        
        # Should have at least head + MIN_TAIL_MESSAGES
        # (may have summary message in between)
        assert len(compressed) >= 1 + MIN_TAIL_MESSAGES

    @given(
        tail_budget_percent=st.floats(min_value=0.10, max_value=0.40),
    )
    @settings(max_examples=30, deadline=3000)
    def test_tail_budget_scales_with_context_length(
        self, tail_budget_percent: float
    ):
        """Tail token budget should scale with context length.
        
        **Validates: Requirements 1.3**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            tail_budget_percent=tail_budget_percent,
        )
        
        # Tail budget should be a fraction of context length
        expected_budget = int(compressor.context_length * tail_budget_percent)
        
        # Allow for minimum budget override
        expected_budget = max(expected_budget, 8_000)
        
        assert compressor.tail_token_budget == expected_budget


# ============================================================================
# Property 4: Tool-Call/Result Pair Integrity
# ============================================================================
# *For any* compression operation, all tool-call/result pairs SHALL remain
# matched—every tool_call_id in assistant messages SHALL have a corresponding
# tool result, and every tool result SHALL reference an existing tool_call_id.
# **Validates: Requirements 1.5**


class TestToolCallResultPairIntegrity:
    """Property tests for tool-call/result pair integrity."""

    @given(
        num_tool_pairs=st.integers(min_value=1, max_value=10),
        middle_message_count=st.integers(min_value=10, max_value=50),
    )
    @settings(max_examples=30, deadline=3000)
    def test_tool_call_and_result_stay_together(
        self, num_tool_pairs: int, middle_message_count: int
    ):
        """Tool call and result pairs should stay together after compression.
        
        **Validates: Requirements 1.5**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
        )
        
        messages = [{"role": "system", "content": "System"}]
        
        # Add middle messages
        for i in range(middle_message_count):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} " * 100,
            })
        
        # Add tool call/result pairs
        tool_call_ids = []
        for i in range(num_tool_pairs):
            tool_call_id = f"call_{i}"
            tool_call_ids.append(tool_call_id)
            
            # Add tool call
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call_id,
                    "function": {"name": f"tool_{i}", "arguments": "{}"},
                }],
            })
            
            # Add tool result
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": f"Result for tool {i}",
            })
        
        compressed = compressor.compress(messages)
        
        # Extract all tool call IDs from compressed messages
        compressed_tool_call_ids = set()
        for msg in compressed:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    compressed_tool_call_ids.add(tc.get("id"))
        
        # Extract all tool result IDs from compressed messages
        compressed_tool_result_ids = set()
        for msg in compressed:
            if msg.get("role") == "tool":
                compressed_tool_result_ids.add(msg.get("tool_call_id"))
        
        # Every tool result should have a corresponding tool call
        for result_id in compressed_tool_result_ids:
            assert result_id in compressed_tool_call_ids, \
                f"Tool result {result_id} has no corresponding tool call"
        
        # Every tool call should have a corresponding result
        for call_id in compressed_tool_call_ids:
            assert call_id in compressed_tool_result_ids, \
                f"Tool call {call_id} has no corresponding result"

    @given(
        tool_call_id=st.text(min_size=5, max_size=20, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'
        )),
        messages_between=st.integers(min_value=5, max_value=30),
    )
    @settings(max_examples=30, deadline=3000)
    def test_tool_pair_integrity_with_messages_between(
        self, tool_call_id: str, messages_between: int
    ):
        """Tool pairs should stay matched even with messages between them.
        
        **Validates: Requirements 1.5**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
        )
        
        messages = [{"role": "system", "content": "System"}]
        
        # Add initial messages
        for i in range(10):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Initial {i} " * 100,
            })
        
        # Add tool call
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": tool_call_id,
                "function": {"name": "test_tool", "arguments": "{}"},
            }],
        })
        
        # Add messages between tool call and result
        for i in range(messages_between):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Between {i} " * 100,
            })
        
        # Add tool result (should pull tool call to tail)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "Tool result",
        })
        
        compressed = compressor.compress(messages)
        
        # Find tool result
        tool_result_found = False
        tool_result_idx = -1
        for i, msg in enumerate(compressed):
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
                tool_result_found = True
                tool_result_idx = i
                break
        
        assert tool_result_found, "Tool result should be preserved"
        
        # Find corresponding tool call
        tool_call_found = False
        tool_call_idx = -1
        for i, msg in enumerate(compressed):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    if tc.get("id") == tool_call_id:
                        tool_call_found = True
                        tool_call_idx = i
                        break
        
        assert tool_call_found, "Tool call should be preserved with its result"
        
        # Tool call should be before or at the tool result
        assert tool_call_idx <= tool_result_idx, \
            "Tool call should come before its result"

    @given(
        num_pairs=st.integers(min_value=2, max_value=8),
    )
    @settings(max_examples=20, deadline=3000)
    def test_multiple_tool_pairs_all_preserved(self, num_pairs: int):
        """All tool call/result pairs should be preserved after compression.
        
        **Validates: Requirements 1.5**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
        )
        
        messages = [{"role": "system", "content": "System"}]
        
        # Add many messages to trigger compression
        for i in range(50):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} " * 100,
            })
        
        # Add multiple tool pairs
        tool_ids = [f"call_{i}" for i in range(num_pairs)]
        for tid in tool_ids:
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tid,
                    "function": {"name": f"tool_{tid}", "arguments": "{}"},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tid,
                "content": f"Result for {tid}",
            })
        
        compressed = compressor.compress(messages)
        
        # All tool results should be present
        for tid in tool_ids:
            result_found = any(
                m.get("role") == "tool" and m.get("tool_call_id") == tid
                for m in compressed
            )
            assert result_found, f"Tool result {tid} should be preserved"
            
            # Corresponding tool call should also be present
            call_found = False
            for m in compressed:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m.get("tool_calls", []):
                        if tc.get("id") == tid:
                            call_found = True
                            break
            assert call_found, f"Tool call {tid} should be preserved"

    @given(
        tool_call_id=st.text(min_size=5, max_size=20, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'
        )),
    )
    @settings(max_examples=30, deadline=3000)
    def test_tool_pair_ordering_preserved(self, tool_call_id: str):
        """Tool call should always come before its result in compressed output.
        
        **Validates: Requirements 1.5**
        """
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
        )
        
        messages = [{"role": "system", "content": "System"}]
        
        # Add messages
        for i in range(30):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message {i} " * 100,
            })
        
        # Add tool call
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": tool_call_id,
                "function": {"name": "test_tool", "arguments": "{}"},
            }],
        })
        
        # Add more messages
        for i in range(10):
            messages.append({
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"After {i} " * 100,
            })
        
        # Add tool result
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "Tool result",
        })
        
        compressed = compressor.compress(messages)
        
        # Find indices
        call_idx = -1
        result_idx = -1
        
        for i, msg in enumerate(compressed):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    if tc.get("id") == tool_call_id:
                        call_idx = i
            
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
                result_idx = i
        
        # Both should be found
        assert call_idx >= 0, "Tool call should be in compressed output"
        assert result_idx >= 0, "Tool result should be in compressed output"
        
        # Call should come before result
        assert call_idx < result_idx, \
            f"Tool call at {call_idx} should come before result at {result_idx}"
