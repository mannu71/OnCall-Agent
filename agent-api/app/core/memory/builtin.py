"""Built-in memory provider using KnowledgeBaseService.

This module provides the BuiltinMemoryProvider that integrates with the existing
KnowledgeBaseService for persistent storage and retrieval of agent knowledge.

Requirements: 9.2, 9.5
"""

from typing import Any, Dict, List
import logging

from app.services.knowledge_base import KnowledgeBaseService

logger = logging.getLogger(__name__)


class BuiltinMemoryProvider:
    """Built-in memory provider using KnowledgeBaseService.
    
    This provider implements the MemoryProvider protocol and uses the existing
    KnowledgeBaseService for storage. It searches known issues and patterns during
    prefetch and optionally persists resolved issues during sync_turn.
    
    Requirements:
    - 9.2: Always registered as the first memory provider
    - 9.5: Prefetch context from knowledge base before each turn
    """
    
    name = "builtin"
    
    def __init__(self, knowledge_base: KnowledgeBaseService):
        """Initialize built-in memory provider.
        
        Args:
            knowledge_base: KnowledgeBaseService instance for storage
        """
        self.kb = knowledge_base
    
    def system_prompt_block(self) -> str:
        """Generate system prompt block for this provider.
        
        Returns:
            System prompt text describing memory capabilities
        """
        return """## Memory System

You have access to a knowledge base containing:
- Known issues and their solutions
- Log patterns and error signatures
- Historical analysis results

When you encounter errors or issues, the system will automatically search for similar known issues.
You can reference these in your responses to provide faster, more accurate solutions."""
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas provided by this memory provider.
        
        The built-in provider does not expose tools directly - it operates
        through prefetch and sync mechanisms.
        
        Returns:
            Empty list (no tools exposed)
        """
        return []
    
    def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Handle a tool call routed to this provider.
        
        Args:
            tool_name: Name of the tool being called
            args: Tool arguments
            
        Returns:
            Tool execution result as string
            
        Raises:
            ValueError: Always, as built-in provider has no tools
        """
        raise ValueError(
            f"Built-in memory provider does not support tool calls. "
            f"Attempted to call: {tool_name}"
        )
    
    async def prefetch(self, query: str, session_id: str = "") -> str:
        """Prefetch relevant context before agent turn.
        
        Searches the knowledge base for:
        1. Known issues similar to the query
        2. Log patterns matching the query
        
        Requirement 9.5: Prefetch context from all providers before each turn
        
        Args:
            query: User query to search for relevant context
            session_id: Optional session identifier (unused by built-in provider)
            
        Returns:
            Formatted recall results for agent context
        """
        try:
            # Search for known issues (limit to top 3 most relevant)
            issues = await self.kb.search_known_issues(
                query=query,
                limit=3,
                threshold=0.7
            )
            
            # Search for similar patterns (limit to top 3)
            patterns = await self.kb.search_similar_patterns(
                query=query,
                limit=3,
                threshold=0.7
            )
            
            return self._format_recall(issues, patterns)
            
        except Exception as e:
            logger.error(f"Failed to prefetch from built-in memory: {e}")
            return ""
    
    async def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
    ) -> None:
        """Sync completed turn to memory storage.
        
        Optionally persists resolved issues to the knowledge base if the
        assistant response contains a resolution.
        
        Args:
            user_content: User message content
            assistant_content: Assistant response content
            session_id: Optional session identifier (unused by built-in provider)
        """
        try:
            # Check if this looks like a resolution
            if self._is_resolution(assistant_content):
                # Extract issue information and persist
                # This is a simple heuristic - could be enhanced with LLM extraction
                logger.info("Detected potential resolution in assistant response")
                # TODO: Implement resolution extraction and persistence
                # For now, we just log it
                
        except Exception as e:
            logger.error(f"Failed to sync turn to built-in memory: {e}")
    
    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Lifecycle hook called at turn start.
        
        Args:
            turn_number: Current turn number
            message: User message
            **kwargs: Additional context
        """
        # Built-in provider doesn't need turn start notifications
        pass
    
    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Lifecycle hook called at session end.
        
        Args:
            messages: Complete message history
        """
        # Built-in provider doesn't need session end notifications
        pass
    
    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Lifecycle hook called before context compression.
        
        Args:
            messages: Messages about to be compressed
            
        Returns:
            Additional context to preserve during compression (empty for built-in)
        """
        # Built-in provider doesn't need to preserve additional context
        return ""
    
    def _format_recall(
        self,
        issues: List[Dict[str, Any]],
        patterns: List[Dict[str, Any]]
    ) -> str:
        """Format recall results for agent context.
        
        Args:
            issues: List of known issues from search
            patterns: List of log patterns from search
            
        Returns:
            Formatted string for injection into agent context
        """
        if not issues and not patterns:
            return ""
        
        sections = []
        
        # Format known issues
        if issues:
            issues_text = "### Known Issues\n\n"
            for issue in issues:
                similarity = issue.get("similarity", 0.0)
                issues_text += f"**{issue['title']}** (similarity: {similarity:.2f})\n"
                issues_text += f"- Category: {issue.get('category', 'unknown')}\n"
                
                if issue.get('symptoms'):
                    issues_text += f"- Symptoms: {', '.join(issue['symptoms'][:3])}\n"
                
                if issue.get('solution'):
                    # Truncate long solutions
                    solution = issue['solution']
                    if len(solution) > 200:
                        solution = solution[:200] + "..."
                    issues_text += f"- Solution: {solution}\n"
                
                issues_text += "\n"
            
            sections.append(issues_text)
        
        # Format log patterns
        if patterns:
            patterns_text = "### Relevant Patterns\n\n"
            for pattern in patterns:
                similarity = pattern.get("similarity", 0.0)
                patterns_text += f"**{pattern['name']}** (similarity: {similarity:.2f})\n"
                patterns_text += f"- Type: {pattern.get('pattern_type', 'unknown')}\n"
                patterns_text += f"- Severity: {pattern.get('severity', 1)}/5\n"
                
                if pattern.get('description'):
                    patterns_text += f"- Description: {pattern['description']}\n"
                
                patterns_text += "\n"
            
            sections.append(patterns_text)
        
        return "\n".join(sections)
    
    def _is_resolution(self, assistant_content: str) -> bool:
        """Check if assistant content contains a resolution.
        
        Simple heuristic to detect if the response contains resolution steps.
        Could be enhanced with more sophisticated detection.
        
        Args:
            assistant_content: Assistant response content
            
        Returns:
            True if content appears to contain a resolution
        """
        # Simple keyword-based detection
        resolution_keywords = [
            "resolved",
            "fixed",
            "solution",
            "to fix this",
            "to resolve",
            "steps to",
            "you can fix",
            "here's how to",
        ]
        
        content_lower = assistant_content.lower()
        return any(keyword in content_lower for keyword in resolution_keywords)
