"""Wrap tool coroutines so their output is pseudonymized before re-entering the LLM.

A tool result is replayed in the message history on every subsequent ReAct
iteration, so any PII it surfaces would otherwise be sent to Bedrock repeatedly.
This wraps each tool that exposes a ``coroutine`` (crawler, CloudWatch, DB-schema
tools …) and pseudonymizes its string output via the active session vault.

MCP tools expose no ``coroutine`` (they self-handle output in ``_arun``) and are
pseudonymized at their own choke point in
``app.services.mcp_client_manager.sanitize_tool_content`` instead, so they are
skipped here — mirroring how :func:`wrap_tools_with_output_cap` treats them.
"""
from __future__ import annotations

from typing import Any, List

from . import is_enabled, pseudonymize_active


def _clone_with_coroutine(tool: Any, coroutine) -> Any:
    """Copy *tool* with a replaced async impl, preserving the model-facing schema."""
    from langchain_core.tools import StructuredTool
    return StructuredTool.from_function(
        coroutine=coroutine,
        name=tool.name,
        description=tool.description,
        args_schema=getattr(tool, "args_schema", None),
    )


def _pseudonymize_result(value: Any) -> Any:
    """Pseudonymize a tool result. Strings are scrubbed; other types pass through."""
    if isinstance(value, str):
        return pseudonymize_active(value)
    return value


def wrap_tools_with_pseudonymization(tools: List[Any]) -> List[Any]:
    """Return *tools* with each coroutine tool's string output pseudonymized.

    No-op when the feature is disabled, so the action space and prompt-cache
    prefix are unchanged. Applied after the permission/output-cap wraps so the
    tool schema stays identical.
    """
    if not is_enabled():
        return tools
    wrapped: List[Any] = []
    for tool in tools:
        original = getattr(tool, "coroutine", None)
        if original is None:
            wrapped.append(tool)  # MCP wrapper — scrubbed in sanitize_tool_content
            continue

        async def _scrubbed(*_a, __orig=original, **kwargs) -> Any:
            out = await __orig(*_a, **kwargs)
            return _pseudonymize_result(out)

        wrapped.append(_clone_with_coroutine(tool, _scrubbed))
    return wrapped
