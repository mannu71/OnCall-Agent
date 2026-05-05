"""Tests for MemoryManager and MemoryProvider protocol."""

import pytest
from typing import Any, Dict, List
from app.core.memory.manager import MemoryManager, MemoryProvider


class MockMemoryProvider:
    """Mock memory provider for testing."""
    
    def __init__(self, name: str, tools: List[str] = None):
        self.name = name
        self._tools = tools or []
        self.prefetch_called = False
        self.sync_called = False
        self.turn_start_called = False
        self.session_end_called = False
        self.pre_compress_called = False
    
    def system_prompt_block(self) -> str:
        return f"System prompt from {self.name}"
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": f"Tool {tool_name} from {self.name}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for tool_name in self._tools
        ]
    
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        return f"Result from {self.name}.{tool_name}"
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        self.prefetch_called = True
        return f"Prefetch from {self.name}: {query}"
    
    async def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
    ) -> None:
        self.sync_called = True
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self.turn_start_called = True
    
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        self.session_end_called = True
    
    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        self.pre_compress_called = True
        return f"Context from {self.name}"


class TestMemoryManager:
    """Test suite for MemoryManager."""
    
    def test_add_builtin_provider(self):
        """Test adding a built-in provider."""
        manager = MemoryManager()
        provider = MockMemoryProvider("builtin")
        
        manager.add_provider(provider, is_builtin=True)
        
        assert manager.has_providers()
        assert "builtin" in manager.get_provider_names()
        assert not manager._has_external
    
    def test_add_external_provider(self):
        """Test adding an external provider."""
        manager = MemoryManager()
        builtin = MockMemoryProvider("builtin")
        external = MockMemoryProvider("external")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external, is_builtin=False)
        
        assert manager.has_providers()
        assert len(manager.get_provider_names()) == 2
        assert manager._has_external
    
    def test_single_external_provider_limit(self):
        """Test that only one external provider is allowed (Requirement 9.3)."""
        manager = MemoryManager()
        builtin = MockMemoryProvider("builtin")
        external1 = MockMemoryProvider("external1")
        external2 = MockMemoryProvider("external2")
        
        manager.add_provider(builtin, is_builtin=True)
        manager.add_provider(external1, is_builtin=False)
        
        # Attempting to add a second external provider should raise ValueError
        with pytest.raises(ValueError, match="Only one external memory provider allowed"):
            manager.add_provider(external2, is_builtin=False)
    
    def test_build_system_prompt(self):
        """Test building system prompt from all providers (Requirement 9.4)."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        prompt = manager.build_system_prompt()
        
        assert "System prompt from provider1" in prompt
        assert "System prompt from provider2" in prompt
    
    @pytest.mark.asyncio
    async def test_prefetch_all(self):
        """Test prefetching from all providers (Requirement 9.5)."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        result = await manager.prefetch_all("test query", "session123")
        
        assert provider1.prefetch_called
        assert provider2.prefetch_called
        assert "Prefetch from provider1" in result
        assert "Prefetch from provider2" in result
    
    @pytest.mark.asyncio
    async def test_sync_all(self):
        """Test syncing to all providers (Requirement 9.6)."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        await manager.sync_all("user message", "assistant response", "session123")
        
        assert provider1.sync_called
        assert provider2.sync_called
    
    def test_tool_routing(self):
        """Test routing tool calls to correct provider (Requirement 9.7)."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", tools=["tool1", "tool2"])
        provider2 = MockMemoryProvider("provider2", tools=["tool3"])
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Test routing to provider1
        result1 = manager.handle_tool_call("tool1", {})
        assert "provider1.tool1" in result1
        
        result2 = manager.handle_tool_call("tool2", {})
        assert "provider1.tool2" in result2
        
        # Test routing to provider2
        result3 = manager.handle_tool_call("tool3", {})
        assert "provider2.tool3" in result3
    
    def test_tool_routing_unknown_tool(self):
        """Test that unknown tool raises ValueError."""
        manager = MemoryManager()
        provider = MockMemoryProvider("provider", tools=["tool1"])
        
        manager.add_provider(provider, is_builtin=True)
        
        with pytest.raises(ValueError, match="Tool 'unknown_tool' not registered"):
            manager.handle_tool_call("unknown_tool", {})
    
    def test_get_all_tool_schemas(self):
        """Test collecting tool schemas from all providers."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1", tools=["tool1"])
        provider2 = MockMemoryProvider("provider2", tools=["tool2", "tool3"])
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        schemas = manager.get_all_tool_schemas()
        
        assert len(schemas) == 3
        tool_names = [s["function"]["name"] for s in schemas]
        assert "tool1" in tool_names
        assert "tool2" in tool_names
        assert "tool3" in tool_names
    
    def test_lifecycle_hooks(self):
        """Test lifecycle hooks (Requirement 9.9)."""
        manager = MemoryManager()
        provider1 = MockMemoryProvider("provider1")
        provider2 = MockMemoryProvider("provider2")
        
        manager.add_provider(provider1, is_builtin=True)
        manager.add_provider(provider2, is_builtin=False)
        
        # Test turn start
        manager.notify_turn_start(1, "test message")
        assert provider1.turn_start_called
        assert provider2.turn_start_called
        
        # Test session end
        manager.notify_session_end([])
        assert provider1.session_end_called
        assert provider2.session_end_called
        
        # Test pre-compress
        context = manager.collect_pre_compress_context([])
        assert provider1.pre_compress_called
        assert provider2.pre_compress_called
        assert "Context from provider1" in context
        assert "Context from provider2" in context
    
    def test_graceful_failure_handling(self):
        """Test that provider failures don't block execution (Requirement 9.8)."""
        
        class FailingProvider:
            name = "failing"
            
            def system_prompt_block(self) -> str:
                raise RuntimeError("Provider failed")
            
            def get_tool_schemas(self) -> List[Dict[str, Any]]:
                raise RuntimeError("Provider failed")
            
            def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
                raise RuntimeError("Provider failed")
            
            async def prefetch(self, query: str, session_id: str = "") -> str:
                raise RuntimeError("Provider failed")
            
            async def sync_turn(
                self,
                user_content: str,
                assistant_content: str,
                session_id: str = "",
            ) -> None:
                raise RuntimeError("Provider failed")
            
            def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
                raise RuntimeError("Provider failed")
            
            def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
                raise RuntimeError("Provider failed")
            
            def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
                raise RuntimeError("Provider failed")
        
        manager = MemoryManager()
        working_provider = MockMemoryProvider("working")
        failing_provider = FailingProvider()
        
        manager.add_provider(working_provider, is_builtin=True)
        manager.add_provider(failing_provider, is_builtin=False)
        
        # These should not raise exceptions, just log errors
        prompt = manager.build_system_prompt()
        assert "System prompt from working" in prompt
        
        schemas = manager.get_all_tool_schemas()
        assert len(schemas) == 0  # Both providers have no tools or fail
        
        manager.notify_turn_start(1, "test")
        manager.notify_session_end([])
        context = manager.collect_pre_compress_context([])
        assert "Context from working" in context
    
    @pytest.mark.asyncio
    async def test_graceful_failure_async(self):
        """Test graceful failure handling for async methods."""
        
        class FailingProvider:
            name = "failing"
            
            def system_prompt_block(self) -> str:
                return ""
            
            def get_tool_schemas(self) -> List[Dict[str, Any]]:
                return []
            
            def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
                return ""
            
            async def prefetch(self, query: str, session_id: str = "") -> str:
                raise RuntimeError("Prefetch failed")
            
            async def sync_turn(
                self,
                user_content: str,
                assistant_content: str,
                session_id: str = "",
            ) -> None:
                raise RuntimeError("Sync failed")
            
            def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
                pass
            
            def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
                pass
            
            def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
                return ""
        
        manager = MemoryManager()
        working_provider = MockMemoryProvider("working")
        failing_provider = FailingProvider()
        
        manager.add_provider(working_provider, is_builtin=True)
        manager.add_provider(failing_provider, is_builtin=False)
        
        # These should not raise exceptions
        result = await manager.prefetch_all("test query")
        assert "Prefetch from working" in result
        
        await manager.sync_all("user", "assistant")
        assert working_provider.sync_called


class TestMemoryManagerProperties:
    """Property-based tests for MemoryManager using Hypothesis."""
    
    @pytest.mark.asyncio
    async def test_property_19_single_external_provider(self):
        """Property 19: Single External Memory Provider
        
        For any attempt to register a second external memory provider when one is
        already registered, the MemoryManager SHALL reject it with ValueError.
        
        **Validates: Requirements 9.3**
        """
        from hypothesis import given, strategies as st
        
        @given(
            builtin_name=st.text(min_size=1, max_size=20),
            external1_name=st.text(min_size=1, max_size=20),
            external2_name=st.text(min_size=1, max_size=20),
            external1_tools=st.lists(st.text(min_size=1, max_size=15), max_size=5),
            external2_tools=st.lists(st.text(min_size=1, max_size=15), max_size=5),
        )
        def property_test(
            builtin_name,
            external1_name,
            external2_name,
            external1_tools,
            external2_tools,
        ):
            # Ensure names are distinct
            if builtin_name == external1_name or builtin_name == external2_name or external1_name == external2_name:
                return
            
            manager = MemoryManager()
            builtin = MockMemoryProvider(builtin_name)
            external1 = MockMemoryProvider(external1_name, tools=external1_tools)
            external2 = MockMemoryProvider(external2_name, tools=external2_tools)
            
            # Register builtin and first external provider
            manager.add_provider(builtin, is_builtin=True)
            manager.add_provider(external1, is_builtin=False)
            
            # Attempting to add a second external provider should raise ValueError
            with pytest.raises(ValueError, match="Only one external memory provider allowed"):
                manager.add_provider(external2, is_builtin=False)
            
            # Verify only the first external provider is registered
            provider_names = manager.get_provider_names()
            assert external1_name in provider_names
            assert external2_name not in provider_names
        
        property_test()
    
    @pytest.mark.asyncio
    async def test_property_20_tool_routing_correctness(self):
        """Property 20: Tool Routing Correctness
        
        For any tool call, the MemoryManager SHALL route it to the provider that
        registered that tool name.
        
        **Validates: Requirements 9.7**
        """
        from hypothesis import given, strategies as st, assume
        
        @given(
            provider1_name=st.text(min_size=1, max_size=20),
            provider2_name=st.text(min_size=1, max_size=20),
            provider1_tools=st.lists(
                st.text(
                    min_size=1,
                    max_size=15,
                    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_")
                ),
                min_size=1,
                max_size=5,
                unique=True
            ),
            provider2_tools=st.lists(
                st.text(
                    min_size=1,
                    max_size=15,
                    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_")
                ),
                min_size=1,
                max_size=5,
                unique=True
            ),
            tool_args=st.dictionaries(
                st.text(min_size=1, max_size=10),
                st.one_of(st.text(max_size=20), st.integers(), st.booleans()),
                max_size=3
            ),
        )
        def property_test(
            provider1_name,
            provider2_name,
            provider1_tools,
            provider2_tools,
            tool_args,
        ):
            # Ensure provider names are distinct
            assume(provider1_name != provider2_name)
            
            # Ensure tool lists don't overlap
            tool_set1 = set(provider1_tools)
            tool_set2 = set(provider2_tools)
            assume(tool_set1.isdisjoint(tool_set2))
            
            manager = MemoryManager()
            provider1 = MockMemoryProvider(provider1_name, tools=provider1_tools)
            provider2 = MockMemoryProvider(provider2_name, tools=provider2_tools)
            
            manager.add_provider(provider1, is_builtin=True)
            manager.add_provider(provider2, is_builtin=False)
            
            # Test routing for each tool in provider1
            for tool_name in provider1_tools:
                result = manager.handle_tool_call(tool_name, tool_args)
                # Verify the result comes from provider1
                expected_result = f"Result from {provider1_name}.{tool_name}"
                assert result == expected_result, f"Expected '{expected_result}', got '{result}'"
            
            # Test routing for each tool in provider2
            for tool_name in provider2_tools:
                result = manager.handle_tool_call(tool_name, tool_args)
                # Verify the result comes from provider2
                expected_result = f"Result from {provider2_name}.{tool_name}"
                assert result == expected_result, f"Expected '{expected_result}', got '{result}'"
            
            # Test that unknown tool raises ValueError
            unknown_tool = "unknown_tool_xyz_123"
            assume(unknown_tool not in tool_set1 and unknown_tool not in tool_set2)
            
            with pytest.raises(ValueError, match=f"Tool '{unknown_tool}' not registered"):
                manager.handle_tool_call(unknown_tool, tool_args)
        
        property_test()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
