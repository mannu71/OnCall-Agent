"""
Enhanced streaming callbacks for real-time agent execution feedback.

This module provides:
- StreamCallback protocol with enhanced event handlers
- LoggingStreamCallback implementation with structured logging
- Tool preview utilities for displaying tool call arguments
- TTY and non-TTY output mode support
"""

import logging
import sys
from typing import Any, Dict, Optional, Protocol

from app.core.error_classifier import ClassifiedError


class StreamCallback(Protocol):
    """Enhanced streaming callback protocol for real-time agent execution feedback.
    
    This protocol defines handlers for various events during agent execution:
    - LLM token generation
    - Tool call execution with preview
    - Tool result delivery
    - Error handling with classification
    - Execution completion with metadata
    
    Implementations can provide custom behavior for each event type,
    such as logging, UI updates, or metrics collection.
    """
    
    async def on_llm_token(self, token: str) -> None:
        """Called for each LLM output token.
        
        Args:
            token: Single token from LLM output stream
        """
        ...
    
    async def on_tool_call(
        self,
        tool_name: str,
        args: dict,
        preview: Optional[str] = None
    ) -> None:
        """Called when tool execution starts.
        
        Args:
            tool_name: Name of the tool being called
            args: Tool arguments dictionary
            preview: Optional short preview of primary argument for display
                    (e.g., "npm test" for terminal, "src/main.py" for read_file)
        """
        ...
    
    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        duration: Optional[float] = None
    ) -> None:
        """Called when tool execution completes.
        
        Args:
            tool_name: Name of the tool that completed
            result: Tool execution result (may be truncated for display)
            duration: Optional execution duration in seconds
        """
        ...
    
    async def on_error(
        self,
        error: str,
        classified: Optional[ClassifiedError] = None
    ) -> None:
        """Called on execution error.
        
        Args:
            error: Error message string
            classified: Optional ClassifiedError with recovery hints and error taxonomy
        """
        ...
    
    async def on_complete(
        self,
        output: str,
        metadata: Optional[dict] = None
    ) -> None:
        """Called when execution completes successfully.
        
        Args:
            output: Final execution output
            metadata: Optional metadata dictionary with execution details
                     (e.g., token usage, duration, model info)
        """
        ...


class LoggingStreamCallback:
    """Default streaming callback implementation with structured logging.
    
    This callback logs all streaming events using Python's logging module
    with structured extra fields for execution tracking. It supports both
    TTY (terminal) and non-TTY (log file) output modes.
    
    Features:
    - Structured logging with execution_id tracking
    - Tool call previews for better visibility
    - Error classification logging
    - Execution metadata capture
    - TTY detection for output formatting
    
    Example:
        >>> logger = logging.getLogger(__name__)
        >>> callback = LoggingStreamCallback(logger, execution_id="exec-123")
        >>> await callback.on_tool_call("read_file", {"path": "main.py"}, "main.py")
    """
    
    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        execution_id: Optional[str] = None,
        tty_mode: Optional[bool] = None
    ):
        """Initialize logging stream callback.
        
        Args:
            logger: Logger instance to use (defaults to module logger)
            execution_id: Execution ID for log correlation
            tty_mode: Force TTY mode on/off (auto-detected if None)
        """
        self.logger = logger or logging.getLogger(__name__)
        self.execution_id = execution_id
        
        # Auto-detect TTY mode if not specified
        if tty_mode is None:
            self.tty_mode = sys.stdout.isatty()
        else:
            self.tty_mode = tty_mode
        
        self._token_count = 0
        self._tool_count = 0
    
    async def on_llm_token(self, token: str) -> None:
        """Log LLM token generation.
        
        In TTY mode, tokens are logged at DEBUG level to avoid spam.
        In non-TTY mode, only periodic summaries are logged.
        
        Args:
            token: Single token from LLM output stream
        """
        self._token_count += 1
        
        # Only log every 50 tokens to avoid spam
        if self._token_count % 50 == 0:
            self.logger.debug(
                "LLM streaming: %d tokens generated",
                self._token_count,
                extra={"execution_id": self.execution_id}
            )
    
    async def on_tool_call(
        self,
        tool_name: str,
        args: dict,
        preview: Optional[str] = None
    ) -> None:
        """Log tool call with preview.
        
        Args:
            tool_name: Name of the tool being called
            args: Tool arguments dictionary
            preview: Optional short preview of primary argument
        """
        self._tool_count += 1
        
        # Build preview if not provided
        if preview is None:
            preview = build_tool_preview(tool_name, args)
        
        # Format log message based on TTY mode
        if self.tty_mode:
            # TTY mode: more compact format
            if preview:
                msg = f"🔧 {tool_name}: {preview}"
            else:
                msg = f"🔧 {tool_name}"
        else:
            # Non-TTY mode: structured format
            if preview:
                msg = f"Tool call: {tool_name} ({preview})"
            else:
                msg = f"Tool call: {tool_name}"
        
        self.logger.info(
            msg,
            extra={
                "execution_id": self.execution_id,
                "tool_name": tool_name,
                "tool_args_keys": list(args.keys()) if args else [],
                "tool_preview": preview,
            }
        )
    
    async def on_tool_result(
        self,
        tool_name: str,
        result: str,
        duration: Optional[float] = None
    ) -> None:
        """Log tool result completion.
        
        Args:
            tool_name: Name of the tool that completed
            result: Tool execution result (truncated for logging)
            duration: Optional execution duration in seconds
        """
        # Truncate result for logging
        result_preview = result[:200] if result else ""
        if len(result) > 200:
            result_preview += "..."
        
        # Format duration if provided
        duration_str = f" ({duration:.1f}s)" if duration is not None else ""
        
        # Format log message based on TTY mode
        if self.tty_mode:
            # TTY mode: use emoji indicators
            if self._looks_like_error(result):
                emoji = "❌"
            else:
                emoji = "✅"
            msg = f"{emoji} {tool_name}{duration_str}"
        else:
            # Non-TTY mode: structured format
            msg = f"Tool result: {tool_name}{duration_str}"
        
        self.logger.info(
            msg,
            extra={
                "execution_id": self.execution_id,
                "tool_name": tool_name,
                "result_length": len(result),
                "duration": duration,
                "result_preview": result_preview,
            }
        )
    
    async def on_error(
        self,
        error: str,
        classified: Optional[ClassifiedError] = None
    ) -> None:
        """Log execution error with classification.
        
        Args:
            error: Error message string
            classified: Optional ClassifiedError with recovery hints
        """
        # Format error message based on classification
        if classified:
            reason = classified.reason.value if hasattr(classified.reason, 'value') else str(classified.reason)
            msg = f"Error ({reason}): {error}"
            
            extra = {
                "execution_id": self.execution_id,
                "error_reason": reason,
                "retryable": classified.retryable,
                "should_compress": classified.should_compress,
                "should_fallback": classified.should_fallback,
            }
        else:
            msg = f"Error: {error}"
            extra = {"execution_id": self.execution_id}
        
        self.logger.error(msg, extra=extra)
    
    async def on_complete(
        self,
        output: str,
        metadata: Optional[dict] = None
    ) -> None:
        """Log execution completion with metadata.
        
        Args:
            output: Final execution output
            metadata: Optional metadata dictionary
        """
        output_length = len(output)
        
        # Build completion message
        if metadata:
            token_usage = metadata.get("token_usage", {})
            duration = metadata.get("duration")
            
            msg_parts = [f"Execution complete: {output_length} chars"]
            
            if token_usage:
                total_tokens = token_usage.get("total_tokens")
                if total_tokens:
                    msg_parts.append(f"{total_tokens} tokens")
            
            if duration:
                msg_parts.append(f"{duration:.1f}s")
            
            msg = ", ".join(msg_parts)
        else:
            msg = f"Execution complete: {output_length} chars"
        
        extra = {
            "execution_id": self.execution_id,
            "output_length": output_length,
            "token_count": self._token_count,
            "tool_count": self._tool_count,
        }
        
        if metadata:
            extra["metadata"] = metadata
        
        self.logger.info(msg, extra=extra)
    
    @staticmethod
    def _looks_like_error(result: str) -> bool:
        """Check if tool result looks like an error.
        
        Args:
            result: Tool result string
            
        Returns:
            True if result appears to be an error
        """
        if not result:
            return False
        
        result_lower = result.lower()
        error_indicators = [
            "error:",
            "failed:",
            "exception:",
            "traceback",
            "fatal:",
        ]
        
        return any(indicator in result_lower for indicator in error_indicators)


def build_tool_preview(tool_name: str, args: dict, max_len: int = 40) -> Optional[str]:
    """Build short preview of tool call's primary argument.
    
    This function extracts the most relevant argument from a tool call
    and formats it as a short preview string for display purposes.
    
    Preview strategies by tool type:
    - File operations: show file path
    - Terminal/shell: show command
    - Search operations: show query
    - Database: show query or table name
    - Web operations: show URL
    
    Args:
        tool_name: Name of the tool being called
        args: Tool arguments dictionary
        max_len: Maximum length of preview string (default: 40)
        
    Returns:
        Short preview string, or None if no suitable argument found
        
    Examples:
        >>> build_tool_preview("read_file", {"path": "src/main.py"})
        'src/main.py'
        
        >>> build_tool_preview("terminal", {"command": "npm test"})
        'npm test'
        
        >>> build_tool_preview("web_search", {"query": "python async"})
        'python async'
    """
    if not args:
        return None
    
    tool_lower = tool_name.lower()
    
    # File operations: look for path, file, filename
    if any(x in tool_lower for x in ['file', 'read', 'write', 'edit']):
        for key in ['path', 'file', 'filename', 'file_path']:
            if key in args:
                value = str(args[key])
                return _truncate_preview(value, max_len)
    
    # Terminal/shell operations: look for command
    if any(x in tool_lower for x in ['terminal', 'shell', 'exec', 'run', 'command']):
        for key in ['command', 'cmd', 'script']:
            if key in args:
                value = str(args[key])
                return _truncate_preview(value, max_len)
    
    # Search operations: look for query
    if any(x in tool_lower for x in ['search', 'find', 'grep', 'query']):
        for key in ['query', 'search', 'pattern', 'term']:
            if key in args:
                value = str(args[key])
                return _truncate_preview(value, max_len)
    
    # Database operations: look for query or table
    if any(x in tool_lower for x in ['db', 'database', 'sql']):
        for key in ['query', 'sql', 'table', 'table_name']:
            if key in args:
                value = str(args[key])
                return _truncate_preview(value, max_len)
    
    # Web operations: look for URL
    if any(x in tool_lower for x in ['web', 'http', 'fetch', 'url']):
        for key in ['url', 'uri', 'endpoint']:
            if key in args:
                value = str(args[key])
                return _truncate_preview(value, max_len)
    
    # Fallback: use first string argument
    for key, value in args.items():
        if isinstance(value, str) and value:
            return _truncate_preview(value, max_len)
    
    return None


def _truncate_preview(text: str, max_len: int) -> str:
    """Truncate preview text to maximum length.
    
    Args:
        text: Text to truncate
        max_len: Maximum length
        
    Returns:
        Truncated text with ellipsis if needed
    """
    if len(text) <= max_len:
        return text
    
    return text[:max_len - 3] + "..."
