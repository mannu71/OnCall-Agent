"""Unit tests for ContextCompressor.

Tests for:
- Compression threshold trigger (Property 1)
- Head message protection (Property 2)
- Token-budget tail protection (Property 3)
- Tool-call/result pair integrity (Property 4)
"""

import pytest
from typing import Any, Dict, List
from hypothesis import given, strategies as st, assume, settings
from hypothesis import Phase

from app.core.context_compression import (
    ContextCompressor,
    CompressionResult,
    DEFAULT_TAIL_BUDGET_PERCENT,
    MIN_TAIL_MESSAGES,
)
from app.core.model_metadata import estimate_messages_tokens_rough


def create_message(role: str, content: str, **kwargs) -> Dict[str, Any]:
    """Helper to create a message dict."""
    msg = {"role": role, "content": content}
    msg.update(kwargs)
    return msg


def create_tool_call_message(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Helper to create an assistant message with tool calls."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": tool_calls,
    }


def create_tool_result(tool_call_id: str, content: str) -> Dict[str, Any]:
    """Helper to create a tool result message."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


class TestContextCompressorInit:
    """Tests for ContextCompressor initialization."""
    
    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        compressor = ContextCompressor(model="gpt-4o")
        
        assert compressor.model == "gpt-4o"
        assert compressor.threshold_percent == 0.50
        assert compressor.protect_first_n == 3
        assert compressor.context_length > 0
        assert compressor.threshold_tokens > 0
        assert compressor.tail_token_budget > 0
    
    def test_init_with_custom_params(self):
        """Test initialization with custom parameters."""
        compressor = ContextCompressor(
            model="claude-3-opus",
            threshold_percent=0.75,
            protect_first_n=5,
            tail_budget_percent=0.30,
        )
        
        assert compressor.threshold_percent == 0.75
        assert compressor.protect_first_n == 5
        assert compressor.tail_token_budget > 0


class TestShouldCompress:
    """Tests for should_compress threshold check.
    
    Validated by: Property 1 - Compression Threshold Trigger
    """
    
    def test_below_threshold_no_compress(self):
        """Messages below threshold should not trigger compression."""
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=0.50,
        )
        
        # Below threshold
        assert compressor.should_compress(1000) is False
        assert compressor.should_compress(compressor.threshold_tokens - 1) is False
    
    def test_above_threshold_triggers_compress(self):
        """Messages above threshold should trigger compression."""
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=0.50,
        )
        
        # Above threshold
        assert compressor.should_compress(compressor.threshold_tokens + 1) is True
        assert compressor.should_compress(compressor.threshold_tokens * 2) is True
    
    def test_at_threshold_no_compress(self):
        """Messages exactly at threshold should not trigger compression."""
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=0.50,
        )
        
        # At threshold (not greater than)
        assert compressor.should_compress(compressor.threshold_tokens) is False


class TestHeadProtection:
    """Tests for head message protection.
    
    Validated by: Property 2 - Head Message Protection
    """
    
    def test_head_messages_preserved(self):
        """First N messages should be preserved unchanged."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=3,
            threshold_percent=0.10,  # Low threshold to trigger compression
        )
        
        messages = [
            create_message("system", "System prompt"),
            create_message("user", "Hello"),
            create_message("assistant", "Hi there!"),
            create_message("user", "Question 1"),
            create_message("assistant", "Answer 1"),
            create_message("user", "Question 2"),
            create_message("assistant", "Answer 2"),
        ]
        
        compressed = compressor.compress(messages)
        
        # First 3 messages should be unchanged
        assert len(compressed) >= 3
        assert compressed[0] == messages[0]
        assert compressed[1] == messages[1]
        assert compressed[2] == messages[2]
    
    def test_all_messages_protected_when_fewer_than_n(self):
        """When message count < protect_first_n, all should be protected."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=5,
            threshold_percent=0.10,
        )
        
        messages = [
            create_message("system", "System"),
            create_message("user", "Hi"),
            create_message("assistant", "Hello"),
        ]
        
        compressed = compressor.compress(messages)
        
        # All messages should be present (no compression needed anyway)
        assert len(compressed) == 3
        assert compressed == messages


class TestTailProtection:
    """Tests for token-budget based tail protection.
    
    Validated by: Property 3 - Token-Budget Tail Protection
    """
    
    def test_tail_protected_by_token_budget(self):
        """Tail should be protected based on token budget, not fixed count."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
            tail_budget_percent=0.10,  # Small budget for testing
        )
        
        # Create messages with varying sizes
        messages = [
            create_message("system", "System"),  # Head
        ]
        
        # Add many middle messages
        for i in range(20):
            messages.append(create_message("user", f"Question {i}" * 100))
            messages.append(create_message("assistant", f"Answer {i}" * 100))
        
        # Add recent messages (should be in tail)
        recent_messages = [
            create_message("user", "Recent question"),
            create_message("assistant", "Recent answer"),
        ]
        messages.extend(recent_messages)
        
        compressed = compressor.compress(messages)
        
        # Recent messages should be in the result (in tail)
        # Check that the last user message is preserved
        user_msgs = [m for m in compressed if m.get("role") == "user"]
        assert any("Recent question" in m.get("content", "") for m in user_msgs)
    
    def test_minimum_tail_messages_preserved(self):
        """At least MIN_TAIL_MESSAGES should be preserved in tail."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
            tail_budget_percent=0.01,  # Very small budget
        )
        
        messages = [
            create_message("system", "System"),  # Head
        ]
        
        # Add enough messages to trigger compression
        for i in range(10):
            messages.append(create_message("user", f"Q{i}"))
            messages.append(create_message("assistant", f"A{i}"))
        
        compressed = compressor.compress(messages)
        
        # Should have at least head + MIN_TAIL_MESSAGES
        assert len(compressed) >= 1 + MIN_TAIL_MESSAGES


class TestToolPairIntegrity:
    """Tests for tool-call/result pair integrity.
    
    Validated by: Property 4 - Tool-Call/Result Pair Integrity
    """
    
    def test_tool_pairs_stay_together(self):
        """Tool call and result pairs should stay together after compression."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
        )
        
        messages = [
            create_message("system", "System"),  # Head
        ]
        
        # Add many messages to create middle section
        for i in range(10):
            messages.append(create_message("user", f"Question {i}" * 50))
            messages.append(create_message("assistant", f"Answer {i}" * 50))
        
        # Add tool call in what would be middle
        tool_call_id = "call_123"
        messages.append(create_tool_call_message([
            {"id": tool_call_id, "function": {"name": "test_tool", "arguments": "{}"}}
        ]))
        
        # Add more messages
        for i in range(5):
            messages.append(create_message("user", f"More Q{i}" * 50))
            messages.append(create_message("assistant", f"More A{i}" * 50))
        
        # Add tool result in tail (should pull tool call to tail too)
        messages.append(create_tool_result(tool_call_id, "Tool result"))
        
        compressed = compressor.compress(messages)
        
        # Find tool result
        tool_result_idx = None
        for i, msg in enumerate(compressed):
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
                tool_result_idx = i
                break
        
        assert tool_result_idx is not None, "Tool result should be preserved"
        
        # Find corresponding tool call
        tool_call_found = False
        for i, msg in enumerate(compressed):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    if tc.get("id") == tool_call_id:
                        tool_call_found = True
                        # Tool call should be before or at the tool result
                        assert i <= tool_result_idx
                        break
        
        assert tool_call_found, "Tool call should be preserved with its result"
    
    def test_multiple_tool_pairs_preserved(self):
        """Multiple tool call/result pairs should all be preserved."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=1,
            threshold_percent=0.10,
        )
        
        messages = [
            create_message("system", "System"),
        ]
        
        # Add multiple tool pairs
        tool_ids = ["call_1", "call_2", "call_3"]
        for tid in tool_ids:
            messages.append(create_tool_call_message([
                {"id": tid, "function": {"name": f"tool_{tid}", "arguments": "{}"}}
            ]))
            messages.append(create_tool_result(tid, f"Result for {tid}"))
        
        compressed = compressor.compress(messages)
        
        # All tool results should be present
        for tid in tool_ids:
            found = any(
                m.get("role") == "tool" and m.get("tool_call_id") == tid
                for m in compressed
            )
            assert found, f"Tool result {tid} should be preserved"


class TestCompressionResult:
    """Tests for CompressionResult dataclass."""
    
    def test_compression_result_metadata(self):
        """CompressionResult should contain accurate metadata when compression occurs."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=2,
            threshold_percent=0.50,  # 50% of 128K = 64K threshold
        )
        
        messages = [
            create_message("system", "System"),
            create_message("user", "Hello"),
        ]
        
        # Add many messages to exceed 64K token threshold
        # Each message is ~400 chars = ~100 tokens
        # Need ~640 messages to hit 64K tokens
        for i in range(400):
            messages.append(create_message("user", f"Question number {i} " * 80))
            messages.append(create_message("assistant", f"Answer number {i} " * 80))
        
        result = compressor.get_compression_result(messages)
        
        # Verify compression occurred
        assert result.original_token_count > compressor.threshold_tokens
        assert result.compressed_token_count > 0
        assert result.head_count == 2
        assert result.tail_count >= MIN_TAIL_MESSAGES
        assert result.was_compressed is True
        assert len(result.messages) < len(messages)
    
    def test_compression_result_no_compression(self):
        """CompressionResult should indicate no compression when below threshold."""
        compressor = ContextCompressor(
            model="gpt-4o",
            protect_first_n=2,
            threshold_percent=0.50,
        )
        
        messages = [
            create_message("system", "System"),
            create_message("user", "Hello"),
            create_message("assistant", "Hi there!"),
        ]
        
        result = compressor.get_compression_result(messages)
        
        # Should not compress small messages
        assert result.was_compressed is False
        assert len(result.messages) == len(messages)


class TestEdgeCases:
    """Tests for edge cases."""
    
    def test_empty_messages(self):
        """Empty message list should return empty."""
        compressor = ContextCompressor(model="gpt-4o")
        
        result = compressor.compress([])
        assert result == []
    
    def test_single_message(self):
        """Single message should be preserved."""
        compressor = ContextCompressor(model="gpt-4o")
        
        messages = [create_message("system", "System")]
        result = compressor.compress(messages)
        
        assert result == messages
    
    def test_no_compression_needed(self):
        """Messages below threshold should not be compressed."""
        compressor = ContextCompressor(
            model="gpt-4o",
            threshold_percent=0.90,  # High threshold
        )
        
        messages = [
            create_message("system", "System"),
            create_message("user", "Hello"),
            create_message("assistant", "Hi"),
        ]
        
        result = compressor.compress(messages)
        
        # Should return same messages (no compression)
        assert result == messages
