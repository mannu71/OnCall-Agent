"""getBodyFlow nodes.

Pipeline: ResolveHandle → ReadFile → SliceLines → BuildBodyResponse

Body handles are issued and resolved through the shared cache in
``app.crawler.handles`` (make_body_handle / resolve_body_handle).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100   # lines per page when slicing large bodies


# ─────────────────────────────────────────────────────────────────────────────
# Node 1: ResolveHandle
# ─────────────────────────────────────────────────────────────────────────────

class ResolveHandle(AsyncNode):
    """Resolve a body-handle string to (repo, file, line_start, line_end)."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"handle": shared["handle"]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.handles import resolve_body_handle as _resolve_body_handle

        handle = prep_res["handle"]
        meta = _resolve_body_handle(handle)
        if meta is None:
            raise ValueError(
                f"ResolveHandle: unknown or expired handle '{handle}'. "
                "Re-run find to get a fresh handle."
            )
        return {
            "repo": meta["repo"],
            "file": meta["file"],
            "line_start": int(meta["line_start"]),
            "line_end": int(meta["line_end"]),
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_handle_meta"] = exec_res
        _append_trace(shared, "ResolveHandle", 1, 0, 0, False)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 2: ReadFile
# ─────────────────────────────────────────────────────────────────────────────

class ReadFile(AsyncNode):
    """Open the resolved file and load all its lines into shared state."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        meta = shared["_handle_meta"]
        return {
            "repo": meta["repo"],
            "file": meta["file"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        repos_root = os.getenv("REPOS_BASE_PATH", "/tmp/indexed_repos")
        abs_path = os.path.join(repos_root, prep_res["repo"], prep_res["file"])

        def _read() -> List[str]:
            check_path(abs_path, repos_root)
            with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                return f.readlines()

        try:
            lines = await asyncio.to_thread(_read)
        except PathJailError as exc:
            raise ValueError(f"ReadFile: path jail violation — {exc}") from exc
        except FileNotFoundError:
            raise ValueError(
                f"ReadFile: '{prep_res['file']}' not found in repo '{prep_res['repo']}'."
            )

        return {"lines": lines}

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_all_lines"] = exec_res["lines"]
        _append_trace(shared, "ReadFile", len(exec_res["lines"]), 0, 0, False)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 3: SliceLines
# ─────────────────────────────────────────────────────────────────────────────

class SliceLines(AsyncNode):
    """Extract the requested page from the handle's line range."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        meta = shared["_handle_meta"]
        return {
            "lines": shared["_all_lines"],
            "line_start": meta["line_start"],
            "line_end": meta["line_end"],
            "page": max(1, int(shared.get("page", 1))),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        all_lines = prep_res["lines"]
        total_file_lines = len(all_lines)

        # Clamp to file bounds (1-based)
        start = max(1, min(prep_res["line_start"], total_file_lines))
        end = max(start, min(prep_res["line_end"], total_file_lines))

        # The slice that this handle covers (0-indexed for list access)
        handle_lines = all_lines[start - 1: end]

        # Paginate within the handle's window
        total_pages = max(1, (len(handle_lines) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(prep_res["page"], total_pages)
        page_start = (page - 1) * _PAGE_SIZE
        page_end = page_start + _PAGE_SIZE
        page_lines = handle_lines[page_start:page_end]

        # Build numbered output for readability
        first_line_no = start + page_start
        content_parts = []
        for i, line in enumerate(page_lines):
            content_parts.append(f"{first_line_no + i:>6} | {line.rstrip()}")
        content = "\n".join(content_parts)

        return {
            "content": content,
            "line_start": start + page_start,
            "line_end": start + page_start + len(page_lines) - 1,
            "page": page,
            "total_pages": total_pages,
            "handle_line_start": start,
            "handle_line_end": end,
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_slice"] = exec_res
        _append_trace(
            shared, "SliceLines",
            exec_res["line_end"] - exec_res["line_start"] + 1,
            0, 0, False,
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 4: BuildBodyResponse
# ─────────────────────────────────────────────────────────────────────────────

class BuildBodyResponse(AsyncNode):
    """Assemble the final body response."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "handle": shared["handle"],
            "meta": shared["_handle_meta"],
            "slice": shared["_slice"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        sl = prep_res["slice"]
        meta = prep_res["meta"]
        return {
            "handle": prep_res["handle"],
            "repo": meta["repo"],
            "file": meta["file"],
            "handle_line_start": sl["handle_line_start"],
            "handle_line_end": sl["handle_line_end"],
            "page": sl["page"],
            "total_pages": sl["total_pages"],
            "content": sl["content"],
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["response"] = exec_res
        _append_trace(shared, "BuildBodyResponse", 1, 0, 0, False)
        return None
