"""Property-based tests for MemoryManager.

This module tests correctness properties for the MemoryManager:
- Property 19: Single External Memory Provider (Requirement 9.3)
- Property 20: Tool Routing Correctness (Requirement 9.7)
"""

import pytest
from hypothesis import given, strategies as st
from app.core.memory.manager import MemoryManager


class SimpleMemoryProvider:
    """Simple memory provider for property testing."""
    
    def __init__(self, name: str, tools: list = None):
        self.name = name
        self._tools = tools or []
    
    def system_prompt_block(self) -> str:
        return f"Prompt from {self.name}"
    
    def get_tool_schemas(self):
        return [
            {
                "function": {
                    "name": tool_name,
                    "description": f"Tool {tool_name} from {self.name}",
                }
            }
            for tool_name in self._tools
        ]
    
    def handle_tool_call(self, tool_name: str, args: dict) -> str:
        return f"Result from {self.name} for {tool_name}"
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        return f"Context from {self.name}"
    
    async def sync_turn(self, user_content: str, assistant_content: str, session_id: str = "") -> None:
        pass
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass
    
    def on_session_end(self, messages: list) -> None:
        pass
    
    def on_pre_compress(self, messages: list) -> str:
        return ""


class TestProperty19SingleExternalProvider:
    """Property 19: Single External Memory Provider.
    
    For any attempt to register a second external memory provider when one is
    already registered, the MemoryManager SHALL reject it with a warning.
    
    **Validates: Requirements 9.3**
    """
    
    @given(
        provider_names=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
            min_size=2,
            max_size=5,
            unique=True
        )
    )
    def test_single_external_provider_limit(self, provider_names):
        """Property: Only one external provider can be registered."""
        manager = MemoryManager()
        
        # Register builtin provider
        builtin = SimpleMemoryProvider("builtin")
        manager.add_provider(builtin, is_builtin=True)
        
        # Register first external provider - should succeed
        external1 = SimpleMemoryProvider(provider_names[0])
        manager.add_provider(external1, is_builtin=False)
        
        # Attempt to register second external provider - should fail
        for name in provider_names[1:]:
            external2 = SimpleMemoryProvider(name)
            with pytest.raises(ValueError, match="Only one external memory provider allowed"):
                manager.add_provider(external2, is_builtin=False)
    
    def test_builtin_providers_not_limited(self):
        """Property: Multiple builtin providers can be registered."""
        manager = MemoryManager()
        
        # Register multiple builtin providers - should all succeed
        for i in range(3):
            provider = SimpleMemoryProvider(f"builtin{i}")
            manager.add_provider(provider, is_builtin=True)
        
        # Verify all were registered
        assert len(manager.get_provider_names()) == 3
    
    @given(
        num_builtins=st.integers(min_value=1, max_value=5)
    )
    def test_one_external_with_multiple_builtins(self, num_builtins):
        """Property: One external provider can coexist with multiple builtins."""
        manager = MemoryManager()
        
        # Register multiple builtin providers
        for i in range(num_builtins):
            provider = SimpleMemoryProvider(f"builtin{i}")
            manager.add_provider(provider, is_builtin=True)
        
        # Register one external provider - should succeed
        external = SimpleMemoryProvider("external")
        manager.add_provider(external, is_builtin=False)
        
        # Verify all were registered
        assert len(manager.get_provider_names()) == num_builtins + 1
        assert "external" in manager.get_provider_names()


class TestProperty20ToolRoutingCorrectness:
    """Property 20: Tool Routing Correctness.
    
    For any tool call, the MemoryManager SHALL route it to the provider that
    registered that tool name.
    
    **Validates: Requirements 9.7**
    """
    
    @given(
        tool_names=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Pc"))),
            min_size=1,
            max_size=10,
            unique=True
        )
    )
    def test_tool_routing_to_correct_provider(self, tool_names):
        """Property: Tool calls are routed to the provider that registered them."""
        manager = MemoryManager()
        
        # Create one builtin provider with all tools (respects single external provider limit)
        provider = SimpleMemoryProvider("builtin", tools=tool_names)
        manager.add_provider(provider, is_builtin=True)
        
        # Test that each tool routes to the correct provider
        for tool_name in tool_names:
            result = manager.handle_tool_call(tool_name, {})
            assert "builtin" in result
            assert tool_name in result
    
    @given(
        tools_per_provider=st.integers(min_value=1, max_value=10)
    )
    def test_multiple_tools_per_provider(self, tools_per_provider):
        """Property: A provider with multiple tools routes all correctly."""
        manager = MemoryManager()
        
        # Create builtin provider with multiple tools
        tools = [f"tool_{j}" for j in range(tools_per_provider)]
        provider = SimpleMemoryProvider("builtin", tools=tools)
        manager.add_provider(provider, is_builtin=True)
        
        # Create one external provider with different tools
        external_tools = [f"ext_tool_{j}" for j in range(tools_per_provider)]
        external = SimpleMemoryProvider("external", tools=external_tools)
        manager.add_provider(external, is_builtin=False)
        
        # Test that all builtin tools route correctly
        for tool_name in tools:
            result = manager.handle_tool_call(tool_name, {})
            assert "builtin" in result
            assert tool_name in result
        
        # Test that all external tools route correctly
        for tool_name in external_tools:
            result = manager.handle_tool_call(tool_name, {})
            assert "external" in result
            assert tool_name in result
    
    def test_unregistered_tool_raises_error(self):
        """Property: Calling an unregistered tool raises ValueError."""
        manager = MemoryManager()
        
        provider = SimpleMemoryProvider("provider", tools=["tool1", "tool2"])
        manager.add_provider(provider, is_builtin=True)
        
        # Calling unregistered tool should raise
        with pytest.raises(ValueError, match="not registered with any memory provider"):
            manager.handle_tool_call("unregistered_tool", {})
    
    @given(
        tool_name=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Pc"))),
        args=st.dictionaries(
            keys=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Lu", "Ll"))),
            values=st.one_of(st.text(), st.integers(), st.booleans()),
            min_size=0,
            max_size=5
        )
    )
    def test_tool_call_with_arbitrary_args(self, tool_name, args):
        """Property: Tool routing works with arbitrary arguments."""
        manager = MemoryManager()
        
        provider = SimpleMemoryProvider("provider", tools=[tool_name])
        manager.add_provider(provider, is_builtin=True)
        
        # Tool call should succeed with any valid arguments
        result = manager.handle_tool_call(tool_name, args)
        assert "provider" in result
        assert tool_name in result


class TestProviderFailureIsolation:
    """Test that provider failures don't affect other providers (Requirement 9.8)."""
    
    @given(
        num_builtins=st.integers(min_value=1, max_value=5),
        failing_indices=st.lists(st.integers(min_value=0, max_value=4), min_size=1, max_size=3, unique=True)
    )
    def test_failing_providers_dont_block_others(self, num_builtins, failing_indices):
        """Property: Failures in some providers don't block others."""
        # Filter failing_indices to be within range
        failing_indices = [i for i in failing_indices if i < num_builtins]
        if not failing_indices:
            failing_indices = [0]
        
        manager = MemoryManager()
        
        # Create builtin providers, some that fail
        providers = []
        for i in range(num_builtins):
            provider = SimpleMemoryProvider(f"builtin{i}")
            
            # Make some providers fail
            if i in failing_indices:
                provider.system_prompt_block = lambda: (_ for _ in ()).throw(RuntimeError("Provider failed"))
            
            providers.append(provider)
            manager.add_provider(provider, is_builtin=True)
        
        # build_system_prompt should not raise
        result = manager.build_system_prompt()
        
        # Result should contain prompts from non-failing providers
        for i in range(num_builtins):
            if i not in failing_indices:
                assert f"builtin{i}" in result
            else:
                assert f"builtin{i}" not in result
