"""Planning / todo tools (write_todos + update_todo).

Promotes the prompt-only "write a markdown task list" guidance into real tools so
a multi-step agent maintains an explicit, inspectable plan: ``write_todos`` sets
the list, ``update_todo`` flips an item's status. The list is held in a
session-scoped store (same lifecycle as the VFS / privacy vault) and can be read
back by the executor to surface a live checklist over SSE.

Off by default: only assembled when an agent profile sets ``planning: true``
(``AgentSpec.planning``).

Backend is pluggable via ``settings.scratch_store_backend`` (default "memory" —
a process-local dict, unaffected by this setting existing at all). Set it to
"postgres" for horizontal scaling, where a run's tool calls can land on a
different replica than the one that wrote earlier todos.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

_VALID_STATUS = ("pending", "in_progress", "completed", "blocked")

# ── Session-scoped todo store (memory backend) ─────────────────────────────────
_store: Dict[str, List[Dict[str, Any]]] = {}
_lock = threading.Lock()


def _backend() -> str:
    try:
        from app.config import settings
        return getattr(settings, "scratch_store_backend", "memory")
    except Exception:  # noqa: BLE001
        return "memory"


def _evidence_required() -> bool:
    """Whether a todo can be marked completed only with cited evidence."""
    try:
        from app.config import settings
        return bool(getattr(settings, "todo_evidence_required", False))
    except Exception:  # noqa: BLE001
        return False


async def get_todos(session_id: Optional[str]) -> List[Dict[str, Any]]:
    sid = session_id or "_default"
    if _backend() == "postgres":
        from app.infrastructure.persistence import execution_scratch_repository
        value = await execution_scratch_repository.get(sid, "todos")
        return list(value) if isinstance(value, list) else []
    with _lock:
        return list(_store.get(sid, []))


async def drop_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    if _backend() == "postgres":
        from app.infrastructure.persistence import execution_scratch_repository
        await execution_scratch_repository.delete(session_id, "todos")
        return
    with _lock:
        _store.pop(session_id, None)


async def _set_todos(session_id: Optional[str], items: List[Dict[str, Any]]) -> None:
    sid = session_id or "_default"
    if _backend() == "postgres":
        from app.infrastructure.persistence import execution_scratch_repository
        await execution_scratch_repository.set(sid, "todos", items)
        return
    with _lock:
        _store[sid] = items


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
        evidence: Optional[str] = Field(
            default=None,
            description="Concrete proof this step is done — a tool reference, "
            "file:line, or evidence ID. Required to mark an item completed when "
            "the run enforces verified completion.",
        )

    async def _write_todos(items: List[str]) -> str:
        todos = [
            {"index": i, "text": str(t), "status": "pending"}
            for i, t in enumerate(items or [])
        ]
        await _set_todos(session_id, todos)
        return json.dumps({"ok": True, "count": len(todos), "todos": todos})

    async def _update_todo(index: int, status: str, evidence: Optional[str] = None) -> str:
        status = (status or "").strip().lower()
        if status not in _VALID_STATUS:
            return json.dumps({"ok": False, "error": f"status must be one of {_VALID_STATUS}"})
        todos = await get_todos(session_id)
        if index < 0 or index >= len(todos):
            return json.dumps({"ok": False, "error": f"no todo at index {index}"})
        evidence = (evidence or "").strip()
        # Verified completion (opt-in): an item can't be marked completed without
        # concrete evidence, so "I'm done" self-reports must cite proof. Off by
        # default (todo_evidence_required) — existing runs are unaffected.
        if status == "completed" and not evidence and _evidence_required():
            return json.dumps({
                "ok": False,
                "error": (
                    "Cannot mark this step completed without evidence. Re-call "
                    "update_todo with evidence=<tool ref / file:line / ID> proving "
                    "it is done, or set status=blocked if it cannot be completed."
                ),
            })
        todos[index]["status"] = status
        if evidence:
            todos[index]["evidence"] = evidence
        await _set_todos(session_id, todos)
        return json.dumps({"ok": True, "todos": todos})

    return [
        StructuredTool.from_function(
            coroutine=_write_todos, name="write_todos", args_schema=_WriteTodosIn,
            description=(
                "Record an ordered plan for a multi-step task as a list of concrete steps. "
                "Call this FIRST on a genuinely multi-step task, then work the list top to "
                "bottom, marking progress with update_todo. Skip it for a single lookup."
            ),
        ),
        StructuredTool.from_function(
            coroutine=_update_todo, name="update_todo", args_schema=_UpdateTodoIn,
            description=(
                "Update one plan item's status (pending|in_progress|completed|blocked). "
                "Only mark an item completed once tool evidence actually supports it, and "
                "pass that proof in `evidence` (a tool ref / file:line / evidence ID)."
            ),
        ),
    ]
