"""Integration tests for context reference expansion.

Tests the full expansion pipeline including file reading, token limits,
and security restrictions.
"""
import asyncio
import tempfile
from pathlib import Path

import pytest

from app.core.context_references import (
    ContextReferenceResult,
    preprocess_context_references,
    preprocess_context_references_async,
)


class TestContextReferenceExpansion:
    """Integration tests for reference expansion."""
    
    def test_expand_file_reference(self, tmp_path):
        """Test expanding a file reference."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('Hello, world!')\n")
        
        message = f"Check @file:{test_file.name} for the implementation"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        assert result.expanded
        assert not result.blocked
        assert len(result.references) == 1
        assert result.references[0].kind == "file"
        assert "def hello():" in result.message
        assert "print('Hello, world!')" in result.message
        assert "--- Attached Context ---" in result.message
    
    def test_expand_file_reference_with_line_range(self, tmp_path):
        """Test expanding a file reference with line range."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        
        message = f"Check @file:{test_file.name}:2-4"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        assert result.expanded
        assert "line 2" in result.message
        assert "line 3" in result.message
        assert "line 4" in result.message
        assert "line 1" not in result.message
        assert "line 5" not in result.message
    
    def test_expand_folder_reference(self, tmp_path):
        """Test expanding a folder reference."""
        # Create test files
        (tmp_path / "file1.py").write_text("content1")
        (tmp_path / "file2.py").write_text("content2")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file3.py").write_text("content3")
        
        message = f"List @folder:{tmp_path.name}"
        result = preprocess_context_references(
            message,
            cwd=tmp_path.parent,
            context_length=100000,
        )
        
        assert result.expanded
        assert "file1.py" in result.message
        assert "file2.py" in result.message
        assert "subdir/" in result.message
    
    def test_soft_limit_warning(self, tmp_path):
        """Test that soft limit (25%) emits warning but allows expansion."""
        # Create a file with content that exceeds soft limit
        test_file = tmp_path / "large.txt"
        # Context length 10000, soft limit 2500 tokens, ~10000 chars
        content = "x" * 10000
        test_file.write_text(content)
        
        message = f"Check @file:{test_file.name}"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=10000,
        )
        
        # Should expand but with warning
        assert result.expanded
        assert not result.blocked
        assert len(result.warnings) > 0
        assert any("25% soft limit" in w for w in result.warnings)
        assert content in result.message
    
    def test_hard_limit_blocks(self, tmp_path):
        """Test that hard limit (50%) blocks expansion."""
        # Create a file with content that exceeds hard limit
        test_file = tmp_path / "huge.txt"
        # Context length 10000, hard limit 5000 tokens, ~20000 chars
        content = "x" * 20000
        test_file.write_text(content)
        
        message = f"Check @file:{test_file.name}"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=10000,
        )
        
        # Should be blocked
        assert not result.expanded
        assert result.blocked
        assert len(result.warnings) > 0
        assert any("50% hard limit" in w for w in result.warnings)
        # Original message should be returned
        assert result.message == message
    
    def test_multiple_references_cumulative_limit(self, tmp_path):
        """Test that multiple references count cumulatively."""
        # Create multiple files
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        # Each file has ~6000 chars (1500 tokens)
        # Total: 3000 tokens, exceeds soft limit (2500) but not hard (5000)
        file1.write_text("x" * 6000)
        file2.write_text("x" * 6000)
        
        message = f"Check @file:{file1.name} and @file:{file2.name}"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=10000,
        )
        
        # Should expand with warning
        assert result.expanded
        assert not result.blocked
        assert len(result.warnings) > 0
        assert any("25% soft limit" in w for w in result.warnings)
    
    def test_sensitive_file_blocked(self, tmp_path):
        """Test that sensitive files are blocked."""
        # Create a .env file
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=secret123")
        
        message = f"Check @file:{env_file.name}"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        # Should have warning about blocked file
        assert len(result.warnings) > 0
        assert any("sensitive credential file" in w for w in result.warnings)
        # Content should not be in message
        assert "SECRET_KEY" not in result.message
    
    def test_file_not_found(self, tmp_path):
        """Test handling of non-existent file."""
        message = "@file:nonexistent.txt"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        # Should have warning
        assert len(result.warnings) > 0
        assert any("file not found" in w for w in result.warnings)
    
    def test_binary_file_blocked(self, tmp_path):
        """Test that binary files are blocked."""
        # Create a binary file
        bin_file = tmp_path / "test.bin"
        bin_file.write_bytes(b"\x00\x01\x02\x03")
        
        message = f"Check @file:{bin_file.name}"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        # Should have warning
        assert len(result.warnings) > 0
        assert any("binary files are not supported" in w for w in result.warnings)
    
    def test_path_outside_allowed_root(self, tmp_path):
        """Test that paths outside allowed_root are blocked."""
        # Create a file in tmp_path
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        # Create a subdirectory as allowed_root
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        
        # Try to reference file outside allowed_root
        message = f"Check @file:../{test_file.name}"
        result = preprocess_context_references(
            message,
            cwd=subdir,
            context_length=100000,
            allowed_root=subdir,
        )
        
        # Should have warning
        assert len(result.warnings) > 0
        assert any("outside the allowed workspace" in w for w in result.warnings)
    
    def test_no_references(self, tmp_path):
        """Test message with no references."""
        message = "This is a normal message without any references"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        assert not result.expanded
        assert not result.blocked
        assert len(result.references) == 0
        assert len(result.warnings) == 0
        assert result.message == message
    
    @pytest.mark.asyncio
    async def test_async_expansion(self, tmp_path):
        """Test async expansion works correctly."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("async def hello():\n    return 'Hello'\n")
        
        message = f"Check @file:{test_file.name}"
        result = await preprocess_context_references_async(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        assert result.expanded
        assert "async def hello():" in result.message
    
    def test_reference_removal_from_message(self, tmp_path):
        """Test that reference tokens are removed from final message."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        
        message = f"Before @file:{test_file.name} after"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        # Reference token should be removed from the main message
        # but content should be in attached context
        assert f"@file:{test_file.name}" not in result.message.split("--- Attached Context ---")[0]
        assert "Before after" in result.message
        assert "content" in result.message


class TestGitReferences:
    """Tests for git reference expansion."""
    
    def test_git_diff_no_repo(self, tmp_path):
        """Test @diff in non-git directory."""
        message = "Show @diff"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        # Should have warning about git command failure
        assert len(result.warnings) > 0
        assert any("git" in w.lower() for w in result.warnings)
    
    def test_git_staged_no_repo(self, tmp_path):
        """Test @staged in non-git directory."""
        message = "Show @staged"
        result = preprocess_context_references(
            message,
            cwd=tmp_path,
            context_length=100000,
        )
        
        # Should have warning about git command failure
        assert len(result.warnings) > 0
        assert any("git" in w.lower() for w in result.warnings)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
