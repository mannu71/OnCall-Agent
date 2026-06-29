"""Memory Manager for orchestrating multiple memory providers.

This module provides the MemoryProvider protocol and MemoryManager class
for managing built-in and external memory providers with a single integration point.
"""

from typing import Any, Dict, List, Optional, Protocol, Tuple
import asyncio
import hashlib
import logging
import os
import time

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class _TTLCache:
    """Minimal TTL cache: dict[key] -> (expires_at, value) with lazy eviction."""

    def __init__(self, maxsize: int, ttl: float):
        self.maxsize = max(1, int(maxsize))
        self.ttl = float(ttl)
        self._data: Dict[Any, Tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> Tuple[bool, Any]:
        entry = self._data.get(key)
        now = time.monotonic()
        if entry is None:
            self.misses += 1
            return False, None
        expires_at, value = entry
        if expires_at < now:
            self._data.pop(key, None)
            self.misses += 1
            return False, None
        self.hits += 1
        return True, value

    def set(self, key: Any, value: Any) -> None:
        now = time.monotonic()
        if len(self._data) >= self.maxsize and key not in self._data:
            # Evict oldest (by insertion order); also sweep expired.
            expired = [k for k, (exp, _) in self._data.items() if exp < now]
            for k in expired:
                self._data.pop(k, None)
            if len(self._data) >= self.maxsize:
                try:
                    oldest = next(iter(self._data))
                    self._data.pop(oldest, None)
                except StopIteration:
                    pass
        self._data[key] = (now + self.ttl, value)

    def invalidate_prefix(self, session_id: str) -> None:
        keys = [k for k in self._data if isinstance(k, tuple) and k and k[0] == session_id]
        for k in keys:
            self._data.pop(k, None)

    def __len__(self) -> int:
        return len(self._data)


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
    
    def __init__(self, providers: Optional[List[MemoryProvider]] = None):
        """Initialize MemoryManager.

        Args:
            providers: Optional list of providers to pre-register (all treated as external-safe;
                first one becomes built-in if provided).
        """
        self._providers: List[MemoryProvider] = []
        self._has_external: bool = False
        self._tool_provider_map: Dict[str, MemoryProvider] = {}

        # TTL cache for hot-path reads (prefetch). Configurable via env.
        maxsize = _env_int("MEMORY_CACHE_MAXSIZE", 512)
        ttl = _env_int("MEMORY_CACHE_TTL_SECONDS", 60)
        self._cache = _TTLCache(maxsize=maxsize, ttl=ttl)
        self._cache_lock = asyncio.Lock()

        if providers:
            for i, p in enumerate(providers):
                self.add_provider(p, is_builtin=(i == 0))
    
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
        query_hash = hashlib.sha256(str(query).encode("utf-8", errors="replace")).hexdigest()
        cache_key = (session_id, query_hash)

        async with self._cache_lock:
            found, cached = self._cache.get(cache_key)
            if found:
                return cached

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

        result = "\n\n".join(contexts)
        async with self._cache_lock:
            self._cache.set(cache_key, result)
        return result
    
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

        # Write path: invalidate cached prefetches for this session.
        self._invalidate_session(session_id)
    
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
            result = provider.handle_tool_call(tool_name, args)
            # Write-ish tool calls may mutate state — invalidate cached prefetches.
            mutating = any(
                kw in tool_name.lower()
                for kw in ("save", "store", "update", "write", "add", "set", "delete", "remove")
            )
            if mutating:
                sid = args.get("session_id", "") if isinstance(args, dict) else ""
                if sid:
                    self._invalidate_session(sid)
                else:
                    self._invalidate_all()
            return result
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
    
    def _invalidate_session(self, session_id: str) -> None:
        """Invalidate all cached entries for a given session_id."""
        if session_id is None:
            return
        self._cache.invalidate_prefix(session_id)

    def _invalidate_all(self) -> None:
        """Drop the entire prefetch cache."""
        self._cache._data.clear()

    def cache_stats(self) -> Dict[str, int]:
        """Return prefetch cache statistics.

        Returns:
            Dict with hits, misses, and current size.
        """
        return {
            "hits": int(self._cache.hits),
            "misses": int(self._cache.misses),
            "size": len(self._cache),
        }

    def get_provider_names(self) -> List[str]:
        """Get names of all registered providers.
        
        Returns:
            List of provider names
        """
        return [p.name for p in self._providers]
