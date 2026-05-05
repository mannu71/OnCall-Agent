"""Property-based tests for Tool Registry.

Tests universal correctness properties:
- Property 16: Tool Registry Retrieval - Registered tools return correct schema and handler
"""

import pytest
from hypothesis import given, strategies as st
from app.core.tool_registry import ToolRegistry


# Strategies for generating test data
@st.composite
def tool_name_strategy(draw):
    """Generate valid tool names."""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'),
        min_size=1,
        max_size=50
    ))


@st.composite
def tool_schema_strategy(draw):
    """Generate valid tool schemas."""
    name = draw(tool_name_strategy())
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": draw(st.text(min_size=1, max_size=200)),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }


@st.composite
def emoji_strategy(draw):
    """Generate emoji characters."""
    emojis = ["⚡", "🔧", "📁", "🌐", "🔍", "💾", "🚀", "⚙️", "📊", "🎯"]
    return draw(st.sampled_from(emojis))


# Property 16: Tool Registry Retrieval
@given(
    tool_name=tool_name_strategy(),
    schema=tool_schema_strategy(),
    emoji=emoji_strategy()
)
def test_property_16_tool_registry_retrieval(tool_name, schema, emoji):
    """Property 16: Registered tools return correct schema and handler.
    
    Universal property: For any tool registered with a schema and handler,
    retrieving that tool by name MUST return the exact schema and handler
    that were registered.
    """
    registry = ToolRegistry()
    
    # Create a simple handler
    def handler(*args, **kwargs):
        return f"executed_{tool_name}"
    
    # Register the tool
    registry.register(
        name=tool_name,
        schema=schema,
        handler=handler,
        emoji=emoji
    )
    
    # Property: Retrieved schema matches registered schema
    retrieved_schema = registry.get_schema(tool_name)
    assert retrieved_schema == schema, \
        f"Retrieved schema does not match registered schema for tool '{tool_name}'"
    
    # Property: Retrieved handler matches registered handler
    retrieved_handler = registry.get_handler(tool_name)
    assert retrieved_handler is handler, \
        f"Retrieved handler does not match registered handler for tool '{tool_name}'"
    
    # Property: Retrieved emoji matches registered emoji
    retrieved_emoji = registry.get_emoji(tool_name)
    assert retrieved_emoji == emoji, \
        f"Retrieved emoji does not match registered emoji for tool '{tool_name}'"
    
    # Property: Tool is in the list of registered tools
    assert tool_name in registry.list_tools(), \
        f"Tool '{tool_name}' not found in list_tools()"
    
    # Property: Tool is available by default (no availability check)
    assert registry.is_available(tool_name), \
        f"Tool '{tool_name}' should be available by default"
    
    # Property: Schema is included in get_all_schemas()
    all_schemas = registry.get_all_schemas()
    assert schema in all_schemas, \
        f"Schema for tool '{tool_name}' not found in get_all_schemas()"


def test_tool_registry_availability_check():
    """Test that availability checks are respected."""
    registry = ToolRegistry()
    
    # Register tool with availability check that returns False
    def unavailable_check():
        return False
    
    registry.register(
        name="unavailable_tool",
        schema={"type": "function", "function": {"name": "unavailable_tool"}},
        handler=lambda: None,
        availability_check=unavailable_check
    )
    
    # Tool should not be available
    assert not registry.is_available("unavailable_tool")
    
    # Tool schema should not be in get_all_schemas()
    all_schemas = registry.get_all_schemas()
    assert not any(s.get("function", {}).get("name") == "unavailable_tool" for s in all_schemas)


def test_tool_registry_nonexistent_tool():
    """Test behavior with nonexistent tools."""
    registry = ToolRegistry()
    
    # Nonexistent tool should return None for schema and handler
    assert registry.get_schema("nonexistent") is None
    assert registry.get_handler("nonexistent") is None
    
    # Nonexistent tool should return default emoji
    assert registry.get_emoji("nonexistent") == "⚡"
    assert registry.get_emoji("nonexistent", default="🔧") == "🔧"
    
    # Nonexistent tool should not be available
    assert not registry.is_available("nonexistent")


def test_tool_registry_overwrite():
    """Test that registering a tool twice overwrites the first registration."""
    registry = ToolRegistry()
    
    def handler1():
        return "handler1"
    
    def handler2():
        return "handler2"
    
    schema1 = {"type": "function", "function": {"name": "tool", "description": "first"}}
    schema2 = {"type": "function", "function": {"name": "tool", "description": "second"}}
    
    # Register tool twice
    registry.register("tool", schema1, handler1, emoji="⚡")
    registry.register("tool", schema2, handler2, emoji="🔧")
    
    # Second registration should overwrite first
    assert registry.get_schema("tool") == schema2
    assert registry.get_handler("tool") is handler2
    assert registry.get_emoji("tool") == "🔧"


def test_tool_registry_clear():
    """Test that clear() removes all tools."""
    registry = ToolRegistry()
    
    registry.register("tool1", {}, lambda: None)
    registry.register("tool2", {}, lambda: None)
    
    assert len(registry.list_tools()) == 2
    
    registry.clear()
    
    assert len(registry.list_tools()) == 0
    assert registry.get_schema("tool1") is None
    assert registry.get_handler("tool1") is None
