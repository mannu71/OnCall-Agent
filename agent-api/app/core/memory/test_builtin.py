"""Tests for BuiltinMemoryProvider.

This module tests the built-in memory provider implementation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.memory.builtin import BuiltinMemoryProvider


@pytest.fixture
def mock_knowledge_base():
    """Create a mock KnowledgeBaseService."""
    kb = MagicMock()
    kb.search_known_issues = AsyncMock()
    kb.search_similar_patterns = AsyncMock()
    return kb


@pytest.fixture
def provider(mock_knowledge_base):
    """Create a BuiltinMemoryProvider instance."""
    return BuiltinMemoryProvider(mock_knowledge_base)


class TestBuiltinMemoryProvider:
    """Test suite for BuiltinMemoryProvider."""
    
    def test_name(self, provider):
        """Test provider name is 'builtin'."""
        assert provider.name == "builtin"
    
    def test_system_prompt_block(self, provider):
        """Test system prompt block generation."""
        prompt = provider.system_prompt_block()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "memory" in prompt.lower() or "knowledge" in prompt.lower()
    
    def test_get_tool_schemas(self, provider):
        """Test that built-in provider returns no tool schemas."""
        schemas = provider.get_tool_schemas()
        assert schemas == []
    
    def test_handle_tool_call_raises(self, provider):
        """Test that tool calls raise ValueError."""
        with pytest.raises(ValueError, match="does not support tool calls"):
            provider.handle_tool_call("some_tool", {})
    
    @pytest.mark.asyncio
    async def test_prefetch_empty_results(self, provider, mock_knowledge_base):
        """Test prefetch with no results returns empty string."""
        mock_knowledge_base.search_known_issues.return_value = []
        mock_knowledge_base.search_similar_patterns.return_value = []
        
        result = await provider.prefetch("test query")
        
        assert result == ""
        mock_knowledge_base.search_known_issues.assert_called_once_with(
            query="test query",
            limit=3,
            threshold=0.7
        )
        mock_knowledge_base.search_similar_patterns.assert_called_once_with(
            query="test query",
            limit=3,
            threshold=0.7
        )
    
    @pytest.mark.asyncio
    async def test_prefetch_with_issues(self, provider, mock_knowledge_base):
        """Test prefetch formats known issues correctly."""
        mock_knowledge_base.search_known_issues.return_value = [
            {
                "id": 1,
                "title": "Database Connection Error",
                "category": "database",
                "symptoms": ["connection timeout", "network error"],
                "solution": "Check database credentials and network connectivity",
                "similarity": 0.85
            }
        ]
        mock_knowledge_base.search_similar_patterns.return_value = []
        
        result = await provider.prefetch("database error")
        
        assert "Known Issues" in result
        assert "Database Connection Error" in result
        assert "similarity: 0.85" in result
        assert "database" in result
        assert "Check database credentials" in result
    
    @pytest.mark.asyncio
    async def test_prefetch_with_patterns(self, provider, mock_knowledge_base):
        """Test prefetch formats patterns correctly."""
        mock_knowledge_base.search_known_issues.return_value = []
        mock_knowledge_base.search_similar_patterns.return_value = [
            {
                "id": 1,
                "name": "Connection Timeout Pattern",
                "pattern": ".*connection.*timeout.*",
                "pattern_type": "error",
                "severity": 4,
                "description": "Network connection timeout",
                "similarity": 0.78
            }
        ]
        
        result = await provider.prefetch("connection timeout")
        
        assert "Relevant Patterns" in result
        assert "Connection Timeout Pattern" in result
        assert "similarity: 0.78" in result
        assert "error" in result
        assert "Severity: 4/5" in result
    
    @pytest.mark.asyncio
    async def test_prefetch_with_both(self, provider, mock_knowledge_base):
        """Test prefetch with both issues and patterns."""
        mock_knowledge_base.search_known_issues.return_value = [
            {
                "id": 1,
                "title": "Test Issue",
                "category": "test",
                "symptoms": ["symptom1"],
                "solution": "solution1",
                "similarity": 0.9
            }
        ]
        mock_knowledge_base.search_similar_patterns.return_value = [
            {
                "id": 1,
                "name": "Test Pattern",
                "pattern": "test.*",
                "pattern_type": "info",
                "severity": 2,
                "description": "Test pattern",
                "similarity": 0.8
            }
        ]
        
        result = await provider.prefetch("test query")
        
        assert "Known Issues" in result
        assert "Relevant Patterns" in result
        assert "Test Issue" in result
        assert "Test Pattern" in result
    
    @pytest.mark.asyncio
    async def test_prefetch_handles_errors(self, provider, mock_knowledge_base):
        """Test prefetch handles errors gracefully."""
        mock_knowledge_base.search_known_issues.side_effect = Exception("DB error")
        
        result = await provider.prefetch("test query")
        
        # Should return empty string on error
        assert result == ""
    
    @pytest.mark.asyncio
    async def test_prefetch_truncates_long_solutions(self, provider, mock_knowledge_base):
        """Test that long solutions are truncated."""
        long_solution = "x" * 300
        mock_knowledge_base.search_known_issues.return_value = [
            {
                "id": 1,
                "title": "Test Issue",
                "category": "test",
                "symptoms": [],
                "solution": long_solution,
                "similarity": 0.9
            }
        ]
        mock_knowledge_base.search_similar_patterns.return_value = []
        
        result = await provider.prefetch("test")
        
        # Solution should be truncated to 200 chars + "..."
        assert "..." in result
        assert len(result) < len(long_solution) + 100  # Much shorter than original
    
    @pytest.mark.asyncio
    async def test_sync_turn_no_error(self, provider):
        """Test sync_turn completes without error."""
        # Should not raise
        await provider.sync_turn("user message", "assistant response")
    
    @pytest.mark.asyncio
    async def test_sync_turn_detects_resolution(self, provider):
        """Test sync_turn detects resolution keywords."""
        # This should log but not raise
        await provider.sync_turn(
            "How do I fix this error?",
            "To resolve this issue, follow these steps..."
        )
    
    def test_on_turn_start(self, provider):
        """Test on_turn_start hook."""
        # Should not raise
        provider.on_turn_start(1, "test message")
    
    def test_on_session_end(self, provider):
        """Test on_session_end hook."""
        # Should not raise
        provider.on_session_end([{"role": "user", "content": "test"}])
    
    def test_on_pre_compress(self, provider):
        """Test on_pre_compress hook."""
        result = provider.on_pre_compress([{"role": "user", "content": "test"}])
        assert result == ""
    
    def test_is_resolution_positive(self, provider):
        """Test _is_resolution detects resolution keywords."""
        assert provider._is_resolution("This issue is now resolved")
        assert provider._is_resolution("I fixed the problem")
        assert provider._is_resolution("Here's the solution")
        assert provider._is_resolution("To fix this, do the following")
        assert provider._is_resolution("To resolve, run this command")
        assert provider._is_resolution("Follow these steps to fix")
        assert provider._is_resolution("You can fix this by...")
        assert provider._is_resolution("Here's how to resolve it")
    
    def test_is_resolution_negative(self, provider):
        """Test _is_resolution returns False for non-resolutions."""
        assert not provider._is_resolution("I don't know")
        assert not provider._is_resolution("What is the error?")
        assert not provider._is_resolution("Can you provide more details?")
        assert not provider._is_resolution("Let me investigate")
    
    def test_format_recall_empty(self, provider):
        """Test _format_recall with empty inputs."""
        result = provider._format_recall([], [])
        assert result == ""
    
    def test_format_recall_issues_only(self, provider):
        """Test _format_recall with only issues."""
        issues = [
            {
                "title": "Test Issue",
                "category": "test",
                "symptoms": ["s1", "s2"],
                "solution": "test solution",
                "similarity": 0.9
            }
        ]
        
        result = provider._format_recall(issues, [])
        
        assert "Known Issues" in result
        assert "Test Issue" in result
        assert "Relevant Patterns" not in result
    
    def test_format_recall_patterns_only(self, provider):
        """Test _format_recall with only patterns."""
        patterns = [
            {
                "name": "Test Pattern",
                "pattern_type": "error",
                "severity": 3,
                "description": "test desc",
                "similarity": 0.8
            }
        ]
        
        result = provider._format_recall([], patterns)
        
        assert "Relevant Patterns" in result
        assert "Test Pattern" in result
        assert "Known Issues" not in result


class TestBuiltinMemoryProviderIntegration:
    """Integration tests for BuiltinMemoryProvider."""
    
    @pytest.mark.asyncio
    async def test_prefetch_limits_results(self, provider, mock_knowledge_base):
        """Test that prefetch limits results to 3 each."""
        # Return more than 3 results
        mock_knowledge_base.search_known_issues.return_value = [
            {"id": i, "title": f"Issue {i}", "category": "test", 
             "symptoms": [], "solution": "sol", "similarity": 0.9}
            for i in range(5)
        ]
        mock_knowledge_base.search_similar_patterns.return_value = [
            {"id": i, "name": f"Pattern {i}", "pattern_type": "error",
             "severity": 3, "description": "desc", "similarity": 0.8}
            for i in range(5)
        ]
        
        result = await provider.prefetch("test")
        
        # Should call with limit=3
        mock_knowledge_base.search_known_issues.assert_called_once_with(
            query="test",
            limit=3,
            threshold=0.7
        )
        mock_knowledge_base.search_similar_patterns.assert_called_once_with(
            query="test",
            limit=3,
            threshold=0.7
        )
