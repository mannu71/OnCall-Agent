"""Metamemory — agent-maintained state files in the session VFS.

Three schema-lightweight system files, seeded once per run (idempotent —
skipped if a file already exists, so a resumed/persisted session keeps its
state):

  /plan.txt             high-level execution graph, upsert-keyed by step id
                         grammar: "S<n> [pending|active|done|blocked] scope: <text> needs: <ids|->"
  /milestones.txt       append-only outcomes and blockers
  /context_summary.txt  compressed state tracker, hard-capped at ~500 tokens
                         (VFSBackend enforces the cap on write — see
                         app.core.vfs.backend._METAMEMORY_SUMMARY_MAX_CHARS)

The agent maintains these itself via fs_upsert/fs_append/fs_write/fs_read/
fs_prune (app.core.vfs.tools) — this module only seeds the initial
skeletons and supplies the LOG/PLAN system-prompt discipline fragment. When
context_summary.txt is non-empty, it supersedes LLM-generated compaction
summaries (see app.harness.engine.compression) — an agent-maintained
summary beats a re-summarized one, and costs nothing extra to produce.

Off by default (settings.metamemory_enabled); requires the VFS session to
be bound already (AgentSpec.filesystem — nowhere to seed files otherwise).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

PLAN_PATH = "/plan.txt"
MILESTONES_PATH = "/milestones.txt"
SUMMARY_PATH = "/context_summary.txt"

# milestones.txt is append-only and uncapped at the VFS layer (unlike
# context_summary.txt's 500-token write-time cap) — read_context_block tail-
# truncates it, line-aligned, so the injected compaction block stays close to
# the ~1200-token budget from the plan (summary already ≤500 tokens; this
# leaves headroom for milestones without the pair growing unbounded).
_MILESTONES_INJECT_MAX_CHARS = 2400

_PLAN_TEMPLATE = "# PLAN v1 | objective: {objective}\n"
_MILESTONES_TEMPLATE = "# MILESTONES v1\n"
_SUMMARY_TEMPLATE = (
    "# SUMMARY v1 (<=500 tokens)\n"
    "OBJECTIVE: {objective}\n"
    "STATE: (not yet started)\n"
    "KEY_FACTS: (none yet)\n"
    "OPEN: (none yet)\n"
)

# Injected as its own system-prompt section (app.harness.agent_builder),
# right after the existing "# Scratch filesystem" section — same pattern as
# the planning/sandbox/verify sections there.
METAMEMORY_SECTION = """\
# Metamemory discipline
You have three self-maintained state files in your virtual filesystem:
- /plan.txt — your execution graph. Use fs_upsert(path="/plan.txt", key="S<n>", \
text="S<n> [pending|active|done|blocked] scope: <text> needs: <ids|->") to add or \
update a step without duplicating it.
- /milestones.txt — an append-only log. Use fs_append after each material step, \
e.g. "<what happened> evidence=<vfs path or tool ref>".
- /context_summary.txt — your compressed working memory, hard-capped at ~500 tokens. \
Keep OBJECTIVE/STATE/KEY_FACTS/OPEN current with fs_upsert or fs_write; if a write is \
rejected for exceeding the cap, fs_prune stale KEY_FACTS first.
Update these after any material step — they survive context compaction and are what a \
delegated subagent or a resumed session sees instead of raw history."""


def is_active(filesystem_enabled: bool) -> bool:
    """True when metamemory should be seeded/used for this run.

    Requires both the global flag and the run's own AgentSpec.filesystem
    (there is no VFS session to seed files into otherwise).
    """
    if not filesystem_enabled:
        return False
    try:
        from app.config import settings
        return bool(getattr(settings, "metamemory_enabled", False))
    except Exception:  # noqa: BLE001
        return False


async def seed_if_absent(session_id: Optional[str], objective: str) -> None:
    """Seed the three system files if they don't already exist (idempotent).

    Best-effort: seeding failures are logged, never raised — metamemory must
    never break a run that would otherwise proceed fine without it.
    """
    if not session_id:
        return
    from app.core.vfs.backend import vfs_read, vfs_write

    objective = (objective or "").strip()[:200] or "(unspecified)"
    seeds = (
        (PLAN_PATH, _PLAN_TEMPLATE.format(objective=objective)),
        (MILESTONES_PATH, _MILESTONES_TEMPLATE),
        (SUMMARY_PATH, _SUMMARY_TEMPLATE.format(objective=objective)),
    )
    for path, template in seeds:
        try:
            await vfs_read(session_id, path)
            continue  # already seeded (persisted session or a prior seed call)
        except KeyError:
            pass
        except Exception:  # noqa: BLE001 — an unreadable file: don't clobber it
            continue
        try:
            await vfs_write(session_id, path, template)
        except Exception as exc:  # noqa: BLE001 — seeding must never break a run
            logger.warning("metamemory: failed to seed %s (%s)", path, exc)


def _session_persistence_active() -> bool:
    try:
        from app.config import settings
        return (
            bool(getattr(settings, "vfs_session_persistence_enabled", False))
            and getattr(settings, "scratch_store_backend", "memory") == "postgres"
        )
    except Exception:  # noqa: BLE001
        return False


async def sync_session_persistence_in(
    execution_id: Optional[str], chat_session_id: Optional[str],
) -> None:
    """Hydrate this execution's VFS content from the chat session's persisted
    metamemory (if any), at run start — so a follow-up turn sees the same
    plan.txt/milestones.txt/context_summary.txt the prior turn left behind.

    Requires settings.vfs_session_persistence_enabled AND
    scratch_store_backend == "postgres" (the in-memory backend has no
    cross-run durability to hydrate from). Best-effort: any failure is a
    silent no-op — a fresh (unhydrated) session is a safe degradation.
    """
    if not (execution_id and chat_session_id) or not _session_persistence_active():
        return
    try:
        from app.infrastructure.persistence import execution_scratch_repository
        persisted = await execution_scratch_repository.get(
            f"session:{chat_session_id}", "vfs",
        )
        if isinstance(persisted, dict) and persisted:
            await execution_scratch_repository.set(execution_id, "vfs", persisted)
    except Exception as exc:  # noqa: BLE001 — hydration must never break a run
        logger.warning(
            "metamemory: session hydrate skipped for chat_session_id=%s (%s)",
            chat_session_id, exc,
        )


async def sync_session_persistence_out(
    execution_id: Optional[str], chat_session_id: Optional[str],
) -> None:
    """Persist this execution's current VFS content back to the chat
    session's key, at run end — BEFORE the execution-scoped VFS row is
    dropped — so the next turn's hydrate can see it.

    Same gating/best-effort behavior as :func:`sync_session_persistence_in`.
    """
    if not (execution_id and chat_session_id) or not _session_persistence_active():
        return
    try:
        from app.infrastructure.persistence import execution_scratch_repository
        current = await execution_scratch_repository.get(execution_id, "vfs")
        if isinstance(current, dict):
            await execution_scratch_repository.set(f"session:{chat_session_id}", "vfs", current)
    except Exception as exc:  # noqa: BLE001 — persistence must never break a run
        logger.warning(
            "metamemory: session persist skipped for chat_session_id=%s (%s)",
            chat_session_id, exc,
        )


def _tail_truncate_lines(text: str, max_chars: int) -> str:
    """Keep the last ``max_chars`` worth of *whole lines* from ``text``.

    Line-aligned so a truncated milestones.txt never starts mid-entry — an
    entry cut in half is more misleading to the model than a dropped one.
    """
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    kept: list = []
    budget = max_chars
    for line in reversed(lines):
        cost = len(line) + 1
        if budget - cost < 0 and kept:
            break
        kept.append(line)
        budget -= cost
    kept.reverse()
    return "…(older milestones truncated)\n" + "\n".join(kept)


async def read_context_block(session_id: Optional[str]) -> Optional[str]:
    """Return context_summary.txt + milestones.txt content for compaction/
    handoff injection, or None if metamemory isn't seeded / is empty.

    milestones.txt is append-only and uncapped at the VFS layer, so it is
    tail-truncated here to keep the injected block within the plan's
    ~1200-token budget (see :data:`_MILESTONES_INJECT_MAX_CHARS`).
    """
    if not session_id:
        return None
    from app.core.vfs.backend import vfs_read

    parts = []
    for path in (SUMMARY_PATH, MILESTONES_PATH):
        try:
            content = await vfs_read(session_id, path)
        except Exception:  # noqa: BLE001
            content = ""
        content = content.strip() if content else ""
        if not content:
            continue
        if path == MILESTONES_PATH:
            content = _tail_truncate_lines(content, _MILESTONES_INJECT_MAX_CHARS)
        parts.append(content)
    return "\n\n".join(parts) if parts else None


__all__ = [
    "PLAN_PATH", "MILESTONES_PATH", "SUMMARY_PATH", "METAMEMORY_SECTION",
    "is_active", "seed_if_absent", "read_context_block",
    "sync_session_persistence_in", "sync_session_persistence_out",
]
