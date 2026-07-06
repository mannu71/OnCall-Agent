"""Automatic large-tool-result offload to the session virtual filesystem.

Wraps each tool's coroutine so a result exceeding ``threshold`` chars is
stored in the session's VFS (``app.core.vfs``) and replaced with a short
handle + preview, instead of bloating every subsequent turn's context with
the full dump. Mirrors ``wrap_tools_with_output_cap``
(``app.harness.tool_permissions``): tools with no ``.coroutine`` attribute
(``MCPToolWrapper`` — only implements ``_arun``) are skipped, because those
already self-cap in ``_arun`` via ``settings.mcp_tool_output_max_chars``.

Only wired in when ``AgentSpec.filesystem`` is on (see
``app.harness.tool_assembler.add_extension_tools``), since offloading
requires a bound VFS session to write into.
"""
from __future__ import annotations

from typing import Any, List, Optional


def wrap_tools_with_offload(
    tools: List[Any], session_id: Optional[str], threshold: int,
) -> List[Any]:
    """Wrap each tool's coroutine so oversized results offload to VFS.

    Returns a new list; ``threshold <= 0`` or no ``session_id`` is a no-op
    (returns ``tools`` unchanged) so callers without a bound VFS session — or
    with offload disabled — pay zero cost.
    """
    if not threshold or threshold <= 0 or not session_id:
        return tools

    from app.harness.tool_permissions import _clone_with_coroutine

    wrapped: List[Any] = []
    for tool in tools:
        original = getattr(tool, "coroutine", None)
        if original is None:
            wrapped.append(tool)  # e.g. MCPToolWrapper — self-caps in _arun
            continue

        async def _offloaded(
            *_a, __orig=original, __name=getattr(tool, "name", "") or "", **kwargs
        ) -> Any:
            out = await __orig(*_a, **kwargs)
            if not isinstance(out, str) or len(out) <= threshold:
                return out
            from app.core.vfs.backend import vfs_offload_if_large

            return await vfs_offload_if_large(session_id, __name, out, threshold=threshold)

        wrapped.append(_clone_with_coroutine(tool, _offloaded))
    return wrapped
