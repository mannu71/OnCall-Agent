"""LangChain tools exposing the virtual filesystem to the agent."""
from __future__ import annotations

import json
from typing import Any, List, Optional


def build_vfs_tools(session_id: Optional[str]) -> List[Any]:
    """Build fs_write / fs_read / fs_ls / fs_grep tools bound to a session.

    Calls through the async ``vfs_*`` facade in ``backend.py`` so these tools
    work with either the default in-memory backend or the opt-in Postgres
    backend (``settings.scratch_store_backend``) transparently.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from app.core.vfs.backend import (
        vfs_write, vfs_append, vfs_upsert, vfs_prune, vfs_read, vfs_ls, vfs_grep,
    )

    class _WriteIn(BaseModel):
        path: str = Field(description="Virtual file path, e.g. /notes/findings.md")
        content: str = Field(description="Text content to store")

    class _AppendIn(BaseModel):
        path: str = Field(description="Virtual file path, e.g. /milestones.txt")
        text: str = Field(description="Text block to append (creates the file if absent)")

    class _UpsertIn(BaseModel):
        path: str = Field(description="Virtual file path, e.g. /plan.txt")
        key: str = Field(
            description=(
                "The line's leading token, e.g. 'S2' for a line like "
                "'S2 [active] scope: ...'. Replaces the existing line starting with "
                "this token, or appends a new line if none matches — use this to "
                "update a status line without duplicating it."
            )
        )
        text: str = Field(description="The full replacement line")

    class _PruneIn(BaseModel):
        path: str = Field(description="Virtual file path to prune")
        keep_last_n: Optional[int] = Field(
            default=None, description="Keep only the last N lines (mutually exclusive with match)"
        )
        match: Optional[str] = Field(
            default=None, description="Regex; matching lines are dropped (mutually exclusive with keep_last_n)"
        )

    class _ReadIn(BaseModel):
        path: str = Field(description="Virtual file path to read")
        offset: int = Field(default=0, description="0-based line offset to start from")
        limit: Optional[int] = Field(default=None, description="Max lines to return")

    class _GrepIn(BaseModel):
        pattern: str = Field(description="Regex to search file contents")
        path_glob: Optional[str] = Field(default=None, description="Optional glob to limit files, e.g. /offload/*")

    async def _write(path: str, content: str) -> str:
        try:
            p = await vfs_write(session_id, path, content)
            return json.dumps({"ok": True, "path": p, "bytes": len(content.encode())})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    async def _append(path: str, text: str) -> str:
        try:
            p = await vfs_append(session_id, path, text)
            return json.dumps({"ok": True, "path": p})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    async def _upsert(path: str, key: str, text: str) -> str:
        try:
            p = await vfs_upsert(session_id, path, key, text)
            return json.dumps({"ok": True, "path": p})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    async def _prune(path: str, keep_last_n: Optional[int] = None, match: Optional[str] = None) -> str:
        try:
            p = await vfs_prune(session_id, path, keep_last_n=keep_last_n, match=match)
            return json.dumps({"ok": True, "path": p})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    async def _read(path: str, offset: int = 0, limit: Optional[int] = None) -> str:
        try:
            return await vfs_read(session_id, path, offset=offset, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    async def _ls() -> str:
        return json.dumps({"files": await vfs_ls(session_id)})

    async def _grep(pattern: str, path_glob: Optional[str] = None) -> str:
        try:
            return json.dumps({"hits": await vfs_grep(session_id, pattern, path_glob=path_glob)})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    return [
        StructuredTool.from_function(
            coroutine=_write, name="fs_write", args_schema=_WriteIn,
            description=(
                "Write text to a virtual scratch file (session-scoped, dropped at run end). "
                "Use to stash a large intermediate result or running notes so it does not "
                "clog your context; read back only the part you need later."
            ),
        ),
        StructuredTool.from_function(
            coroutine=_append, name="fs_append", args_schema=_AppendIn,
            description=(
                "Append a text block to a virtual file, creating it if absent. Use for "
                "append-only logs (e.g. /milestones.txt) — never overwrites existing content."
            ),
        ),
        StructuredTool.from_function(
            coroutine=_upsert, name="fs_upsert", args_schema=_UpsertIn,
            description=(
                "Replace the line starting with 'key' in a virtual file with 'text', or "
                "append it as a new line if no such line exists yet. Use for keyed status "
                "files (e.g. /plan.txt's 'S<n> [status] ...' entries) so re-recording the "
                "same key never creates duplicate stale entries."
            ),
        ),
        StructuredTool.from_function(
            coroutine=_prune, name="fs_prune", args_schema=_PruneIn,
            description=(
                "Drop stale lines from a virtual file — either the oldest lines beyond "
                "'keep_last_n', or every line matching the 'match' regex. Use to keep an "
                "agent-maintained state file (e.g. /context_summary.txt) within its size cap."
            ),
        ),
        StructuredTool.from_function(
            coroutine=_read, name="fs_read", args_schema=_ReadIn,
            description="Read a virtual file, optionally a line range (offset/limit).",
        ),
        StructuredTool.from_function(
            coroutine=_ls, name="fs_ls",
            description="List virtual files with their size and line count.",
        ),
        StructuredTool.from_function(
            coroutine=_grep, name="fs_grep", args_schema=_GrepIn,
            description="Regex-search virtual file contents; returns path/line/text hits.",
        ),
    ]
