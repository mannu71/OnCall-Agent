"""Durable storage for large tool results — persist instead of truncating.

A tool result used to be cut to 2,000 chars on its way into
``executions.trajectory`` (``agent_runner._serialize_agent_result``). That store
is exactly what a follow-up chat turn replays from, so the cut destroyed the
evidence *at write time*: the read-side budget
(``chat_tool_history_max_tokens``) had nothing left to budget, and the next turn
re-ran the whole investigation.

Measured on session f84da222 (2026-07-23): turn 1's entire profile investigation
was ONE 2,000-char tool entry — the agent delegates its real work, so a single
delegation envelope carries everything and a single cut removes it all. Turn 2
("check logs") started blind and spent 191,457 tokens re-deriving it.

This module stores the full text and puts a POINTER in the trajectory instead.
Two properties follow:

* **Truncation becomes recoverable.** The agent can call ``read_tool_result``
  with the handle and get the original bytes back, so an evidence chain that
  needs the exact row (``submitted_job_id = null``) can still reach it.
* **Replay gets cheaper, not dearer.** A pointer + preview is ~300 chars against
  the 2,000 that used to sit inline, and the full text is fetched only when the
  model actually needs it.

Storage is the shared LangGraph KV store (``harness.runtime.get_store``) over the
already-provisioned ``store`` table — no new migration, and ``index=None`` keeps
it a plain key-value table with no embedding column.

Lifetime is the CHAT SESSION, not a clock: entries are written without a TTL and
dropped by :func:`purge_session` when the session is deleted. Runs with no chat
session are never replayed, so nothing is stored for them — that keeps
production rows out of the store for runs that could not benefit from them.

Every function is best-effort: a missing store, an unknown handle or a dead
connection degrades to today's behaviour and never breaks a run.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Namespace root. Full namespace is ``(_NS_ROOT, <chat_session_id>)`` so a
#: session's results are one contiguous prefix, cheap to purge and impossible to
#: read across sessions.
_NS_ROOT = "tool_results"

#: How much of the original to leave inline beside the pointer. Enough for the
#: model to judge relevance without fetching; small enough that replaying it
#: costs less than today's flat 2,000-char slice.
PREVIEW_CHARS = 600

#: Tools whose output is ALREADY a summary — storing a pointer to a summary just
#: adds a fetch round-trip for no saving. Matched as a name prefix.
_INLINE_TOOL_PREFIXES = ("delegate_",)


def namespace_for(chat_session_id: str) -> Tuple[str, str]:
    return (_NS_ROOT, str(chat_session_id))


def make_handle(execution_id: Any, tool_call_id: str) -> str:
    """Stable key for one tool result within a session."""
    return f"{execution_id}:{tool_call_id or 'unknown'}"


def should_store(tool_name: str, content: str, threshold: int) -> bool:
    """True when *content* is worth offloading rather than keeping inline.

    ``delegate_*`` output is exempt: a subagent envelope is already a distilled
    summary (bounded by ``delegation_output_max_chars``), so it is both small
    enough to inline and the thing the next turn most wants to read directly.
    """
    if not isinstance(content, str) or threshold <= 0:
        return False
    if len(content) <= threshold:
        return False
    name = tool_name or ""
    return not any(name.startswith(p) for p in _INLINE_TOOL_PREFIXES)


def render_pointer(handle: str, content: str, preview_chars: int = PREVIEW_CHARS) -> str:
    """The text that replaces a stored result in the trajectory.

    Deliberately explicit. A bare character slice is indistinguishable from a
    complete result — the model cannot tell it is holding a fragment and answers
    from it as though it were whole. This says what happened and how to undo it.
    """
    preview = content[:preview_chars]
    return (
        f"[stored tool result — {len(content)} chars; showing the first "
        f"{len(preview)}. Call read_tool_result(\"{handle}\") for the full "
        f"output.]\n{preview}"
    )


async def put(
    chat_session_id: str,
    execution_id: Any,
    tool_call_id: str,
    tool_name: str,
    content: str,
) -> Optional[str]:
    """Store *content* and return its handle, or ``None`` if it could not be stored.

    ``None`` means the caller must keep its existing behaviour (inline cap) —
    never that the content may be dropped.
    """
    if not chat_session_id:
        return None
    from app.harness.runtime import get_store

    store = get_store()
    if store is None:
        return None
    handle = make_handle(execution_id, tool_call_id)
    try:
        await store.aput(
            namespace_for(chat_session_id),
            handle,
            {"tool": tool_name or "", "content": content, "chars": len(content)},
        )
        return handle
    except Exception as exc:  # noqa: BLE001 — storage must never break a run
        logger.warning(
            "tool_result_store: failed to store %s for session %s (%s) — "
            "falling back to an inline cap",
            handle, chat_session_id, exc,
        )
        return None


async def get(chat_session_id: str, handle: str) -> Optional[Dict[str, Any]]:
    """Fetch a stored result, or ``None`` when absent/unavailable."""
    if not chat_session_id or not handle:
        return None
    from app.harness.runtime import get_store

    store = get_store()
    if store is None:
        return None
    try:
        item = await store.aget(namespace_for(chat_session_id), handle)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_result_store: read failed for %s (%s)", handle, exc)
        return None
    if item is None:
        return None
    value = getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def purge_session(chat_session_id: str) -> int:
    """Drop every stored result for a session. Returns how many were removed.

    This is what makes "lifetime = the chat session" true rather than aspirational:
    without it, entries written with no TTL would outlive the conversation that
    produced them, leaving production rows at rest indefinitely.
    """
    if not chat_session_id:
        return 0
    from app.harness.runtime import get_store

    store = get_store()
    if store is None:
        return 0
    ns = namespace_for(chat_session_id)
    removed = 0
    try:
        while True:
            items: List[Any] = await store.asearch(ns, limit=100)
            if not items:
                break
            for item in items:
                try:
                    await store.adelete(ns, item.key)
                    removed += 1
                except Exception:  # noqa: BLE001 — keep purging the rest
                    pass
            if len(items) < 100:
                break
    except Exception as exc:  # noqa: BLE001 — cleanup must never break a delete
        logger.warning(
            "tool_result_store: purge incomplete for session %s after %d "
            "entries (%s)", chat_session_id, removed, exc,
        )
    if removed:
        logger.info(
            "tool_result_store: purged %d stored result(s) for session %s",
            removed, chat_session_id,
        )
    return removed


async def offload_messages(
    messages: List[Dict[str, Any]],
    *,
    chat_session_id: Optional[str],
    execution_id: Any,
    threshold: int,
    inline_cap: int,
    tool_names_by_id: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Rewrite oversized tool entries in *messages* to pointers, in place-safe form.

    Returns a NEW list; entries are only replaced when their content was
    successfully stored. Anything not stored — no session, no store, a write
    failure, or a tool exempt from offload — falls back to ``inline_cap``, which
    is the pre-existing behaviour.
    """
    if not messages:
        return messages
    names = tool_names_by_id or {}
    out: List[Dict[str, Any]] = []
    stored = 0
    for entry in messages:
        if not isinstance(entry, dict) or entry.get("role") != "tool":
            out.append(entry)
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            out.append(entry)
            continue
        tool_call_id = entry.get("tool_call_id") or ""
        tool_name = names.get(tool_call_id, "")

        handle = None
        if chat_session_id and should_store(tool_name, content, threshold):
            handle = await put(
                chat_session_id, execution_id, tool_call_id, tool_name, content,
            )
        if handle:
            out.append({**entry, "content": render_pointer(handle, content)})
            stored += 1
        else:
            out.append(
                {**entry, "content": content[:inline_cap]}
                if inline_cap > 0 and len(content) > inline_cap
                else entry
            )
    if stored:
        logger.info(
            "tool_result_store: stored %d/%d tool result(s) for execution %s "
            "(session %s)", stored, len(messages), execution_id, chat_session_id,
        )
    return out


__all__ = [
    "PREVIEW_CHARS",
    "get",
    "make_handle",
    "namespace_for",
    "offload_messages",
    "purge_session",
    "put",
    "render_pointer",
    "should_store",
]
