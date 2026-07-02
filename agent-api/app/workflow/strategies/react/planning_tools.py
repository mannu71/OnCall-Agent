"""Planning / todo tools (write_todos + update_todo).

Promotes the prompt-only "write a markdown task list" guidance into real tools so
a multi-step agent maintains an explicit, inspectable plan: ``write_todos`` sets
the list, ``update_todo`` flips an item's status. The list is held in a
session-scoped store (same lifecycle as the VFS / privacy vault) and can be read
back by the executor to surface a live checklist over SSE.

Off by default: only assembled when an agent profile sets ``planning: true``
(``AgentSpec.planning``).
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

_VALID_STATUS = ("pending", "in_progress", "completed", "blocked")

# ── Session-scoped todo store ─────────────────────────────────────────────────
_store: Dict[str, List[Dict[str, Any]]] = {}
_lock = threading.Lock()


def get_todos(session_id: Optional[str]) -> List[Dict[str, Any]]:
    with _lock:
        return list(_store.get(session_id or "_default", []))


def drop_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    with _lock:
        _store.pop(session_id, None)


def _set_todos(session_id: Optional[str], items: List[Dict[str, Any]]) -> None:
    with _lock:
        _store[session_id or "_default"] = items


def build_planning_tools(session_id: Optional[str]) -> List[Any]:
    """Build write_todos / update_todo tools bound to a session."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class _WriteTodosIn(BaseModel):
        items: List[str] = Field(
            description="The ordered list of concrete steps you intend to take.")

    class _UpdateTodoIn(BaseModel):
        index: int = Field(description="0-based index of the todo to update")
        status: str = Field(
            description="New status: pending | in_progress | completed | blocked")

    def _write_todos(items: List[str]) -> str:
        todos = [
            {"index": i, "text": str(t), "status": "pending"}
            for i, t in enumerate(items or [])
        ]
        _set_todos(session_id, todos)
        return json.dumps({"ok": True, "count": len(todos), "todos": todos})

    def _update_todo(index: int, status: str) -> str:
        status = (status or "").strip().lower()
        if status not in _VALID_STATUS:
            return json.dumps({"ok": False, "error": f"status must be one of {_VALID_STATUS}"})
        todos = get_todos(session_id)
        if index < 0 or index >= len(todos):
            return json.dumps({"ok": False, "error": f"no todo at index {index}"})
        todos[index]["status"] = status
        _set_todos(session_id, todos)
        return json.dumps({"ok": True, "todos": todos})

    return [
        StructuredTool.from_function(
            func=_write_todos, name="write_todos", args_schema=_WriteTodosIn,
            description=(
                "Record an ordered plan for a multi-step task as a list of concrete steps. "
                "Call this FIRST on a genuinely multi-step task, then work the list top to "
                "bottom, marking progress with update_todo. Skip it for a single lookup."
            ),
        ),
        StructuredTool.from_function(
            func=_update_todo, name="update_todo", args_schema=_UpdateTodoIn,
            description=(
                "Update one plan item's status (pending|in_progress|completed|blocked). "
                "Only mark an item completed once tool evidence actually supports it."
            ),
        ),
    ]
