"""LangChain tools exposing the virtual filesystem to the agent."""
from __future__ import annotations

import json
from typing import Any, List, Optional


def build_vfs_tools(session_id: Optional[str]) -> List[Any]:
    """Build fs_write / fs_read / fs_ls / fs_grep tools bound to a session."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    from app.core.vfs.backend import get_backend

    class _WriteIn(BaseModel):
        path: str = Field(description="Virtual file path, e.g. /notes/findings.md")
        content: str = Field(description="Text content to store")

    class _ReadIn(BaseModel):
        path: str = Field(description="Virtual file path to read")
        offset: int = Field(default=0, description="0-based line offset to start from")
        limit: Optional[int] = Field(default=None, description="Max lines to return")

    class _GrepIn(BaseModel):
        pattern: str = Field(description="Regex to search file contents")
        path_glob: Optional[str] = Field(default=None, description="Optional glob to limit files, e.g. /offload/*")

    def _write(path: str, content: str) -> str:
        try:
            p = get_backend(session_id).write(path, content)
            return json.dumps({"ok": True, "path": p, "bytes": len(content.encode())})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    def _read(path: str, offset: int = 0, limit: Optional[int] = None) -> str:
        try:
            return get_backend(session_id).read(path, offset=offset, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    def _ls() -> str:
        return json.dumps({"files": get_backend(session_id).ls()})

    def _grep(pattern: str, path_glob: Optional[str] = None) -> str:
        try:
            return json.dumps({"hits": get_backend(session_id).grep(pattern, path_glob=path_glob)})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)})

    return [
        StructuredTool.from_function(
            func=_write, name="fs_write", args_schema=_WriteIn,
            description=(
                "Write text to a virtual scratch file (session-scoped, dropped at run end). "
                "Use to stash a large intermediate result or running notes so it does not "
                "clog your context; read back only the part you need later."
            ),
        ),
        StructuredTool.from_function(
            func=_read, name="fs_read", args_schema=_ReadIn,
            description="Read a virtual file, optionally a line range (offset/limit).",
        ),
        StructuredTool.from_function(
            func=_ls, name="fs_ls",
            description="List virtual files with their size and line count.",
        ),
        StructuredTool.from_function(
            func=_grep, name="fs_grep", args_schema=_GrepIn,
            description="Regex-search virtual file contents; returns path/line/text hits.",
        ),
    ]
