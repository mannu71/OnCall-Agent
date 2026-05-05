"""Unit tests for enhanced StreamCallback protocol.

Tests verify that the StreamCallback protocol correctly defines the enhanced
interface with tool preview, classified errors, and completion metadata.
"""
import pytest
from typing import Optional

from app.core.streaming.callbacks import StreamCallback
from app.core.error_classifier import ClassifiedError, FailoverReason


class MockStreamCallback:
    """Mock implementation of StreamCallback for testing."""
    
    def __init__(self):
        self.llm_tokens = []
        self.tool_calls = []
        self.tool_results = []
        self.errors = []
        self.completions = []
    
    async def on_llm_token(self, token: str) -> None:
        self.llm_tokens.append(token)
    
    async def on_tool_call(self, tool_name: str, args: dict, preview: str = None) -> None:
        self.tool_calls.append({
            "tool_name": tool_name,
            "args": args,
            "preview": preview,
        })
    
    async def on_tool_result(self, tool_name: str, result: str) -> None:
        self.tool_results.append({
            "tool_name": tool_name,
            "result": result,
        })
    
    async def on_error(self, error: str, classified: Optional[ClassifiedError] = None) -> None:
        self.errors.append({
            "error": error,
            "classified": classified,
        })
    
    async def on_complete(self, output: str, metadata: dict = None) -> None:
        self.completions.append({
            "output": output,
            "metadata": metadata,
        })


@pytest.mark.asyncio
class TestStreamCallbackProtocol:
    """Test suite for StreamCallback protocol enhancements."""
    
    async def test_on_llm_token_basic(self):
        """Test on_llm_token receives tokens correctly."""
        callback = MockStreamCallback()
        
        await callback.on_llm_token("Hello")
        await callback.on_llm_token(" ")
        await callback.on_llm_token("world")
        
        assert len(callback.llm_tokens) == 3
        assert callback.llm_tokens == ["Hello", " ", "world"]
    
    async def test_on_tool_call_without_preview(self):
        """Test on_tool_call works without preview parameter."""
        callback = MockStreamCallback()
        
        await callback.on_tool_call("read_file", {"path": "test.py"})
        
        assert len(callback.tool_calls) == 1
        assert callback.tool_calls[0]["tool_name"] == "read_file"
        assert callback.tool_calls[0]["args"] == {"path": "test.py"}
        assert callback.tool_calls[0]["preview"] is None
    
    async def test_on_tool_call_with_preview(self):
        """Test on_tool_call includes preview when provided."""
        callback = MockStreamCallback()
        
        await callback.on_tool_call(
            "terminal",
            {"command": "npm test"},
            preview="npm test"
        )
        
        assert len(callback.tool_calls) == 1
        assert callback.tool_calls[0]["tool_name"] == "terminal"
        assert callback.tool_calls[0]["args"] == {"command": "npm test"}
        assert callback.tool_calls[0]["preview"] == "npm test"
    
    async def test_on_tool_call_preview_examples(self):
        """Test on_tool_call with various preview examples."""
        callback = MockStreamCallback()
        
        # Terminal command preview
        await callback.on_tool_call(
            "terminal",
            {"command": "npm run build"},
            preview="npm run build"
        )
        
        # File path preview
        await callback.on_tool_call(
            "read_file",
            {"path": "src/main.py", "start_line": 10},
            preview="src/main.py"
        )
        
        # Search query preview
        await callback.on_tool_call(
            "web_search",
            {"query": "python async best practices"},
            preview="python async best practices"
        )
        
        assert len(callback.tool_calls) == 3
        assert callback.tool_calls[0]["preview"] == "npm run build"
        assert callback.tool_calls[1]["preview"] == "src/main.py"
        assert callback.tool_calls[2]["preview"] == "python async best practices"
    
    async def test_on_tool_result_basic(self):
        """Test on_tool_result receives results correctly."""
        callback = MockStreamCallback()
        
        await callback.on_tool_result("read_file", "file contents here")
        
        assert len(callback.tool_results) == 1
        assert callback.tool_results[0]["tool_name"] == "read_file"
        assert callback.tool_results[0]["result"] == "file contents here"
    
    async def test_on_error_without_classification(self):
        """Test on_error works without classified parameter."""
        callback = MockStreamCallback()
        
        await callback.on_error("Connection timeout")
        
        assert len(callback.errors) == 1
        assert callback.errors[0]["error"] == "Connection timeout"
        assert callback.errors[0]["classified"] is None
    
    async def test_on_error_with_classification(self):
        """Test on_error includes ClassifiedError when provided."""
        callback = MockStreamCallback()
        
        classified = ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            status_code=429,
            message="Rate limit exceeded",
            retryable=True,
            should_fallback=True,
        )
        
        await callback.on_error("Rate limit exceeded", classified=classified)
        
        assert len(callback.errors) == 1
        assert callback.errors[0]["error"] == "Rate limit exceeded"
        assert callback.errors[0]["classified"] is not None
        assert callback.errors[0]["classified"].reason == FailoverReason.RATE_LIMIT
        assert callback.errors[0]["classified"].retryable is True
        assert callback.errors[0]["classified"].should_fallback is True
    
    async def test_on_error_with_context_overflow(self):
        """Test on_error with context overflow classification."""
        callback = MockStreamCallback()
        
        classified = ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            status_code=400,
            message="Context length exceeded",
            retryable=False,
            should_compress=True,
        )
        
        await callback.on_error("Context too large", classified=classified)
        
        assert len(callback.errors) == 1
        assert callback.errors[0]["classified"].reason == FailoverReason.CONTEXT_OVERFLOW
        assert callback.errors[0]["classified"].should_compress is True
    
    async def test_on_complete_without_metadata(self):
        """Test on_complete works without metadata parameter."""
        callback = MockStreamCallback()
        
        await callback.on_complete("Task completed successfully")
        
        assert len(callback.completions) == 1
        assert callback.completions[0]["output"] == "Task completed successfully"
        assert callback.completions[0]["metadata"] is None
    
    async def test_on_complete_with_metadata(self):
        """Test on_complete includes metadata when provided."""
        callback = MockStreamCallback()
        
        metadata = {
            "duration_ms": 1234,
            "tokens_used": 500,
            "model": "gpt-4",
            "tool_calls": 3,
        }
        
        await callback.on_complete("Analysis complete", metadata=metadata)
        
        assert len(callback.completions) == 1
        assert callback.completions[0]["output"] == "Analysis complete"
        assert callback.completions[0]["metadata"] == metadata
        assert callback.completions[0]["metadata"]["duration_ms"] == 1234
        assert callback.completions[0]["metadata"]["tokens_used"] == 500
    
    async def test_on_complete_with_various_metadata(self):
        """Test on_complete with various metadata examples."""
        callback = MockStreamCallback()
        
        # Execution metadata
        await callback.on_complete("Done", metadata={
            "execution_id": "exec-123",
            "duration_ms": 5000,
            "tokens": {"prompt": 100, "completion": 200},
        })
        
        # Performance metadata
        await callback.on_complete("Done", metadata={
            "cache_hit": True,
            "compression_applied": False,
            "retry_count": 0,
        })
        
        assert len(callback.completions) == 2
        assert "execution_id" in callback.completions[0]["metadata"]
        assert "cache_hit" in callback.completions[1]["metadata"]
    
    async def test_full_workflow_sequence(self):
        """Test complete workflow with all callback methods."""
        callback = MockStreamCallback()
        
        # LLM generates tokens
        await callback.on_llm_token("I")
        await callback.on_llm_token(" will")
        await callback.on_llm_token(" help")
        
        # Tool call with preview
        await callback.on_tool_call(
            "read_file",
            {"path": "config.json"},
            preview="config.json"
        )
        
        # Tool result
        await callback.on_tool_result("read_file", '{"key": "value"}')
        
        # More LLM tokens
        await callback.on_llm_token("Based")
        await callback.on_llm_token(" on")
        
        # Completion with metadata
        await callback.on_complete(
            "Analysis complete",
            metadata={"tokens_used": 150}
        )
        
        # Verify sequence
        assert len(callback.llm_tokens) == 5
        assert len(callback.tool_calls) == 1
        assert len(callback.tool_results) == 1
        assert len(callback.completions) == 1
        assert len(callback.errors) == 0
    
    async def test_error_recovery_workflow(self):
        """Test workflow with error and recovery."""
        callback = MockStreamCallback()
        
        # Initial attempt
        await callback.on_tool_call("api_call", {"endpoint": "/data"})
        
        # Error with classification
        classified = ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            retryable=True,
            should_fallback=True,
        )
        await callback.on_error("Rate limited", classified=classified)
        
        # Retry after backoff
        await callback.on_tool_call("api_call", {"endpoint": "/data"})
        await callback.on_tool_result("api_call", "success")
        
        # Complete
        await callback.on_complete("Done", metadata={"retry_count": 1})
        
        assert len(callback.tool_calls) == 2
        assert len(callback.errors) == 1
        assert callback.errors[0]["classified"].retryable is True
        assert callback.completions[0]["metadata"]["retry_count"] == 1


@pytest.mark.asyncio
class TestStreamCallbackBackwardCompatibility:
    """Test backward compatibility with existing code."""
    
    async def test_on_tool_call_backward_compatible(self):
        """Test on_tool_call works with old signature (no preview)."""
        callback = MockStreamCallback()
        
        # Old code that doesn't pass preview
        await callback.on_tool_call("some_tool", {"arg": "value"})
        
        assert len(callback.tool_calls) == 1
        assert callback.tool_calls[0]["preview"] is None
    
    async def test_on_error_backward_compatible(self):
        """Test on_error works with old signature (no classified)."""
        callback = MockStreamCallback()
        
        # Old code that doesn't pass classified
        await callback.on_error("Some error")
        
        assert len(callback.errors) == 1
        assert callback.errors[0]["classified"] is None
    
    async def test_on_complete_backward_compatible(self):
        """Test on_complete works with old signature (no metadata)."""
        callback = MockStreamCallback()
        
        # Old code that doesn't pass metadata
        await callback.on_complete("Done")
        
        assert len(callback.completions) == 1
        assert callback.completions[0]["metadata"] is None
