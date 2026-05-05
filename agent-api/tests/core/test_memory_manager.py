"""Tests for MemoryManager lifecycle hooks and error handling.

This module tests Requirements 9.8 and 9.9:
- 9.8: Handle provider failures gracefully without blocking execution
- 9.9: Provide lifecycle hooks for turn start, session end, and compression events
"""

import pytest
from unittest.mock import Mock, AsyncMock
from app.core.memory.manager import MemoryManager, MemoryProvider


class MockMemoryProvider:
    """Mock memory provider for testing."""
    
    def __init__(self, name: str, should_fail: bool = False):
        self.name = name
        self.should_fail = should_fail
        
        # Track calls
        self.turn_start_calls = []
        self.session_end_calls = []
        self.pre_compress_calls = []
        self.system_prompt_calls = 0
        self.prefetch_calls = []
        self.sync_turn_calls = []
        self.tool_schema_calls = 0
    
    def system_prompt_block(self) -> str:
        """Generate system prompt block."""
        self.system_prompt_calls += 1
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in system_prompt_block")
        return f"System prompt from {self.name}"
    
    def get_tool_schemas(self):
        """Get tool schemas."""
        self.tool_schema_calls += 1
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in get_tool_schemas")
        return [
            {
                "function": {
                    "name": f"{self.name}_tool",
                    "description": f"Tool from {self.name}",
                }
            }
        ]
    
    def handle_tool_call(self, tool_name: str, args: dict) -> str:
        """Handle tool call."""
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in handle_tool_call")
        return f"Result from {self.name}"
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        """Prefetch context."""
        self.prefetch_calls.append((query, session_id))
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in prefetch")
        return f"Context from {self.name} for query: {query}"
    
    async def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
    ) -> None:
        """Sync turn."""
        self.sync_turn_calls.append((user_content, assistant_content, session_id))
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in sync_turn")
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Lifecycle hook for turn start."""
        self.turn_start_calls.append((turn_number, message, kwargs))
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in on_turn_start")
    
    def on_session_end(self, messages: list) -> None:
        """Lifecycle hook for session end."""
        self.session_end_calls.append(messages)
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in on_session_end")
    
    def on_pre_compress(self, messages: list) -> str:
        """Lifecycle hook for pre-compression."""
        self.pre_compress_calls.append(messages)
        if self.should_fail:
            raise RuntimeError(f"Provider {self.name} failed in on_pre_compress")
        return f"Pre-compress context from {self.name}"


class TestLifecycleHooks:
    """Test lifecycle hooks (Requirement 9.9)."""
    
    def test_notify_turn_start_calls_all_providers(self):
        """Test that notify_turn_start calls on_turn_start on all providers."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Call notify_turn_start
        manager.notify_turn_start(1, "Hello", extra_data="test")
        
        # Verify both providers were called
        assert len(provider1.turn_start_calls) == 1
        assert len(provider2.turn_start_calls) == 1
        
        # Verify call arguments
        assert provider1.turn_start_calls[0] == (1, "Hello", {"extra_data": "test"})
        assert provider2.turn_start_calls[0] == (1, "Hello", {"extra_data": "test"})
    
    def test_notify_turn_start_handles_provider_failure(self):
        """Test that provider failure in on_turn_start doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Call notify_turn_start - should not raise
        manager.notify_turn_start(1, "Hello")
        
        # Verify provider2 was still called despite provider1 failure
        assert len(provider1.turn_start_calls) == 1
        assert len(provider2.turn_start_calls) == 1
    
    def test_notify_session_end_calls_all_providers(self):
        """Test that notify_session_end calls on_session_end on all providers."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        messages = [{"role": "user", "content": "Hello"}]
        
        # Call notify_session_end
        manager.notify_session_end(messages)
        
        # Verify both providers were called
        assert len(provider1.session_end_calls) == 1
        assert len(provider2.session_end_calls) == 1
        
        # Verify call arguments
        assert provider1.session_end_calls[0] == messages
        assert provider2.session_end_calls[0] == messages
    
    def test_notify_session_end_handles_provider_failure(self):
        """Test that provider failure in on_session_end doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        messages = [{"role": "user", "content": "Hello"}]
        
        # Call notify_session_end - should not raise
        manager.notify_session_end(messages)
        
        # Verify provider2 was still called despite provider1 failure
        assert len(provider1.session_end_calls) == 1
        assert len(provider2.session_end_calls) == 1
    
    def test_collect_pre_compress_context_calls_all_providers(self):
        """Test that collect_pre_compress_context calls on_pre_compress on all providers."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        messages = [{"role": "user", "content": "Hello"}]
        
        # Call collect_pre_compress_context
        result = manager.collect_pre_compress_context(messages)
        
        # Verify both providers were called
        assert len(provider1.pre_compress_calls) == 1
        assert len(provider2.pre_compress_calls) == 1
        
        # Verify call arguments
        assert provider1.pre_compress_calls[0] == messages
        assert provider2.pre_compress_calls[0] == messages
        
        # Verify result combines contexts
        assert "Pre-compress context from provider1" in result
        assert "Pre-compress context from provider2" in result
    
    def test_collect_pre_compress_context_handles_provider_failure(self):
        """Test that provider failure in on_pre_compress doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        messages = [{"role": "user", "content": "Hello"}]
        
        # Call collect_pre_compress_context - should not raise
        result = manager.collect_pre_compress_context(messages)
        
        # Verify provider2 was still called despite provider1 failure
        assert len(provider1.pre_compress_calls) == 1
        assert len(provider2.pre_compress_calls) == 1
        
        # Verify result only contains provider2's context
        assert "Pre-compress context from provider1" not in result
        assert "Pre-compress context from provider2" in result
    
    def test_collect_pre_compress_context_filters_empty_contexts(self):
        """Test that empty contexts are filtered out."""
        manager = MemoryManager()
        
        # Provider that returns empty string
        provider1 = MockMemoryProvider("provider1")
        provider1.on_pre_compress = lambda messages: ""
        
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        messages = [{"role": "user", "content": "Hello"}]
        result = manager.collect_pre_compress_context(messages)
        
        # Verify only provider2's context is in result
        assert "Pre-compress context from provider2" in result
        assert result.count("\n\n") == 0  # No double newlines from empty context


class TestGracefulErrorHandling:
    """Test graceful error handling (Requirement 9.8)."""
    
    def test_build_system_prompt_handles_provider_failure(self):
        """Test that provider failure in system_prompt_block doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Call build_system_prompt - should not raise
        result = manager.build_system_prompt()
        
        # Verify both providers were called
        assert provider1.system_prompt_calls == 1
        assert provider2.system_prompt_calls == 1
        
        # Verify result only contains provider2's prompt
        assert "System prompt from provider1" not in result
        assert "System prompt from provider2" in result
    
    @pytest.mark.asyncio
    async def test_prefetch_all_handles_provider_failure(self):
        """Test that provider failure in prefetch doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Call prefetch_all - should not raise
        result = await manager.prefetch_all("test query", "session123")
        
        # Verify both providers were called
        assert len(provider1.prefetch_calls) == 1
        assert len(provider2.prefetch_calls) == 1
        
        # Verify result only contains provider2's context
        assert "provider1" not in result
        assert "provider2" in result
        assert "test query" in result
    
    @pytest.mark.asyncio
    async def test_sync_all_handles_provider_failure(self):
        """Test that provider failure in sync_turn doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Call sync_all - should not raise
        await manager.sync_all("user message", "assistant response", "session123")
        
        # Verify both providers were called
        assert len(provider1.sync_turn_calls) == 1
        assert len(provider2.sync_turn_calls) == 1
        
        # Verify call arguments
        assert provider1.sync_turn_calls[0] == ("user message", "assistant response", "session123")
        assert provider2.sync_turn_calls[0] == ("user message", "assistant response", "session123")
    
    def test_get_all_tool_schemas_handles_provider_failure(self):
        """Test that provider failure in get_tool_schemas doesn't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Call get_all_tool_schemas - should not raise
        result = manager.get_all_tool_schemas()
        
        # Verify both providers were called
        assert provider1.tool_schema_calls == 2  # Once during add_provider, once in get_all_tool_schemas
        assert provider2.tool_schema_calls == 2
        
        # Verify result only contains provider2's schema
        assert len(result) == 1
        assert result[0]["function"]["name"] == "provider2_tool"
    
    def test_multiple_provider_failures_dont_block_execution(self):
        """Test that multiple provider failures don't block execution."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", should_fail=True)
        provider2 = MockMemoryProvider("provider2", should_fail=True)
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # All operations should complete without raising
        manager.build_system_prompt()
        manager.notify_turn_start(1, "Hello")
        manager.notify_session_end([])
        result = manager.collect_pre_compress_context([])
        
        # Result should be empty but not raise
        assert result == ""


class TestLifecycleHooksIntegration:
    """Integration tests for lifecycle hooks."""
    
    def test_full_lifecycle_flow(self):
        """Test a complete lifecycle flow with all hooks."""
        manager = MemoryManager()
        provider = MockMemoryProvider("test_provider")
        
        manager.add_provider(provider, is_builtin=True)
        
        # Simulate a full lifecycle
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        
        # Turn start
        manager.notify_turn_start(1, "Hello", session_id="test_session")
        assert len(provider.turn_start_calls) == 1
        
        # Pre-compress (if needed)
        context = manager.collect_pre_compress_context(messages)
        assert len(provider.pre_compress_calls) == 1
        assert "Pre-compress context from test_provider" in context
        
        # Session end
        manager.notify_session_end(messages)
        assert len(provider.session_end_calls) == 1
        
        # Verify all hooks were called with correct data
        assert provider.turn_start_calls[0][0] == 1
        assert provider.turn_start_calls[0][1] == "Hello"
        assert provider.pre_compress_calls[0] == messages
        assert provider.session_end_calls[0] == messages
    
    def test_lifecycle_hooks_with_multiple_providers(self):
        """Test lifecycle hooks work correctly with multiple providers."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("builtin")
        provider2 = MockMemoryProvider("external")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        messages = [{"role": "user", "content": "Test"}]
        
        # All hooks should call both providers
        manager.notify_turn_start(1, "Test")
        manager.collect_pre_compress_context(messages)
        manager.notify_session_end(messages)
        
        # Verify both providers received all hooks
        assert len(provider1.turn_start_calls) == 1
        assert len(provider1.pre_compress_calls) == 1
        assert len(provider1.session_end_calls) == 1
        
        assert len(provider2.turn_start_calls) == 1
        assert len(provider2.pre_compress_calls) == 1
        assert len(provider2.session_end_calls) == 1
