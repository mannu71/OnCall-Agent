"""Code-edit tools — let the agent APPLY a fix or IMPLEMENT a feature, not just
diagnose it.

Two surgical tools:
  * ``edit_file`` — replace one unique snippet in an existing file (str-replace).
  * ``create_file`` — write a genuinely NEW file (fails if it already exists).

Both are classified ``ask`` by the permission gate (``tool_permissions``), so an
operator must approve every write before it touches code, and the repo must be
mounted read-write for the write to succeed.

Safety:
  * Path-jailed to ``REPOS_BASE_PATH`` via ``check_path`` (no escaping the repo).
  * ``check_write_path`` blocks writes to sensitive files (.ssh/.aws/etc.).
  * ``edit_file`` requires ``old_string`` to be UNIQUE in the file, so the edit is
    unambiguous (mirrors Claude Code's str-replace edit contract).
  * ``create_file`` refuses to overwrite — modifying an existing file is
    ``edit_file``'s job, so the agent can't silently clobber.

Deliberately NOT added: a batched multi-edit / append tool. Repeated ``edit_file``
calls compose (each gets its own approval card, preserving per-change audit
granularity), an append is just ``edit_file`` against a unique tail anchor, and a
batched edit would weaken the single-unique-match contract and muddy the approval
card for no protocol gain.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, List

logger = logging.getLogger(__name__)


def build_edit_tools() -> List[Any]:
    """Return the code-edit StructuredTool(s)."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField
    from app.config import settings

    class _EditInput(BaseModel):
        repo: str = PydanticField(description="Repository name under REPOS_BASE_PATH.")
        file: str = PydanticField(description="Repository-relative path of the file to edit.")
        old_string: str = PydanticField(
            description="Exact existing text to replace — must appear EXACTLY ONCE in the file. "
                        "Copy it verbatim (read with crawler_get_body first), include enough "
                        "surrounding context to be unique.")
        new_string: str = PydanticField(description="Replacement text.")

    async def _edit_file(repo: str, file: str, old_string: str, new_string: str) -> str:
        import asyncio
        from app.core.security import check_path, PathJailError

        root = settings.repos_base_path
        abs_path = os.path.join(root, repo, file)

        def _do() -> dict:
            check_path(abs_path, root)
            try:
                from app.core.security import check_write_path
                check_write_path(abs_path)
            except ImportError:
                pass
            with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            count = content.count(old_string)
            if count == 0:
                return {"error": "old_string not found in file (copy it verbatim, with context)",
                        "file": file}
            if count > 1:
                return {"error": f"old_string is not unique ({count} matches) — add more surrounding "
                                 f"context so it matches exactly once", "file": file}
            updated = content.replace(old_string, new_string, 1)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(updated)
            return {"ok": True, "file": file, "replaced": 1, "new_size": len(updated)}

        try:
            res = await asyncio.to_thread(_do)
            if res.get("ok"):
                logger.info("edit_file: applied edit to %s/%s", repo, file)
            return json.dumps(res)
        except PathJailError as exc:
            return json.dumps({"error": f"path outside the repository jail: {exc}", "file": file})
        except FileNotFoundError:
            return json.dumps({"error": "file not found", "file": file})
        except PermissionError:
            return json.dumps({"error": "write denied — the repository is mounted read-only. "
                                        "Mount it read-write to apply fixes.", "file": file})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc), "file": file})

    class _CreateInput(BaseModel):
        repo: str = PydanticField(description="Repository name under REPOS_BASE_PATH.")
        file: str = PydanticField(
            description="Repository-relative path of the NEW file to create. "
                        "Missing parent directories are created.")
        content: str = PydanticField(description="Full text content of the new file.")

    async def _create_file(repo: str, file: str, content: str) -> str:
        import asyncio
        from app.core.security import check_path, PathJailError

        root = settings.repos_base_path
        abs_path = os.path.join(root, repo, file)

        def _do() -> dict:
            check_path(abs_path, root)
            try:
                from app.core.security import check_write_path
                check_write_path(abs_path)
            except ImportError:
                pass
            if os.path.exists(abs_path):
                return {"error": "file already exists — use edit_file to modify it", "file": file}
            os.makedirs(os.path.dirname(abs_path) or root, exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "file": file, "bytes": len(content)}

        try:
            res = await asyncio.to_thread(_do)
            if res.get("ok"):
                logger.info("create_file: created %s/%s (%d bytes)", repo, file, res["bytes"])
            return json.dumps(res)
        except PathJailError as exc:
            return json.dumps({"error": f"path outside the repository jail: {exc}", "file": file})
        except PermissionError:
            return json.dumps({"error": "write denied — the repository is mounted read-only. "
                                        "Mount it read-write to create files.", "file": file})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc), "file": file})

    edit_tool = StructuredTool.from_function(
        coroutine=_edit_file,
        name="edit_file",
        description=(
            "Apply a fix by replacing an exact snippet in a source file (surgical str-replace). "
            "WHEN TO USE: you have LOCATED the bug and want to apply the fix. First read the exact "
            "text with crawler_get_body, then pass old_string (must be unique in the file) and "
            "new_string. This MODIFIES code on disk and REQUIRES operator approval. WHEN NOT TO USE: "
            "for investigation — use the read-only crawler tools; to create a brand-new file — use "
            "create_file. After editing, tell the user to build/test (the agent cannot run the "
            "build itself)."
        ),
        args_schema=_EditInput,
    )
    create_tool = StructuredTool.from_function(
        coroutine=_create_file,
        name="create_file",
        description=(
            "Create a NEW source file with the given content. WHEN TO USE: implementing a feature "
            "that needs a genuinely new file (a new module, test, or migration). First READ a "
            "sibling file with crawler_get_body so the new file matches existing conventions. "
            "FAILS if the file already exists — to change an existing file use edit_file. This "
            "MODIFIES the repo on disk and REQUIRES operator approval. After creating, tell the "
            "user to build/test (the agent cannot run the build itself)."
        ),
        args_schema=_CreateInput,
    )
    return [edit_tool, create_tool]
