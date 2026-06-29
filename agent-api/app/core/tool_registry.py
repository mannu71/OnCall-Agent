"""Tool Registry for centralized tool management.

This module provides a central registry for managing tool schemas, handlers,
and metadata. It integrates with MCP tools and provides a unified interface
for tool discovery and execution.
"""

from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Definition of a registered tool."""
    name: str
    schema: Dict[str, Any]
    handler: Callable
    emoji: str = "⚡"
    availability_check: Optional[Callable[[], bool]] = None


class ToolRegistry:
    """Central registry for tool schemas and handlers.
    
    Provides a unified interface for:
    - Tool registration with schema and handler
    - Tool discovery and availability checking
    - Emoji icons for display
    - Integration with MCP tools
    """
    
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
    
    def register(
        self,
        name: str,
        schema: Dict[str, Any],
        handler: Callable,
        emoji: str = "⚡",
        availability_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register a tool with schema, handler, and metadata.
        
        Args:
            name: Tool name (unique identifier)
            schema: OpenAI-compatible tool schema
            handler: Callable that executes the tool
            emoji: Display emoji for the tool
            availability_check: Optional function to check if tool is available
        """
        if name in self._tools:
            logger.warning(f"Tool '{name}' already registered, overwriting")
        
        self._tools[name] = ToolDefinition(
            name=name,
            schema=schema,
            handler=handler,
            emoji=emoji,
            availability_check=availability_check,
        )
        logger.debug(f"Registered tool: {name} {emoji}")
    
    def get_schema(self, name: str) -> Optional[Dict[str, Any]]:
        """Get OpenAI-compatible tool schema.
        
        Args:
            name: Tool name
            
        Returns:
            Tool schema dict or None if not found
        """
        tool = self._tools.get(name)
        return tool.schema if tool else None
    
    def get_handler(self, name: str) -> Optional[Callable]:
        """Get tool handler function.
        
        Args:
            name: Tool name
            
        Returns:
            Tool handler callable or None if not found
        """
        tool = self._tools.get(name)
        return tool.handler if tool else None
    
    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """Get display emoji for tool.
        
        Args:
            name: Tool name
            default: Default emoji if tool not found
            
        Returns:
            Tool emoji or default
        """
        tool = self._tools.get(name)
        return tool.emoji if tool else default
    
    def is_available(self, name: str) -> bool:
        """Check if tool is currently available.
        
        Args:
            name: Tool name
            
        Returns:
            True if tool is available, False otherwise
        """
        tool = self._tools.get(name)
        if not tool:
            return False
        
        if tool.availability_check is None:
            return True
        
        try:
            return tool.availability_check()
        except Exception as e:
            logger.warning(f"Availability check failed for tool '{name}': {e}")
            return False
    
    def list_tools(self) -> List[str]:
        """List all registered tool names.
        
        Returns:
            List of tool names
        """
        return list(self._tools.keys())
    
    def get_all_schemas(
        self,
        query: Optional[str] = None,
        router: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Get all tool schemas for LLM binding.

        Only returns schemas for available tools. When ``query`` is supplied
        (and ``router`` is None, the module-level ``default_router`` is used),
        the catalog is pruned to top-K by relevance — a 15-25 K-token saving
        per LLM call on rigs with many MCP servers. The router never excludes
        pinned tools, so the agent always sees its core "final answer / ask
        user / escalate" set regardless of score.

        Args:
            query: Optional task/turn text to rank tools against. When
                ``None`` the full available catalog is returned (legacy
                behaviour).
            router: Optional ``ToolRouter`` to use; defaults to the
                module-level ``default_router`` when ``query`` is given.

        Returns:
            List of tool schemas (possibly pruned).
        """
        schemas: List[Dict[str, Any]] = []
        for name, tool in self._tools.items():
            if self.is_available(name):
                schemas.append(tool.schema)

        if query:
            # Late import to avoid a circular import at module load.
            try:
                from app.core.tools.router import ToolRouter, default_router
                active = router if router is not None else default_router
                if isinstance(active, ToolRouter):
                    schemas = active.filter(schemas, query=query)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tool_registry: tool_router unavailable (%s); "
                    "returning full catalog.", exc,
                )
        return schemas
    
    def clear(self) -> None:
        """Clear all registered tools (useful for testing)."""
        self._tools.clear()


# Global registry instance
registry = ToolRegistry()
