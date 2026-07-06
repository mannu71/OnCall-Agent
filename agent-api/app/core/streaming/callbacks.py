"""
Enhanced streaming callbacks for real-time agent execution feedback.

This module provides:
- StreamCallback protocol with enhanced event handlers
- LoggingStreamCallback implementation with structured logging
- TokenUsageCallback: LangChain BaseCallbackHandler that captures exact token
  counts from every LLM call (streaming and non-streaming)
- Tool preview utilities for displaying tool call arguments
- TTY and non-TTY output mode support
"""

import logging
import sys
from typing import Any, Dict, List, Optional, Protocol

from app.core.error_classifier import ClassifiedError

logger = logging.getLogger(__name__)


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
        duration: Optional[float] = None,
        failed: bool = False
    ) -> None:
        """Called when tool execution completes.

        Args:
            tool_name: Name of the tool that completed
            result: Tool execution result (may be truncated for display)
            duration: Optional execution duration in seconds
            failed: Content-based failure classification (classify_tool_failure),
                    not just "the call completed without raising" — a tool can
                    return normally while its content reports an error (e.g. a
                    DB connection-refused message).
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
        duration: Optional[float] = None,
        failed: bool = False
    ) -> None:
        """Log tool result completion.

        Args:
            tool_name: Name of the tool that completed
            result: Tool execution result (truncated for logging)
            duration: Optional execution duration in seconds
            failed: Content-based failure classification from the caller
                    (classify_tool_failure). ORed with the local
                    _looks_like_error heuristic so a caller that hasn't been
                    updated to pass this yet keeps today's behavior.
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
            if failed or self._looks_like_error(result):
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


# ─────────────────────────────────────────────────────────────────────────────
# Token Usage Callback — exact per-execution token counting
# ─────────────────────────────────────────────────────────────────────────────

class TokenUsageCallback:
    """LangChain BaseCallbackHandler subclass that captures exact token counts.

    Wired into the LangGraph run_config["callbacks"] list so it fires on every
    LLM completion (streaming AND non-streaming).  The counts are read by
    ReactStrategy._execute_agent() after the agent loop finishes.

    Handles token key formats for all supported providers:
    - ChatBedrockConverse: llm_output["usage"]["inputTokens"/"outputTokens"]
    - ChatAnthropic:       llm_output["usage"]["input_tokens"/"output_tokens"]
    - ChatOpenAI:          llm_output["token_usage"]["prompt_tokens"/"completion_tokens"]
    - Fallback:            AIMessage.usage_metadata["input_tokens"/"output_tokens"]
    """

    # ── LangChain BaseCallbackHandler required attributes ────────────────────
    # Declaring these avoids subclassing BaseCallbackHandler while satisfying
    # every attribute access in langchain_core/callbacks/manager.py.
    run_inline:          bool = False
    raise_error:         bool = False
    ignore_llm:          bool = False
    ignore_chain:        bool = True   # only care about LLM events
    ignore_agent:        bool = True
    ignore_retriever:    bool = True
    ignore_chat_model:   bool = False
    ignore_retry:        bool = True
    ignore_custom_event: bool = True

    def __init__(self) -> None:
        self.input_tokens:  int = 0
        self.output_tokens: int = 0
        # Prompt-cache visibility (Bedrock/Anthropic). Non-zero cache_read on the
        # 2nd+ LLM call confirms the cached prefix is being reused.
        self.cache_read_tokens:     int = 0
        self.cache_creation_tokens: int = 0
        # ── Live SSE publish (optional; set by the caller after construction) ──
        # When all three are set, every LLM completion pushes a fresh
        # token_usage_delta so the UI's token counter and context-window bar
        # move DURING the run, not just once at the end. This is also what
        # makes the display resilient to a mid-run crash: whatever was burned
        # before a failure was already streamed on the prior LLM turn, so the
        # frontend never has to fall back to zero.
        self.execution_port: Any = None
        self.execution_id: Optional[str] = None
        self.model_name: Optional[str] = None

    # ── Catch-all: silently absorb any LangChain callback method we don't ────
    # implement (e.g. on_llm_new_token, on_chain_start, on_tool_start …).
    # LangChain calls these on every registered handler; without this, each
    # missing method raises AttributeError and floods the logs with WARNINGs.
    def __getattr__(self, name: str):  # noqa: ANN001
        async def _noop(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            pass
        return _noop

    # ── LangChain callback entry point ───────────────────────────────────────

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Called after every LLM completion (sync variant required by LC)."""
        self._accumulate(response)

    async def on_llm_end_async(self, response: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Async variant — LangChain calls whichever is present."""
        self._accumulate(response)
        await self._publish_live()

    async def _publish_live(self) -> None:
        """Best-effort live token_usage_delta after every LLM call.

        No-ops unless the caller wired execution_port/execution_id (see
        agent_runner.execute_agent). Mirrors the fields the end-of-run result
        already carries (strategy.py's context-window block) so the UI's live
        and final values use the same shape.
        """
        if self.execution_port is None or not self.execution_id:
            return
        try:
            totals: Dict[str, Any] = {
                "input_tokens":  self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens":  self.input_tokens + self.output_tokens,
                "cache_read_tokens":     self.cache_read_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
            }
            if self.model_name:
                from app.core.model_metadata import window_size_for_model
                window = window_size_for_model(self.model_name)
                used = self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens
                totals["context_window_size"] = window
                totals["context_used_tokens"] = used
                totals["context_used_pct"] = (
                    min(100, round(used / window * 100)) if window else 0
                )
            await self.execution_port.publish_token_usage(self.execution_id, totals)
        except Exception:  # noqa: BLE001 — telemetry must never break a run
            pass

    # ── Token extraction ─────────────────────────────────────────────────────

    def _accumulate(self, response: Any) -> None:
        """Extract and sum token counts from an LLMResult object."""
        llm_output: Dict[str, Any] = getattr(response, "llm_output", None) or {}

        inp, out = self._parse_llm_output(llm_output)

        # Fallback: iterate generations for AIMessage.usage_metadata
        if inp == 0 and out == 0:
            generations: List[Any] = getattr(response, "generations", []) or []
            for gen_list in generations:
                for gen in (gen_list if isinstance(gen_list, list) else [gen_list]):
                    msg = getattr(gen, "message", None)
                    usage = getattr(msg, "usage_metadata", None) or {}
                    inp  += usage.get("input_tokens",  0) or 0
                    out  += usage.get("output_tokens", 0) or 0

        self.input_tokens  += inp
        self.output_tokens += out

        # ── Prompt-cache visibility (best-effort, never fatal) ────────────────
        try:
            c_read, c_create = self._parse_cache_tokens(response, llm_output)
            self.cache_read_tokens     += c_read
            self.cache_creation_tokens += c_create
            if c_read or c_create:
                logger.info(
                    "Prompt cache: read=%d creation=%d (cumulative read=%d) — caching is engaging",
                    c_read, c_create, self.cache_read_tokens,
                )
        except Exception:  # noqa: BLE001 — metrics must never break the run
            pass

    @staticmethod
    def _parse_cache_tokens(response: Any, llm_output: Dict[str, Any]) -> tuple:
        """Extract (cache_read, cache_creation) tokens across provider shapes."""
        usage = (llm_output or {}).get("usage", {}) or {}
        # Bedrock Converse usage / Anthropic usage key variants.
        read = (
            usage.get("cacheReadInputTokens")
            or usage.get("cache_read_input_tokens")
            or 0
        )
        create = (
            usage.get("cacheWriteInputTokens")
            or usage.get("cache_creation_input_tokens")
            or 0
        )
        # LangChain standardized: usage_metadata.input_token_details.cache_read
        generations: List[Any] = getattr(response, "generations", []) or []
        for gen_list in generations:
            for gen in (gen_list if isinstance(gen_list, list) else [gen_list]):
                msg = getattr(gen, "message", None)
                details = (getattr(msg, "usage_metadata", None) or {}).get(
                    "input_token_details", {}
                ) or {}
                read = read or details.get("cache_read", 0) or 0
                create = create or details.get("cache_creation", 0) or 0
        return int(read or 0), int(create or 0)

    @staticmethod
    def _parse_llm_output(llm_output: Dict[str, Any]) -> tuple:
        """Return (input_tokens, output_tokens) from provider llm_output dict."""
        # ChatBedrockConverse: usage.inputTokens / outputTokens
        usage = llm_output.get("usage", {}) or {}
        if "inputTokens" in usage or "outputTokens" in usage:
            return (
                usage.get("inputTokens",  0) or 0,
                usage.get("outputTokens", 0) or 0,
            )

        # ChatAnthropic: usage.input_tokens / output_tokens
        if "input_tokens" in usage or "output_tokens" in usage:
            return (
                usage.get("input_tokens",  0) or 0,
                usage.get("output_tokens", 0) or 0,
            )

        # ChatOpenAI: token_usage.prompt_tokens / completion_tokens
        token_usage = llm_output.get("token_usage", {}) or {}
        if token_usage:
            return (
                token_usage.get("prompt_tokens",     0) or 0,
                token_usage.get("completion_tokens", 0) or 0,
            )

        return (0, 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
