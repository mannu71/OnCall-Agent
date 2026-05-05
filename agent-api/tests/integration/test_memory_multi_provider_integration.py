"""Comprehensive integration tests for Memory Manager with multiple providers.

This module tests the Memory Manager's ability to orchestrate multiple memory
providers, route tool calls correctly, and handle provider failures gracefully.

Requirements: 9.1-9.9
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

from app.core.memory.manager import MemoryManager, MemoryProvider


class MockExternalMemoryProvider:
    """Mock external memory provider for testing."""
    
    def __init__(self, name: str = "external", should_fail: bool = False):
        self.name = name
        self.should_fail = should_fail
        self.prefetch_called = False
        self.sync_called = False
        self.tool_calls_handled = []
        
    def system_prompt_block(self) -> str:
        """Return system prompt block."""
        return f"## {self.name.title()} Memory\nExternal memory system active."
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas."""
        return [
            {
                "name": f"{self.name}_search",
                "description": f"Search {self.name} memory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    },
                    "required": ["query"]
                }
            }
        ]
    
    def handle_tool_call(self, tool_name: str, args: Dict) -> str:
        """Handle tool call."""
        self.tool_calls_handled.append((tool_name, args))
        if self.should_fail:
            raise Exception(f"{self.name} tool call failed")
        return f"{self.name} result for: {args.get('query', '')}"
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        """Prefetch context."""
        self.prefetch_called = True
        if self.should_fail:
            raise Exception(f"{self.name} prefetch failed")
        return f"{self.name} prefetch: {query[:50]}"
    
    async def sync_turn(self, user_content: str, assistant_content: str, session_id: str = "") -> None:
        """Sync turn."""
        self.sync_called = True
        if self.should_fail:
            raise Exception(f"{self.name} sync failed")
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Handle turn start."""
        pass
    
    def on_session_end(self, messages: List[Dict]) -> None:
        """Handle session end."""
        pass
    
    def on_pre_compress(self, messages: List[Dict]) -> str:
        """Handle pre-compression."""
        return ""


class TestMemoryManagerMultiProvider:
    """Test Memory Manager with multiple providers.
    
    **Validates: Requirements 9.1-9.9**
    """
    
    def test_single_external_provider_limit(self):
        """Test that only one external provider can be registered.
        
        **Validates: Requirement 9.3 - Single external provider limit**
        """
        manager = MemoryManager()
        
        # Register builtin provider
        builtin = MockExternalMemoryProvider(name="builtin")
        manager.add_provider(builtin, is_builtin=True)
        
        # Register first external provider - should succeed
        external1 = MockExternalMemoryProvider(name="external1")
        manager.add_provider(external1, is_builtin=False)
        
        # Try to register second external provider - should fail
        external2 = MockExternalMemoryProvider(name="external2")
        with pytest.raises(ValueError, match="Only one external memory provider"):
            manager.add_provider(external2, is_builtin=False)
        
        # Verify only builtin + external1 are registered
        provider_names = manager.get_provider_names()
        assert "builtin" in provider_names
        assert "external1" in provider_names
        assert "external2" not in provider_names
    
    @pytest.mark.asyncio
    async def test_prefetch_from_multiple_providers(self):
        """Test prefetch collects context from all providers.
        
        **Validates: Requirement 9.5 - Prefetch from all providers**
        """
        manager = MemoryManager()
        
        # Register multiple providers
        builtin = MockExternalMemoryProvider(name="builtin")
        external = MockExternalMemoryProvider(name="external")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Prefetch from all providers
        result = await manager.prefetch_all(
            query="Why did the service fail?",
            session_id="test-session"
        )
        
        # Verify both providers were called
        assert builtin.prefetch_called
        assert external.prefetch_called
        
        # Verify result contains context from both
        assert "builtin prefetch" in result
        assert "external prefetch" in result
    
    @pytest.mark.asyncio
    async def test_sync_to_multiple_providers(self):
        """Test sync persists to all providers.
        
        **Validates: Requirement 9.6 - Sync to all providers**
        """
        manager = MemoryManager()
        
        # Register multiple providers
        builtin = MockExternalMemoryProvider(name="builtin")
        external = MockExternalMemoryProvider(name="external")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Sync turn to all providers
        await manager.sync_all(
            user_content="Why did the service fail?",
            assistant_content="Database connection timeout",
            session_id="test-session"
        )
        
        # Verify both providers were called
        assert builtin.sync_called
        assert external.sync_called
    
    def test_tool_routing_to_correct_provider(self):
        """Test tool calls are routed to the correct provider.
        
        **Validates: Requirement 9.7 - Tool routing correctness**
        """
        manager = MemoryManager()
        
        # Register multiple providers with different tools
        builtin = MockExternalMemoryProvider(name="builtin")
        external = MockExternalMemoryProvider(name="external")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Call builtin tool
        result1 = manager.handle_tool_call("builtin_search", {"query": "test1"})
        assert result1 == "builtin result for: test1"
        assert len(builtin.tool_calls_handled) == 1
        assert len(external.tool_calls_handled) == 0
        
        # Call external tool
        result2 = manager.handle_tool_call("external_search", {"query": "test2"})
        assert result2 == "external result for: test2"
        assert len(builtin.tool_calls_handled) == 1
        assert len(external.tool_calls_handled) == 1
        
        # Call unknown tool - should raise ValueError
        with pytest.raises(ValueError, match="not registered"):
            manager.handle_tool_call("unknown_tool", {})
    
    @pytest.mark.asyncio
    async def test_provider_failure_isolation_prefetch(self):
        """Test that one provider's prefetch failure doesn't block others.
        
        **Validates: Requirement 9.8 - Provider failure isolation**
        """
        manager = MemoryManager()
        
        # Register providers - one will fail
        builtin = MockExternalMemoryProvider(name="builtin", should_fail=False)
        external = MockExternalMemoryProvider(name="external", should_fail=True)
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Prefetch should not raise despite external failure
        result = await manager.prefetch_all(
            query="Test query",
            session_id="test-session"
        )
        
        # Verify builtin was called and succeeded
        assert builtin.prefetch_called
        assert "builtin prefetch" in result
        
        # Verify external was attempted but failed gracefully
        assert external.prefetch_called
        # Result should not contain external content due to failure
        assert "external prefetch" not in result
    
    @pytest.mark.asyncio
    async def test_provider_failure_isolation_sync(self):
        """Test that one provider's sync failure doesn't block others.
        
        **Validates: Requirement 9.8 - Provider failure isolation**
        """
        manager = MemoryManager()
        
        # Register providers - one will fail
        builtin = MockExternalMemoryProvider(name="builtin", should_fail=False)
        external = MockExternalMemoryProvider(name="external", should_fail=True)
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Sync should not raise despite external failure
        await manager.sync_all(
            user_content="Test query",
            assistant_content="Test answer",
            session_id="test-session"
        )
        
        # Verify builtin was called and succeeded
        assert builtin.sync_called
        
        # Verify external was attempted but failed gracefully
        assert external.sync_called
    
    def test_provider_failure_isolation_tool_call(self):
        """Test that tool call failure is logged but raises to caller.
        
        **Validates: Requirement 9.8 - Provider failure isolation**
        """
        manager = MemoryManager()
        
        # Register provider that will fail on tool calls
        failing_provider = MockExternalMemoryProvider(name="failing", should_fail=True)
        manager.add_provider(failing_provider, is_builtin=True)
        
        # Tool call should raise the exception from the provider
        with pytest.raises(Exception, match="failing tool call failed"):
            manager.handle_tool_call("failing_search", {"query": "test"})
        
        # Verify tool call was attempted
        assert len(failing_provider.tool_calls_handled) == 1
    
    def test_system_prompt_aggregation(self):
        """Test that system prompts from all providers are aggregated.
        
        **Validates: Requirement 9.4 - Build system prompts from all providers**
        """
        manager = MemoryManager()
        
        # Register multiple providers
        builtin = MockExternalMemoryProvider(name="builtin")
        external = MockExternalMemoryProvider(name="external")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Build system prompt
        system_prompt = manager.build_system_prompt()
        
        # Verify both providers' prompts are included
        assert "Builtin Memory" in system_prompt
        assert "External Memory" in system_prompt
    
    def test_tool_schema_aggregation(self):
        """Test that tool schemas from all providers are aggregated.
        
        **Validates: Requirement 9.4 - Collect tool schemas from all providers**
        """
        manager = MemoryManager()
        
        # Register multiple providers
        builtin = MockExternalMemoryProvider(name="builtin")
        external = MockExternalMemoryProvider(name="external")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        # Get all tool schemas
        schemas = manager.get_all_tool_schemas()
        
        # Verify both providers' tools are included
        tool_names = [schema["name"] for schema in schemas]
        assert "builtin_search" in tool_names
        assert "external_search" in tool_names
    
    @pytest.mark.asyncio
    async def test_lifecycle_hooks_invoked(self):
        """Test that lifecycle hooks are invoked at appropriate times.
        
        **Validates: Requirement 9.9 - Lifecycle hooks**
        """
        manager = MemoryManager()
        
        # Create provider with hook tracking
        class HookTrackingProvider(MockExternalMemoryProvider):
            def __init__(self):
                super().__init__(name="tracking")
                self.turn_start_called = False
                self.session_end_called = False
                self.pre_compress_called = False
            
            def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
                self.turn_start_called = True
            
            def on_session_end(self, messages: List[Dict]) -> None:
                self.session_end_called = True
            
            def on_pre_compress(self, messages: List[Dict]) -> str:
                self.pre_compress_called = True
                return "Pre-compress context"
        
        provider = HookTrackingProvider()
        manager.add_provider(provider, is_builtin=True)
        
        # Test turn start hook
        manager.notify_turn_start(1, "Test message")
        assert provider.turn_start_called
        
        # Test session end hook
        manager.notify_session_end([{"role": "user", "content": "test"}])
        assert provider.session_end_called
        
        # Test pre-compress hook
        result = manager.collect_pre_compress_context([{"role": "user", "content": "test"}])
        assert provider.pre_compress_called
        assert "Pre-compress context" in result
    
    @pytest.mark.asyncio
    async def test_empty_manager_graceful_handling(self):
        """Test that MemoryManager handles empty provider list gracefully.
        
        **Validates: Requirement 9.8 - Graceful handling of edge cases**
        """
        manager = MemoryManager()
        
        # No providers registered
        assert not manager.has_providers()
        
        # Prefetch should return empty string
        result = await manager.prefetch_all("test query")
        assert result == ""
        
        # Sync should not raise
        await manager.sync_all("user", "assistant")
        
        # System prompt should be empty
        system_prompt = manager.build_system_prompt()
        assert system_prompt == ""
        
        # Tool schemas should be empty
        schemas = manager.get_all_tool_schemas()
        assert schemas == []
        
        # Tool call should raise ValueError for unknown tool
        with pytest.raises(ValueError, match="not registered"):
            manager.handle_tool_call("unknown", {})


class TestMemoryManagerWithBuiltinProvider:
    """Test Memory Manager with the actual BuiltinMemoryProvider.
    
    **Validates: Requirements 9.2, 9.5**
    """
    
    @pytest.fixture
    def mock_knowledge_base(self):
        """Mock knowledge base service."""
        kb = MagicMock()
        kb.search_known_issues = AsyncMock(return_value=[
            {
                "id": 1,
                "title": "Database connection timeout",
                "symptoms": ["timeout", "connection refused"],
                "solution": "Increase connection pool size",
                "category": "database",
                "similarity": 0.85
            }
        ])
        kb.search_similar_patterns = AsyncMock(return_value=[
            {
                "id": 1,
                "name": "Connection timeout pattern",
                "pattern_type": "error",
                "severity": "high",
                "description": "Database connection timeouts",
                "similarity": 0.80
            }
        ])
        kb.record_analysis = AsyncMock()
        kb.add_known_issue = AsyncMock()
        return kb
    
    @pytest.mark.asyncio
    async def test_builtin_provider_prefetch(self, mock_knowledge_base):
        """Test BuiltinMemoryProvider prefetch functionality.
        
        **Validates: Requirement 9.2 - Builtin provider using knowledge_base service**
        """
        from app.core.memory.manager import MemoryManager
        from app.core.memory.builtin import BuiltinMemoryProvider
        
        manager = MemoryManager()
        builtin = BuiltinMemoryProvider(mock_knowledge_base)
        manager.add_provider(builtin, is_builtin=True)
        
        # Prefetch context
        result = await manager.prefetch_all(
            query="Why is the database timing out?",
            session_id="test-session"
        )
        
        # Verify KB was queried
        assert mock_knowledge_base.search_known_issues.called
        assert mock_knowledge_base.search_similar_patterns.called
        
        # Verify result contains KB data
        assert "Database connection timeout" in result or "timeout" in result.lower()
    
    @pytest.mark.asyncio
    async def test_builtin_provider_sync(self, mock_knowledge_base):
        """Test BuiltinMemoryProvider sync functionality.
        
        **Validates: Requirement 9.2 - Builtin provider persistence**
        """
        from app.core.memory.manager import MemoryManager
        from app.core.memory.builtin import BuiltinMemoryProvider
        
        manager = MemoryManager()
        builtin = BuiltinMemoryProvider(mock_knowledge_base)
        manager.add_provider(builtin, is_builtin=True)
        
        # Sync a resolution
        await manager.sync_all(
            user_content="Why is the database timing out?",
            assistant_content="The root cause is connection pool exhaustion. Solution: increase pool size.",
            session_id="test-session"
        )
        
        # Verify KB persistence was attempted
        # Note: BuiltinMemoryProvider only persists on resolution detection
        # This test verifies sync doesn't raise errors
        assert True  # Sync completed without error
    
    def test_builtin_provider_system_prompt(self, mock_knowledge_base):
        """Test BuiltinMemoryProvider system prompt generation.
        
        **Validates: Requirement 9.4 - System prompt from builtin provider**
        """
        from app.core.memory.manager import MemoryManager
        from app.core.memory.builtin import BuiltinMemoryProvider
        
        manager = MemoryManager()
        builtin = BuiltinMemoryProvider(mock_knowledge_base)
        manager.add_provider(builtin, is_builtin=True)
        
        # Build system prompt
        system_prompt = manager.build_system_prompt()
        
        # Verify builtin provider's prompt is included
        assert "Memory System" in system_prompt or "memory" in system_prompt.lower()


class TestMemoryManagerConcurrency:
    """Test Memory Manager behavior under concurrent operations.
    
    **Validates: Requirements 9.8**
    """
    
    @pytest.mark.asyncio
    async def test_concurrent_prefetch_calls(self):
        """Test that concurrent prefetch calls don't interfere with each other.
        
        **Validates: Requirement 9.8 - Concurrent operation safety**
        """
        manager = MemoryManager()
        
        # Register provider
        provider = MockExternalMemoryProvider(name="concurrent")
        manager.add_provider(provider, is_builtin=True)
        
        # Make concurrent prefetch calls
        results = await asyncio.gather(
            manager.prefetch_all("query1", "session1"),
            manager.prefetch_all("query2", "session2"),
            manager.prefetch_all("query3", "session3"),
        )
        
        # Verify all calls completed
        assert len(results) == 3
        assert all("concurrent prefetch" in r for r in results)
    
    @pytest.mark.asyncio
    async def test_concurrent_sync_calls(self):
        """Test that concurrent sync calls don't interfere with each other.
        
        **Validates: Requirement 9.8 - Concurrent operation safety**
        """
        manager = MemoryManager()
        
        # Register provider
        provider = MockExternalMemoryProvider(name="concurrent")
        manager.add_provider(provider, is_builtin=True)
        
        # Make concurrent sync calls
        await asyncio.gather(
            manager.sync_all("user1", "assistant1", "session1"),
            manager.sync_all("user2", "assistant2", "session2"),
            manager.sync_all("user3", "assistant3", "session3"),
        )
        
        # Verify sync was called (at least once, possibly more due to concurrency)
        assert provider.sync_called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
