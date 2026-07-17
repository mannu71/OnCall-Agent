"""Tool-inclusive chat history replay.

A follow-up chat question re-runs every tool from scratch unless prior
turns' ``tool_calls``/tool results are replayed alongside plain user/
assistant text. The full tool-inclusive trajectory for each turn is already
persisted per-execution (``app.services.trajectory_service`` ->
``executions.trajectory``); this module reconstructs a chat session's replay
history from those trajectories instead of relying only on the UI's
text-only ``history``.

The pure functions here operate on plain dicts — the same shape
``agent_runner.build_initial_messages`` already understands — and never
touch the DB; only :func:`rebuild_chat_history` is async and DB-backed.
Everything else is unit-testable without a database.

Fail-open by design: :func:`rebuild_chat_history` returns ``None`` on ANY
error or when the feature is disabled, and the caller falls back to today's
UI-supplied text-only history.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.harness.agent_runner import SYNTHETIC_NUDGE_PREFIXES

logger = logging.getLogger(__name__)


def extract_turn_segment(trajectory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the entries belonging to the LAST real user turn in *trajectory*.

    A saved trajectory can contain more than one user-authored turn once
    tool-inclusive replay itself ships: turn N+1's trajectory will include
    turn N's replayed tool messages. Segmenting on the FIRST tool call would
    grab the wrong (earlier) turn's activity, so this always anchors on the
    LAST qualifying user entry. It also skips synthetic recovery nudges
    injected mid-run as ``role="user"`` messages (recursion-limit forced
    synthesis, truncation continuation, see
    ``agent_runner.SYNTHETIC_NUDGE_PREFIXES``) — those are not turn
    boundaries.
    """
    if not trajectory:
        return []
    last_real_user_idx: Optional[int] = None
    for i, entry in enumerate(trajectory):
        if entry.get("role") != "user":
            continue
        content = entry.get("content") or ""
        if any(content.startswith(p) for p in SYNTHETIC_NUDGE_PREFIXES):
            continue
        last_real_user_idx = i
    if last_real_user_idx is None:
        return []
    segment = trajectory[last_real_user_idx + 1:]
    # Drop a trailing assistant entry with neither text nor tool_calls
    # (shouldn't occur, but keep the segment clean).
    return [
        entry for entry in segment
        if not (entry.get("role") == "assistant" and not entry.get("content") and not entry.get("tool_calls"))
    ]


def _drop_unmatched_tool_calls(out: List[Dict[str, Any]], unmatched_ids: set) -> None:
    """Strip *unmatched_ids* from the last assistant entry in *out*, in place.

    Drops the entry entirely if that leaves it with no ``tool_calls`` and no
    text (an assistant turn that ONLY called tools none of which resolved).
    """
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "assistant":
            tool_calls = out[i].get("tool_calls") or []
            kept = [tc for tc in tool_calls if tc.get("id") not in unmatched_ids]
            if kept:
                out[i]["tool_calls"] = kept
            else:
                out[i].pop("tool_calls", None)
                if not out[i].get("content"):
                    out.pop(i)
            return


def repair_tool_pairing(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop any ``tool_calls``/tool-result that isn't cleanly paired.

    Forward walk: every id in an assistant entry's ``tool_calls`` must be
    satisfied by a ``role="tool"`` entry (matching ``tool_call_id``) before
    the NEXT assistant entry appears. Unmatched ids are stripped from the
    assistant entry (dropped entirely if that leaves no text either); orphan
    tool entries (no matching call) are dropped. Bedrock-valid by
    construction — no tool_use without its tool_result, and vice versa.
    """
    out: List[Dict[str, Any]] = []
    pending_ids: set = set()
    for entry in entries:
        role = entry.get("role")
        if role == "assistant":
            if pending_ids and out:
                _drop_unmatched_tool_calls(out, pending_ids)
            tool_calls = entry.get("tool_calls") or []
            pending_ids = {
                tc.get("id") for tc in tool_calls if isinstance(tc, dict) and tc.get("id")
            }
            if entry.get("content") or tool_calls:
                out.append(dict(entry))
        elif role == "tool":
            tcid = entry.get("tool_call_id")
            if tcid and tcid in pending_ids:
                pending_ids.discard(tcid)
                out.append(dict(entry))
            # else: orphan tool entry (no matching pending call) — drop.
        else:
            out.append(dict(entry))
    if pending_ids and out:
        _drop_unmatched_tool_calls(out, pending_ids)
    return out


def estimate_entry_tokens(entries: List[Dict[str, Any]]) -> int:
    """Rough token estimate for trajectory-shaped dicts (chars/4 — consistent
    with ``app.core.context.compaction._msg_token_estimate``)."""
    total = 0
    for entry in entries:
        total += len(str(entry.get("content") or "")) // 4
        for tc in (entry.get("tool_calls") or []):
            if isinstance(tc, dict):
                total += len(str(tc)) // 4
    return total


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def build_tool_inclusive_history(
    chat_rows: List[Dict[str, Any]],
    executions: List[Dict[str, Any]],
    *,
    current_user_query: str,
    max_tool_tokens: int,
    max_tool_executions: int,
    logger_instance: Any = None,
) -> List[Dict[str, Any]]:
    """Merge ``chat_messages`` rows + trajectory-bearing executions into one
    tool-inclusive history list, ready for ``agent_runner.build_initial_messages``.

    Algorithm:
      1. Drop trailing user rows matching *current_user_query* — it's
         appended separately by ``build_initial_messages``; the row was
         persisted pre-run (see ``workflows.py``'s ``_persist_chat_message``).
      2. Link each assistant row to the execution that produced it: primary
         key is ``row.metadata["execution_id"]``; fallback for rows
         predating that field is timestamp bracketing (the last execution
         that started at/before the row's ``created_at`` — assistant rows
         are persisted AFTER their run completes). Ambiguous/unmatched rows
         get no tool segment (text-only degradation).
      3. Select tool segments newest-first while under budget; a segment
         that would exceed either cap stops selection entirely (older
         segments are dropped, never cherry-picked around).
      4. Emit in chat_rows order. Trajectory USER messages are never
         replayed (they're the augmented, KB-recall-laden, pseudonymized
         query — replaying them would duplicate what the next turn's own
         recall step prepends, and is also the replay-recursion hazard);
         the clean chat-row text always wins for user/assistant turns.
    """
    log = logger_instance or logger

    rows = list(chat_rows)
    q = (current_user_query or "").strip()
    while rows and rows[-1].get("role") == "user" and (rows[-1].get("content") or "").strip() == q:
        rows.pop()

    exec_by_id = {str(ex["id"]): ex for ex in executions if ex.get("id") is not None}
    sorted_execs = sorted(
        (ex for ex in executions if _parse_ts(ex.get("started_at")) is not None),
        key=lambda e: _parse_ts(e["started_at"]),
    )

    def _find_execution_for_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        meta = row.get("metadata") or {}
        eid = meta.get("execution_id")
        if eid is not None and str(eid) in exec_by_id:
            return exec_by_id[str(eid)]
        row_ts = _parse_ts(row.get("created_at"))
        if row_ts is None:
            return None
        candidate = None
        for ex in sorted_execs:
            if _parse_ts(ex["started_at"]) <= row_ts:
                candidate = ex
            else:
                break
        return candidate

    linked: List[tuple] = []
    for idx, row in enumerate(rows):
        if row.get("role") != "assistant":
            continue
        ex = _find_execution_for_row(row)
        if ex is not None:
            linked.append((idx, ex))

    selected_segments: Dict[int, List[Dict[str, Any]]] = {}
    total_tokens = 0
    count = 0
    for idx, ex in reversed(linked):
        if count >= max_tool_executions:
            break
        segment = repair_tool_pairing(extract_turn_segment(ex.get("trajectory") or []))
        # The trajectory's own trailing entry is that turn's final answer as
        # text — the chat row's REHYDRATED content is emitted for that below,
        # so drop the trajectory's copy here or the answer would repeat twice.
        if segment and segment[-1].get("role") == "assistant" and not segment[-1].get("tool_calls"):
            segment = segment[:-1]
        if not segment:
            continue
        seg_tokens = estimate_entry_tokens(segment)
        if total_tokens + seg_tokens > max_tool_tokens:
            break
        selected_segments[idx] = segment
        total_tokens += seg_tokens
        count += 1

    log.debug(
        "chat_history: replaying tool segments for %d/%d linked turn(s), ~%d tokens",
        count, len(linked), total_tokens,
    )

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        role = row.get("role")
        content = row.get("content") or ""
        if role == "system":
            if content:
                out.append({"role": "system", "content": content})
        elif role == "user":
            if content:
                out.append({"role": "user", "content": content})
        elif role == "assistant":
            if idx in selected_segments:
                out.extend(selected_segments[idx])
            if content:
                out.append({"role": "assistant", "content": content})
        # Unknown roles are dropped here too (build_initial_messages would
        # warn/drop them anyway).
    return out


async def rebuild_chat_history(
    session_id: str,
    *,
    current_user_query: str,
    logger_instance: Any = None,
) -> Optional[List[Dict[str, Any]]]:
    """Rebuild a chat session's history with prior turns' tool results
    included. Returns ``None`` on any error or when the feature is
    disabled — the caller falls back to the UI-supplied text-only history.

    Trajectory content is pseudonymized under a vault that's dropped at the
    end of the run that produced it (``app.core.privacy``) — replayed tool
    content may therefore carry stable but now-unresolvable placeholder
    tokens. Accepted: the model treats them as opaque identifiers, and this
    is no worse than the status quo (the UI's text history already replays
    each turn's REHYDRATED final answer verbatim).
    """
    log = logger_instance or logger
    from app.config import settings
    if not settings.chat_history_include_tools:
        return None
    try:
        from app.infrastructure.persistence import session_repository
        from app.infrastructure.persistence.execution_repository import ExecutionRepository

        chat_rows = await session_repository.get_messages(session_id)
        if not chat_rows:
            return None
        executions = await ExecutionRepository().list_completed_by_chat_session(session_id)
        return build_tool_inclusive_history(
            chat_rows, executions,
            current_user_query=current_user_query,
            max_tool_tokens=settings.chat_tool_history_max_tokens,
            max_tool_executions=settings.chat_tool_history_max_executions,
            logger_instance=log,
        )
    except Exception as exc:  # noqa: BLE001 — fail open to the UI's history
        log.warning(
            "chat_history: rebuild failed for session %s (%s); falling back "
            "to UI-supplied history", session_id, exc,
        )
        return None


__all__ = [
    "extract_turn_segment",
    "repair_tool_pairing",
    "estimate_entry_tokens",
    "build_tool_inclusive_history",
    "rebuild_chat_history",
]
