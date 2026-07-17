# Streaming Architecture

This module provides enhanced streaming callbacks for real-time agent execution feedback.

## Overview

The streaming architecture enables real-time progress updates during agent execution through a protocol-based callback system. It supports:

- **LLM token streaming**: Real-time display of model output
- **Tool call previews**: Show what tools are being invoked with key arguments
- **Tool result feedback**: Display tool execution results with duration
- **Error classification**: Enhanced error reporting with recovery hints
- **Execution metadata**: Capture token usage, duration, and other metrics

## Components

### StreamCallback Protocol

The `StreamCallback` protocol defines the interface for streaming event handlers:

```python
from app.core.streaming import StreamCallback

class MyCallback:
    async def on_llm_token(self, token: str) -> None:
        """Called for each LLM output token."""
        print(token, end="", flush=True)
    
    async def on_tool_call(self, tool_name: str, args: dict, preview: str = None) -> None:
        """Called when tool execution starts."""
        print(f"\n🔧 {tool_name}: {preview}")
    
    async def on_tool_result(self, tool_name: str, result: str, duration: float = None) -> None:
        """Called when tool execution completes."""
        print(f"✅ {tool_name} ({duration:.1f}s)")
    
    async def on_error(self, error: str, classified: ClassifiedError = None) -> None:
        """Called on execution error."""
        print(f"❌ Error: {error}")
    
    async def on_complete(self, output: str, metadata: dict = None) -> None:
        """Called when execution completes."""
        print(f"\n✨ Complete: {len(output)} chars")
```

### LoggingStreamCallback

The default implementation that logs all events using Python's logging module:

```python
from app.core.streaming import LoggingStreamCallback
import logging

logger = logging.getLogger(__name__)
callback = LoggingStreamCallback(
    logger=logger,
    execution_id="exec-123",
    tty_mode=True  # Auto-detected if not specified
)

# Use with ReactStrategy
context = {
    "user_query": "Investigate the issue",
    "stream_callback": callback,
    "execution_id": "exec-123",
}
result = await strategy.execute(workflow, context)
```

### Tool Preview Generation

The `build_tool_preview()` function extracts the most relevant argument from tool calls:

```python
from app.core.streaming import build_tool_preview

# File operations
preview = build_tool_preview("read_file", {"path": "src/main.py"})
# Returns: "src/main.py"

# Terminal commands
preview = build_tool_preview("terminal", {"command": "npm test"})
# Returns: "npm test"

# Search queries
preview = build_tool_preview("web_search", {"query": "python async"})
# Returns: "python async"

# Database queries
preview = build_tool_preview("sql_query", {"query": "SELECT * FROM users"})
# Returns: "SELECT * FROM users"
```

## Usage Examples

### Basic Logging

```python
from app.core.streaming import LoggingStreamCallback
from app.workflow.strategies.react import ReactStrategy

# Create callback
callback = LoggingStreamCallback(execution_id="exec-123")

# Execute with streaming
strategy = ReactStrategy()
result = await strategy.execute(
    workflow=workflow_def,
    context={
        "user_query": "What caused the error?",
        "stream_callback": callback,
        "execution_id": "exec-123",
    }
)
```

### Custom Callback

```python
from app.core.streaming import StreamCallback
from typing import List

class MetricsCallback:
    """Collect execution metrics."""
    
    def __init__(self):
        self.tokens = 0
        self.tools_called = []
        self.errors = []
    
    async def on_llm_token(self, token: str) -> None:
        self.tokens += 1
    
    async def on_tool_call(self, tool_name: str, args: dict, preview=None) -> None:
        self.tools_called.append(tool_name)
    
    async def on_tool_result(self, tool_name: str, result: str, duration=None) -> None:
        pass
    
    async def on_error(self, error: str, classified=None) -> None:
        self.errors.append(error)
    
    async def on_complete(self, output: str, metadata=None) -> None:
        print(f"Metrics: {self.tokens} tokens, {len(self.tools_called)} tools")

# Use custom callback
callback = MetricsCallback()
result = await strategy.execute(workflow, {"stream_callback": callback})
print(f"Tools used: {callback.tools_called}")
```

### WebSocket Streaming

```python
from app.core.streaming import StreamCallback
import json

class WebSocketCallback:
    """Stream events to WebSocket client."""
    
    def __init__(self, websocket):
        self.ws = websocket
    
    async def on_llm_token(self, token: str) -> None:
        await self.ws.send_json({
            "type": "token",
            "data": {"token": token}
        })
    
    async def on_tool_call(self, tool_name: str, args: dict, preview=None) -> None:
        await self.ws.send_json({
            "type": "tool_call",
            "data": {
                "tool": tool_name,
                "preview": preview,
            }
        })
    
    async def on_tool_result(self, tool_name: str, result: str, duration=None) -> None:
        await self.ws.send_json({
            "type": "tool_result",
            "data": {
                "tool": tool_name,
                "result": result[:500],  # Truncate for transport
                "duration": duration,
            }
        })
    
    async def on_error(self, error: str, classified=None) -> None:
        await self.ws.send_json({
            "type": "error",
            "data": {"error": error}
        })
    
    async def on_complete(self, output: str, metadata=None) -> None:
        await self.ws.send_json({
            "type": "complete",
            "data": {
                "output": output,
                "metadata": metadata,
            }
        })
```

## TTY vs Non-TTY Mode

The `LoggingStreamCallback` automatically detects whether output is going to a terminal (TTY) or a log file (non-TTY) and adjusts formatting accordingly:

**TTY Mode** (terminal):
- Uses emoji indicators (🔧, ✅, ❌)
- More compact formatting
- Real-time progress display

**Non-TTY Mode** (log files):
- Structured text format
- Full context in each log line
- Machine-parseable output

You can force a specific mode:

```python
# Force TTY mode
callback = LoggingStreamCallback(tty_mode=True)

# Force non-TTY mode (for log files)
callback = LoggingStreamCallback(tty_mode=False)

# Auto-detect (default)
callback = LoggingStreamCallback()
```

## Error Classification Integration

The streaming callbacks integrate with the error classification system to provide rich error context:

```python
from app.core.streaming import LoggingStreamCallback
from app.core.llm.error_classifier import ClassifiedError, FailoverReason

callback = LoggingStreamCallback()

# Error with classification
classified = ClassifiedError(
    reason=FailoverReason.RATE_LIMIT,
    retryable=True,
    should_compress=False,
)

await callback.on_error("Rate limit exceeded", classified)
# Logs: "Error (rate_limit): Rate limit exceeded"
# Extra fields: retryable=True, should_compress=False
```

## Testing

The module includes comprehensive tests:

```bash
# Run all streaming tests
pytest app/core/streaming/test_callbacks.py -v

# Run specific test class
pytest app/core/streaming/test_callbacks.py::TestLoggingStreamCallback -v

# Run with coverage
pytest app/core/streaming/test_callbacks.py --cov=app.core.streaming
```

## Integration with ReactStrategy

The `ReactStrategy` already has built-in support for streaming callbacks. The StreamCallback protocol is defined in `react.py` and used throughout the execution flow:

```python
# In ReactStrategy.execute()
stream_callback = context.get("stream_callback")

# Passed to _execute_agent()
result = await self._execute_agent(
    agent, query, logger, execution_id, stream_callback
)

# Used during streaming execution
if stream_callback:
    await stream_callback.on_tool_call(tool_name, args, preview)
    await stream_callback.on_tool_result(tool_name, result, duration)
```

## Best Practices

1. **Always handle exceptions**: Callback methods should catch and log exceptions to avoid breaking the execution flow

2. **Keep callbacks lightweight**: Avoid heavy processing in callback methods as they run in the hot path

3. **Use structured logging**: Include execution_id and other context in log extra fields

4. **Truncate large outputs**: Tool results can be very large; truncate for display/logging

5. **Provide meaningful previews**: Use `build_tool_preview()` to show the most relevant argument

6. **Test both TTY modes**: Ensure your callback works in both terminal and log file contexts

## Future Enhancements

Potential improvements for future iterations:

- **Progress bars**: Visual progress indicators for long-running operations
- **Inline diffs**: Show file changes inline during streaming
- **Animated spinners**: Terminal spinners for tool execution
- **Rate limiting**: Throttle token streaming for very fast models
- **Buffering**: Buffer tokens for smoother display
- **Metrics collection**: Built-in metrics aggregation
- **Event replay**: Record and replay streaming events for debugging

## Requirements Satisfied

This implementation satisfies the following requirements from the design document:

- **Requirement 11.1**: StreamCallback protocol with enhanced event handlers
- **Requirement 11.2**: Tool call previews showing primary arguments
- **Requirement 11.3**: Error handling with ClassifiedError integration
- **Requirement 11.7**: TTY and non-TTY output mode support

## See Also

- [Error Classifier](../error_classifier.py) - Error classification system
- [ReactStrategy](../../workflow/strategies/react/strategy.py) - Agent execution strategy
- [Tool Registry](../tool_registry.py) - Tool management system
