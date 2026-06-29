"""Example usage of BuiltinMemoryProvider with MemoryManager.

This example demonstrates how to integrate the BuiltinMemoryProvider
with the MemoryManager in a typical agent workflow.
"""

import asyncio
from app.core.memory import MemoryManager, BuiltinMemoryProvider
from app.services.knowledge_base import knowledge_base


async def example_usage():
    """Example of using BuiltinMemoryProvider with MemoryManager."""
    
    # Initialize memory manager
    memory_manager = MemoryManager()
    
    # Create and register built-in provider (always first)
    builtin_provider = BuiltinMemoryProvider(knowledge_base)
    memory_manager.add_provider(builtin_provider, is_builtin=True)
    
    # Build system prompt with memory context
    system_prompt = memory_manager.build_system_prompt()
    print("System Prompt:")
    print(system_prompt)
    print("\n" + "="*80 + "\n")
    
    # Prefetch relevant context before agent turn
    user_query = "Database connection timeout error"
    prefetch_context = await memory_manager.prefetch_all(user_query)
    
    if prefetch_context:
        print("Prefetched Context:")
        print(prefetch_context)
        print("\n" + "="*80 + "\n")
    else:
        print("No relevant context found in knowledge base")
        print("\n" + "="*80 + "\n")
    
    # Simulate agent execution
    assistant_response = """
    To resolve the database connection timeout error:
    1. Check your database credentials
    2. Verify network connectivity
    3. Increase connection timeout settings
    """
    
    # Sync the completed turn
    await memory_manager.sync_all(user_query, assistant_response)
    print("Turn synced to memory providers")
    
    # Lifecycle hooks example
    memory_manager.notify_turn_start(1, user_query)
    print("Turn start notification sent")


async def example_with_external_provider():
    """Example showing built-in + external provider."""
    
    # Initialize memory manager
    memory_manager = MemoryManager()
    
    # Register built-in provider first
    builtin_provider = BuiltinMemoryProvider(knowledge_base)
    memory_manager.add_provider(builtin_provider, is_builtin=True)
    
    # You could register ONE external provider here
    # Example: memory_manager.add_provider(custom_provider, is_builtin=False)
    
    print(f"Registered providers: {memory_manager.get_provider_names()}")
    print(f"Has providers: {memory_manager.has_providers()}")


if __name__ == "__main__":
    print("Example 1: Basic Usage")
    print("="*80)
    asyncio.run(example_usage())
    
    print("\n\nExample 2: Multiple Providers")
    print("="*80)
    asyncio.run(example_with_external_provider())
