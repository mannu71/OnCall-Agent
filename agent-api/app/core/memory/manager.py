"""Memory Manager for orchestrating multiple memory providers.

This module provides the MemoryProvider protocol and MemoryManager class
for managing built-in and external memory providers with a single integration point.
"""

from typing import Any, Dict, List, Protocol
import logging

logger = logging.getLogger(__name__)


class MemoryProvider(Protocol):
    """Interface for memory providers.
    
    Memory providers implement persistent storage and retrieval of agent knowledge.
    The MemoryManager orchestrates multiple providers with lifecycle hooks.
    """
    
    name: str
    
    def system_prompt_block(self) -> str:
        """Generate system prompt block for this provider.
        
        Returns:
            System prompt text to inject into agent context
        """
        ...
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas provided by this memory provider.
        
        Returns:
            List of OpenAI-compatible tool schemas
        """
        ...
    
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Handle a tool call routed to this provider.
        
        Args:
            tool_name: Name of the tool being called
            args: Tool arguments
            
        Returns:
            Tool execution result as string
        """
        ...
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        """Prefetch relevant context before agent turn.
        
        Args:
            query: User query to search for relevant context
            session_id: Optional session identifier
            
        Returns:
            Prefetched context as formatted string
        """
        ...
    
    async def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
    ) -> None:
        """Sync completed turn to memory storage.
        
        Args:
            user_content: User message content
            assistant_content: Assistant response content
            session_id: Optional session identifier
        """
        ...
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Lifecycle hook called at turn start.
        
        Args:
            turn_number: Current turn number
            message: User message
            **kwargs: Additional context
        """
        ...
    
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Lifecycle hook called at session end.
        
        Args:
            messages: Complete message history
        """
        ...
    
    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Lifecycle hook called before context compression.
        
        Args:
            messages: Messages about to be compressed
            
        Returns:
            Additional context to preserve during compression
        """
        ...


class MemoryManager:
    """Orchestrates built-in + one external memory provider.
    
    The MemoryManager provides a single integration point for memory systems:
    - Always registers a built-in memory provider first
    - Allows at most one external memory provider to prevent schema bloat
    - Builds system prompts from all registered providers
    - Prefetches context from all providers before each turn
    - Syncs completed turns to all providers
    - Routes tool calls to the correct provider
    
    Requirements: 9.1-9.6
    """
    
    def __init__(self):
        """Initialize MemoryManager."""
        self._providers: List[MemoryProvider] = []
        self._has_external: bool = False
        self._tool_provider_map: Dict[str, MemoryProvider] = {}
    
    def add_provider(self, provider: MemoryProvider, is_builtin: bool = False) -> None:
        """Register a memory provider.
        
        Only ONE external (non-builtin) provider allowed to prevent schema bloat.
        
        Args:
            provider: MemoryProvider instance to register
            is_builtin: Whether this is the built-in provider
            
        Raises:
            ValueError: If attempting to register a second external provider
        """
        # Requirement 9.3: Allow at most one external memory provider
        if not is_builtin and self._has_external:
            raise ValueError(
                "Only one external memory provider allowed. "
                f"Already registered: {[p.name for p in self._providers if p.name != 'builtin']}"
            )
        
        # Requirement 9.2: Always register built-in provider first
        if is_builtin and self._providers:
            logger.warning("Built-in provider should be registered first")
        
        self._providers.append(provider)
        
        if not is_builtin:
            self._has_external = True
        
        # Build tool routing map
        try:
            tool_schemas = provider.get_tool_schemas()
            for schema in tool_schemas:
                tool_name = schema.get("function", {}).get("name") or schema.get("name")
                if tool_name:
                    self._tool_provider_map[tool_name] = provider
            
            logger.info(
                f"Registered memory provider: {provider.name} "
                f"(builtin={is_builtin}, tools={len(tool_schemas)})"
            )
        except Exception as e:
            logger.error(
                f"Failed to get tool schemas from provider '{provider.name}': {e}"
            )
            logger.info(
                f"Registered memory provider: {provider.name} "
                f"(builtin={is_builtin}, tools=unknown)"
            )
    
    def build_system_prompt(self) -> str:
        """Collect system prompt blocks from all providers.
        
        Requirement 9.4: Build system prompts from all registered providers
        
        Returns:
            Combined system prompt text
        """
        blocks = []
        
        for provider in self._providers:
            try:
                block = provider.system_prompt_block()
                if block:
                    blocks.append(block)
            except Exception as e:
                # Requirement 9.8: Handle provider failures gracefully
                logger.error(
                    f"Failed to get system prompt from provider '{provider.name}': {e}"
                )
        
        return "\n\n".join(blocks)
    
    async def prefetch_all(self, query: str, session_id: str = "") -> str:
        """Collect prefetch context from all providers.
        
        Requirement 9.5: Prefetch context from all providers before each turn
        
        Args:
            query: User query to search for relevant context
            session_id: Optional session identifier
            
        Returns:
            Combined prefetch context
        """
        contexts = []
        
        for provider in self._providers:
            try:
                context = await provider.prefetch(query, session_id)
                if context:
                    contexts.append(f"## Memory: {provider.name}\n\n{context}")
            except Exception as e:
                # Requirement 9.8: Handle provider failures gracefully
                logger.error(
                    f"Failed to prefetch from provider '{provider.name}': {e}"
                )
        
        return "\n\n".join(contexts)
    
    async def sync_all(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
    ) -> None:
        """Sync completed turn to all providers.
        
        Requirement 9.6: Sync completed turns to all providers
        
        Args:
            user_content: User message content
            assistant_content: Assistant response content
            session_id: Optional session identifier
        """
        for provider in self._providers:
            try:
                await provider.sync_turn(user_content, assistant_content, session_id)
            except Exception as e:
                # Requirement 9.8: Handle provider failures gracefully
                logger.error(
                    f"Failed to sync turn to provider '{provider.name}': {e}"
                )
    
    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """Collect tool schemas from all providers.
        
        Returns:
            List of all tool schemas from registered providers
        """
        schemas = []
        
        for provider in self._providers:
            try:
                provider_schemas = provider.get_tool_schemas()
                schemas.extend(provider_schemas)
            except Exception as e:
                logger.error(
                    f"Failed to get tool schemas from provider '{provider.name}': {e}"
                )
        
        return schemas
    
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Route tool call to correct provider.
        
        Requirement 9.7: Route tool calls to the correct provider
        
        Args:
            tool_name: Name of the tool being called
            args: Tool arguments
            
        Returns:
            Tool execution result
            
        Raises:
            ValueError: If tool is not registered with any provider
        """
        provider = self._tool_provider_map.get(tool_name)
        
        if not provider:
            raise ValueError(
                f"Tool '{tool_name}' not registered with any memory provider. "
                f"Available tools: {list(self._tool_provider_map.keys())}"
            )
        
        try:
            return provider.handle_tool_call(tool_name, args)
        except Exception as e:
            logger.error(
                f"Failed to handle tool call '{tool_name}' "
                f"with provider '{provider.name}': {e}"
            )
            raise
    
    def notify_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Notify all providers of turn start.
        
        Requirement 9.9: Provide lifecycle hooks for turn start
        
        Args:
            turn_number: Current turn number
            message: User message
            **kwargs: Additional context
        """
        for provider in self._providers:
            try:
                provider.on_turn_start(turn_number, message, **kwargs)
            except Exception as e:
                logger.error(
                    f"Failed to notify turn start to provider '{provider.name}': {e}"
                )
    
    def notify_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Notify all providers of session end.
        
        Requirement 9.9: Provide lifecycle hooks for session end
        
        Args:
            messages: Complete message history
        """
        for provider in self._providers:
            try:
                provider.on_session_end(messages)
            except Exception as e:
                logger.error(
                    f"Failed to notify session end to provider '{provider.name}': {e}"
                )
    
    def collect_pre_compress_context(self, messages: List[Dict[str, Any]]) -> str:
        """Collect context to preserve before compression.
        
        Requirement 9.9: Provide lifecycle hooks for compression events
        
        Args:
            messages: Messages about to be compressed
            
        Returns:
            Combined context to preserve
        """
        contexts = []
        
        for provider in self._providers:
            try:
                context = provider.on_pre_compress(messages)
                if context:
                    contexts.append(context)
            except Exception as e:
                logger.error(
                    f"Failed to collect pre-compress context "
                    f"from provider '{provider.name}': {e}"
                )
        
        return "\n\n".join(contexts)
    
    def has_providers(self) -> bool:
        """Check if any providers are registered.
        
        Returns:
            True if at least one provider is registered
        """
        return len(self._providers) > 0
    
    def get_provider_names(self) -> List[str]:
        """Get names of all registered providers.
        
        Returns:
            List of provider names
        """
        return [p.name for p in self._providers]
