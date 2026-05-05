"""
Example usage of streaming callbacks with ReactStrategy.

This module demonstrates how to use the enhanced streaming architecture
for real-time agent execution feedback.
"""

import asyncio
import logging
from typing import Optional

from app.core.streaming import LoggingStreamCallback, StreamCallback


# Example 1: Basic usage with LoggingStreamCallback
async def example_basic_logging():
    """Basic example using LoggingStreamCallback."""
    from app.workflow.strategies.react import ReactStrategy
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Create streaming callback
    callback = LoggingStreamCallback(
        logger=logger,
        execution_id="example-exec-123",
        tty_mode=True  # Use emoji indicators
    )
    
    # Create workflow and context
    workflow = {
        "id": "example-workflow",
        "nodes": [
            {"type": "agent", "data": {"instructions": "Investigate the issue"}},
            {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
        ]
    }
    
    context = {
        "user_query": "What caused the database connection error?",
        "execution_id": "example-exec-123",
        "stream_callback": callback,  # Enable streaming
    }
    
    # Execute with streaming
    strategy = ReactStrategy()
    result = await strategy.execute(workflow, context)
    
    print(f"\nExecution complete!")
    print(f"Final answer: {result['final_answer'][:100]}...")


# Example 2: Custom callback for metrics collection
class MetricsCallback:
    """Custom callback that collects execution metrics."""
    
    def __init__(self):
        self.tokens_generated = 0
        self.tools_called = []
        self.tool_durations = {}
        self.errors = []
        self.start_time = None
        self.end_time = None
    
    async def on_llm_token(self, token: str) -> None:
        """Count tokens."""
        self.tokens_generated += 1
    
    async def on_tool_call(
        self,
        tool_name: str,
        args: dict,
        preview: Optional[str] = None
    ) -> None:
        """Track tool calls."""
        self.tools_called.append({
            "tool": tool_name,
            "preview": preview,
        })
        print(f"🔧 Calling {tool_name}: {preview or ''}")
    
    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        duration: Optional[float] = None
    ) -> None:
        """Track tool durations."""
        if duration:
            self.tool_durations[tool_name] = duration
        print(f"✅ {tool_name} completed in {duration:.1f}s" if duration else f"✅ {tool_name} completed")
    
    async def on_error(
        self,
        error: str,
        classified: Optional["ClassifiedError"] = None
    ) -> None:
        """Track errors."""
        self.errors.append({
            "error": error,
            "classified": classified,
        })
        print(f"❌ Error: {error}")
    
    async def on_complete(
        self,
        output: str,
        metadata: Optional[dict] = None
    ) -> None:
        """Capture completion metadata."""
        print(f"\n✨ Execution complete!")
        print(f"   Tokens: {self.tokens_generated}")
        print(f"   Tools: {len(self.tools_called)}")
        print(f"   Errors: {len(self.errors)}")
        
        if self.tool_durations:
            print(f"\n   Tool Durations:")
            for tool, duration in self.tool_durations.items():
                print(f"     - {tool}: {duration:.1f}s")


async def example_metrics_collection():
    """Example using custom metrics callback."""
    from app.workflow.strategies.react import ReactStrategy
    
    # Create metrics callback
    callback = MetricsCallback()
    
    # Create workflow and context
    workflow = {
        "id": "metrics-workflow",
        "nodes": [
            {"type": "agent", "data": {"instructions": "Analyze the logs"}},
            {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
        ]
    }
    
    context = {
        "user_query": "Find errors in the application logs",
        "execution_id": "metrics-exec-456",
        "stream_callback": callback,
    }
    
    # Execute with metrics collection
    strategy = ReactStrategy()
    result = await strategy.execute(workflow, context)
    
    # Access collected metrics
    print(f"\n📊 Metrics Summary:")
    print(f"   Total tokens: {callback.tokens_generated}")
    print(f"   Tools used: {[t['tool'] for t in callback.tools_called]}")
    print(f"   Total errors: {len(callback.errors)}")


# Example 3: WebSocket streaming callback
class WebSocketCallback:
    """Stream events to WebSocket client."""
    
    def __init__(self, websocket):
        """Initialize with WebSocket connection.
        
        Args:
            websocket: WebSocket connection object (e.g., from FastAPI)
        """
        self.ws = websocket
    
    async def on_llm_token(self, token: str) -> None:
        """Stream tokens to client."""
        try:
            await self.ws.send_json({
                "type": "token",
                "data": {"token": token}
            })
        except Exception as e:
            print(f"WebSocket send error: {e}")
    
    async def on_tool_call(
        self,
        tool_name: str,
        args: dict,
        preview: Optional[str] = None
    ) -> None:
        """Stream tool call events."""
        try:
            await self.ws.send_json({
                "type": "tool_call",
                "data": {
                    "tool": tool_name,
                    "preview": preview,
                    "args_keys": list(args.keys()),
                }
            })
        except Exception as e:
            print(f"WebSocket send error: {e}")
    
    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        duration: Optional[float] = None
    ) -> None:
        """Stream tool results."""
        try:
            await self.ws.send_json({
                "type": "tool_result",
                "data": {
                    "tool": tool_name,
                    "result": result[:500],  # Truncate for transport
                    "duration": duration,
                }
            })
        except Exception as e:
            print(f"WebSocket send error: {e}")
    
    async def on_error(
        self,
        error: str,
        classified: Optional["ClassifiedError"] = None
    ) -> None:
        """Stream errors."""
        try:
            await self.ws.send_json({
                "type": "error",
                "data": {
                    "error": error,
                    "reason": classified.reason.value if classified else None,
                    "retryable": classified.retryable if classified else False,
                }
            })
        except Exception as e:
            print(f"WebSocket send error: {e}")
    
    async def on_complete(
        self,
        output: str,
        metadata: Optional[dict] = None
    ) -> None:
        """Stream completion."""
        try:
            await self.ws.send_json({
                "type": "complete",
                "data": {
                    "output": output,
                    "metadata": metadata,
                }
            })
        except Exception as e:
            print(f"WebSocket send error: {e}")


# Example 4: Composite callback (multiple callbacks)
class CompositeCallback:
    """Combine multiple callbacks into one."""
    
    def __init__(self, *callbacks: StreamCallback):
        """Initialize with multiple callbacks.
        
        Args:
            *callbacks: Variable number of StreamCallback instances
        """
        self.callbacks = callbacks
    
    async def on_llm_token(self, token: str) -> None:
        """Forward to all callbacks."""
        for callback in self.callbacks:
            try:
                await callback.on_llm_token(token)
            except Exception as e:
                print(f"Callback error: {e}")
    
    async def on_tool_call(
        self,
        tool_name: str,
        args: dict,
        preview: Optional[str] = None
    ) -> None:
        """Forward to all callbacks."""
        for callback in self.callbacks:
            try:
                await callback.on_tool_call(tool_name, args, preview)
            except Exception as e:
                print(f"Callback error: {e}")
    
    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        duration: Optional[float] = None
    ) -> None:
        """Forward to all callbacks."""
        for callback in self.callbacks:
            try:
                await callback.on_tool_result(tool_name, result, duration)
            except Exception as e:
                print(f"Callback error: {e}")
    
    async def on_error(
        self,
        error: str,
        classified: Optional["ClassifiedError"] = None
    ) -> None:
        """Forward to all callbacks."""
        for callback in self.callbacks:
            try:
                await callback.on_error(error, classified)
            except Exception as e:
                print(f"Callback error: {e}")
    
    async def on_complete(
        self,
        output: str,
        metadata: Optional[dict] = None
    ) -> None:
        """Forward to all callbacks."""
        for callback in self.callbacks:
            try:
                await callback.on_complete(output, metadata)
            except Exception as e:
                print(f"Callback error: {e}")


async def example_composite_callback():
    """Example using composite callback for multiple outputs."""
    from app.workflow.strategies.react import ReactStrategy
    
    # Create multiple callbacks
    logger = logging.getLogger(__name__)
    logging_callback = LoggingStreamCallback(logger=logger, execution_id="composite-exec")
    metrics_callback = MetricsCallback()
    
    # Combine them
    composite = CompositeCallback(logging_callback, metrics_callback)
    
    # Use composite callback
    workflow = {
        "id": "composite-workflow",
        "nodes": [
            {"type": "agent", "data": {"instructions": "Investigate"}},
            {"type": "llm", "data": {"model": "gpt-4", "provider": "openai"}},
        ]
    }
    
    context = {
        "user_query": "What's the issue?",
        "execution_id": "composite-exec",
        "stream_callback": composite,  # Use composite
    }
    
    strategy = ReactStrategy()
    result = await strategy.execute(workflow, context)
    
    # Both callbacks received events
    print(f"\n📊 Metrics: {metrics_callback.tokens_generated} tokens, {len(metrics_callback.tools_called)} tools")


# Main entry point
async def main():
    """Run all examples."""
    print("=" * 60)
    print("Streaming Callbacks Examples")
    print("=" * 60)
    
    print("\n1. Basic Logging Example")
    print("-" * 60)
    # await example_basic_logging()  # Uncomment to run
    
    print("\n2. Metrics Collection Example")
    print("-" * 60)
    # await example_metrics_collection()  # Uncomment to run
    
    print("\n3. Composite Callback Example")
    print("-" * 60)
    # await example_composite_callback()  # Uncomment to run
    
    print("\n✅ Examples complete!")


if __name__ == "__main__":
    asyncio.run(main())
