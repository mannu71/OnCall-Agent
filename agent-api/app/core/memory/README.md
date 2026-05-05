# Memory Management Module

This module provides memory provider orchestration for cross-session recall in the agent-api.

## Overview

The Memory Manager orchestrates multiple memory providers with a single integration point:
- Always registers a built-in memory provider first
- Allows at most one external memory provider to prevent schema bloat
- Builds system prompts from all registered providers
- Prefetches context from all providers before each turn
- Syncs completed turns to all providers
- Routes tool calls to the correct provider

## Components

### MemoryProvider Protocol

The `MemoryProvider` protocol defines the interface that all memory providers must implement:

```python
class MemoryProvider(Protocol):
    name: str
    
    def system_prompt_block(self) -> str: ...
    def get_tool_schemas(self) -> List[Dict[str, Any]]: ...
    def handle_tool_call(self, tool_name: str, args: Dict) -> str: ...
    
    async def prefetch(self, query: str, session_id: str = "") -> str: ...
    async def sync_turn(self, user_content: str, assistant_content: str, session_id: str = "") -> None: ...
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None: ...
    def on_session_end(self, messages: List[Dict]) -> None: ...
    def on_pre_compress(self, messages: List[Dict]) -> str: ...
```

### MemoryManager

The `MemoryManager` class orchestrates multiple memory providers:

```python
manager = MemoryManager()

# Add built-in provider first
manager.add_provider(builtin_provider, is_builtin=True)

# Add at most one external provider
manager.add_provider(external_provider, is_builtin=False)

# Build system prompt from all providers
system_prompt = manager.build_system_prompt()

# Prefetch context before agent turn
context = await manager.prefetch_all("user query", "session_id")

# Sync completed turn to all providers
await manager.sync_all("user message", "assistant response", "session_id")

# Route tool calls to correct provider
result = manager.handle_tool_call("tool_name", {"arg": "value"})
```

### BuiltinMemoryProvider

The `BuiltinMemoryProvider` is the default memory provider that integrates with the existing `KnowledgeBaseService`:

```python
from app.core.memory import BuiltinMemoryProvider
from app.services.knowledge_base import knowledge_base

# Create built-in provider
builtin_provider = BuiltinMemoryProvider(knowledge_base)

# Register with memory manager
memory_manager.add_provider(builtin_provider, is_builtin=True)
```

**Features:**
- Searches known issues and patterns during prefetch
- Formats recall results for agent context
- Optionally persists resolved issues during sync_turn
- No tools exposed (operates through prefetch/sync)

**Prefetch Behavior:**
- Searches top 3 most relevant known issues (similarity > 0.7)
- Searches top 3 most relevant log patterns (similarity > 0.7)
- Formats results with similarity scores and metadata
- Returns empty string if no relevant context found

**Sync Behavior:**
- Detects resolution keywords in assistant responses
- Logs potential resolutions for future enhancement
- Gracefully handles errors without blocking execution

## Example Implementation

Here's an example of implementing a custom memory provider:

```python
from typing import Any, Dict, List
from app.core.memory import MemoryProvider

class CustomMemoryProvider:
    """Custom memory provider implementation."""
    
    def __init__(self, name: str = "custom"):
        self.name = name
        self._memory_store = {}
    
    def system_prompt_block(self) -> str:
        return """
        # Custom Memory System
        
        You have access to a custom memory system that can store and retrieve information.
        Use the memory tools to save important facts and recall them later.
        """
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "save_memory",
                    "description": "Save information to memory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "description": "Memory key"},
                            "value": {"type": "string", "description": "Value to store"},
                        },
                        "required": ["key", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_memory",
                    "description": "Recall information from memory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "description": "Memory key"},
                        },
                        "required": ["key"],
                    },
                },
            },
        ]
    
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        if tool_name == "save_memory":
            key = args["key"]
            value = args["value"]
            self._memory_store[key] = value
            return f"Saved '{key}' to memory"
        
        elif tool_name == "recall_memory":
            key = args["key"]
            value = self._memory_store.get(key)
            if value:
                return f"Recalled '{key}': {value}"
            else:
                return f"No memory found for key '{key}'"
        
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        # Search memory for relevant context
        relevant = []
        for key, value in self._memory_store.items():
            if query.lower() in value.lower():
                relevant.append(f"- {key}: {value}")
        
        if relevant:
            return "Relevant memories:\n" + "\n".join(relevant)
        return ""
    
    async def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
    ) -> None:
        # Optionally persist the turn to storage
        pass
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        # Called at the start of each turn
        pass
    
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        # Called when session ends
        pass
    
    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        # Return context to preserve during compression
        return ""
```

## Usage in ReactStrategy

To integrate with the ReactStrategy:

```python
from app.core.memory import MemoryManager, BuiltinMemoryProvider
from app.services.knowledge_base import knowledge_base

# Initialize memory manager
memory_manager = MemoryManager()

# Add built-in provider
builtin_provider = BuiltinMemoryProvider(knowledge_base)
memory_manager.add_provider(builtin_provider, is_builtin=True)

# Optionally add external provider
if external_memory_config:
    external_provider = create_external_provider(external_memory_config)
    memory_manager.add_provider(external_provider, is_builtin=False)

# Use in agent execution
system_prompt = memory_manager.build_system_prompt()
prefetch_context = await memory_manager.prefetch_all(user_query, session_id)
await memory_manager.sync_all(user_message, assistant_response, session_id)
```

## Requirements Validation

This implementation satisfies the following requirements:

- **9.1**: Memory Manager orchestrates multiple memory providers with a single integration point
- **9.2**: Always register a built-in memory provider first (BuiltinMemoryProvider)
- **9.3**: Allow at most one external memory provider to prevent schema bloat
- **9.4**: Build system prompts from all registered providers
- **9.5**: Prefetch context from all providers before each turn (BuiltinMemoryProvider searches known issues and patterns)
- **9.6**: Sync completed turns to all providers
- **9.7**: Route tool calls to the correct provider
- **9.8**: Handle provider failures gracefully without blocking execution
- **9.9**: Provide lifecycle hooks for turn start, session end, and compression events

## Testing

Run the test suite:

```bash
# Test MemoryManager
python -m pytest agent-api/app/core/memory/test_manager.py -v

# Test BuiltinMemoryProvider
python -m pytest agent-api/app/core/memory/test_builtin.py -v

# Run all memory tests
python -m pytest agent-api/app/core/memory/ -v
```

All tests should pass, validating:
- Provider registration and limits
- System prompt building
- Prefetch and sync operations
- Tool routing
- Lifecycle hooks
- Graceful failure handling
- Built-in provider integration with KnowledgeBaseService
