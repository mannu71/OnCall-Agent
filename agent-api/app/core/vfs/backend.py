"""In-memory, session-scoped virtual-filesystem backend.

Keeps a ``{path: content}`` map per session. Bounded so a runaway agent cannot
exhaust memory: a max number of files and a max total size per session, enforced
on write. The store is process-local and dropped when the run ends — there is no
durability guarantee (that is intentional; it is scratch space, not storage).
"""
from __future__ import annotations

import fnmatch
import logging
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Bounds (generous but finite). A single offloaded tool result is usually a few
# KB; these caps stop pathological loops from growing without limit.
_MAX_FILES = 256
_MAX_TOTAL_BYTES = 8 * 1024 * 1024  # 8 MB per session
_OFFLOAD_THRESHOLD = 6000           # chars; tool results larger than this offload

# Metamemory: context_summary.txt (app.harness.metamemory) is a compressed
# state tracker the agent maintains itself — hard-capped at ~500 tokens
# (~4 chars/token) so it stays cheap to inject on every turn. The agent must
# fs_prune it before writing more, rather than letting it grow unbounded like
# ordinary scratch files. Enforced once in write() since append()/upsert()
# both funnel through it.
_METAMEMORY_SUMMARY_PATH = "/context_summary.txt"
_METAMEMORY_SUMMARY_MAX_CHARS = 2000


class VFSBackend:
    """A bounded, in-memory file map for one session."""

    def __init__(self) -> None:
        self._files: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._offload_seq = 0

    def write(self, path: str, content: str) -> str:
        path = _norm(path)
        content = content if isinstance(content, str) else str(content)
        if path == _METAMEMORY_SUMMARY_PATH and len(content) > _METAMEMORY_SUMMARY_MAX_CHARS:
            raise ValueError(
                f"vfs: {_METAMEMORY_SUMMARY_PATH} exceeds the 500-token "
                f"(~{_METAMEMORY_SUMMARY_MAX_CHARS} char) cap — prune first"
            )
        with self._lock:
            prospective = self._total_bytes() - len(self._files.get(path, "").encode()) \
                + len(content.encode())
            if path not in self._files and len(self._files) >= _MAX_FILES:
                raise ValueError(f"vfs: too many files (max {_MAX_FILES})")
            if prospective > _MAX_TOTAL_BYTES:
                raise ValueError(f"vfs: session size limit exceeded (max {_MAX_TOTAL_BYTES} bytes)")
            self._files[path] = content
        return path

    def append(self, path: str, text: str) -> str:
        """Append text to a file, creating it if absent.

        A newline separates the new text from existing content unless the
        file is empty or already ends with one. Routes through write() so
        size caps (and the context_summary.txt token cap) still apply.
        """
        path = _norm(path)
        text = text if isinstance(text, str) else str(text)
        with self._lock:
            existing = self._files.get(path, "")
        sep = "" if not existing or existing.endswith("\n") else "\n"
        return self.write(path, existing + sep + text)

    def upsert(self, path: str, key: str, text: str) -> str:
        """Replace the line whose first whitespace-delimited token equals
        ``key`` with ``text``; append ``text`` as a new line if no such line
        exists yet.

        The dedup format agent-maintained state files rely on (e.g.
        ``plan.txt``'s ``S<n> [status] ...`` entries) — re-upserting the same
        key never accumulates duplicate stale entries. Routes through
        write() so size caps still apply.
        """
        path = _norm(path)
        text = text if isinstance(text, str) else str(text)
        key = (key or "").strip()
        if not key:
            raise ValueError("vfs: upsert requires a non-empty key")
        with self._lock:
            existing = self._files.get(path, "")
        lines = existing.splitlines() if existing else []
        replaced = False
        out_lines: List[str] = []
        for line in lines:
            stripped = line.strip()
            first_token = stripped.split(None, 1)[0] if stripped else ""
            if first_token == key and not replaced:
                out_lines.append(text)
                replaced = True
            else:
                out_lines.append(line)
        if not replaced:
            out_lines.append(text)
        return self.write(path, "\n".join(out_lines))

    def prune(
        self, path: str, *, keep_last_n: Optional[int] = None, match: Optional[str] = None,
    ) -> str:
        """Drop stale lines from a file — the agent pruning its own memory.

        Exactly one of ``keep_last_n`` (keep only the last N lines) or
        ``match`` (a regex; matching lines are dropped) must be given.
        No-op on a missing/empty file. Routes through write() so size caps
        still apply.
        """
        path = _norm(path)
        if keep_last_n is None and not match:
            raise ValueError("vfs: prune requires keep_last_n or match")
        with self._lock:
            existing = self._files.get(path, "")
        if not existing:
            return path
        lines = existing.splitlines()
        if keep_last_n is not None:
            lines = lines[-max(0, keep_last_n):]
        else:
            import re
            try:
                rx = re.compile(match)  # type: ignore[arg-type]
            except re.error as exc:
                raise ValueError(f"vfs: bad regex: {exc}")
            lines = [ln for ln in lines if not rx.search(ln)]
        return self.write(path, "\n".join(lines))

    def read(self, path: str, *, offset: int = 0, limit: Optional[int] = None) -> str:
        path = _norm(path)
        with self._lock:
            if path not in self._files:
                raise KeyError(f"vfs: no such file: {path}")
            content = self._files[path]
        if offset or limit is not None:
            lines = content.splitlines()
            end = (offset + limit) if limit is not None else len(lines)
            return "\n".join(lines[offset:end])
        return content

    def ls(self) -> List[Dict[str, int]]:
        with self._lock:
            return [
                {"path": p, "bytes": len(c.encode()), "lines": c.count("\n") + 1}
                for p, c in sorted(self._files.items())
            ]

    def grep(self, pattern: str, *, path_glob: Optional[str] = None, max_hits: int = 100) -> List[Dict[str, object]]:
        import re
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"vfs: bad regex: {exc}")
        hits: List[Dict[str, object]] = []
        with self._lock:
            items = list(self._files.items())
        for p, content in items:
            if path_glob and not fnmatch.fnmatch(p, path_glob):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": p, "line": i, "text": line[:300]})
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def next_offload_path(self, name: str) -> str:
        """Pick a fresh, collision-free ``/offload/<name>_<n>.txt`` path.

        Derives ``n`` from the count of existing ``/offload/*`` entries in
        ``self._files`` rather than the in-object ``_offload_seq`` counter
        alone — the Postgres-backed facade (see ``_load_pg_backend``)
        rehydrates a *fresh* ``VFSBackend`` on every call, so a counter-only
        scheme would restart at 1 each time and silently overwrite the
        previous offload for the same tool name.
        """
        with self._lock:
            self._offload_seq += 1
            existing_offloads = sum(1 for p in self._files if p.startswith("/offload/"))
            seq = max(self._offload_seq, existing_offloads + 1)
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "tool"))
            return f"/offload/{safe}_{seq}.txt"

    def _total_bytes(self) -> int:
        return sum(len(c.encode()) for c in self._files.values())


def _norm(path: str) -> str:
    p = (path or "").strip()
    if not p:
        raise ValueError("vfs: empty path")
    if not p.startswith("/"):
        p = "/" + p
    return p


# ── Session registry (mirrors privacy vault lifecycle) ────────────────────────
_backends: Dict[str, VFSBackend] = {}
_registry_lock = threading.Lock()


def bind_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    with _registry_lock:
        _backends.setdefault(session_id, VFSBackend())


def get_backend(session_id: Optional[str]) -> VFSBackend:
    """Return (creating if needed) the backend for a session."""
    sid = session_id or "_default"
    with _registry_lock:
        be = _backends.get(sid)
        if be is None:
            be = VFSBackend()
            _backends[sid] = be
        return be


def drop_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    with _registry_lock:
        _backends.pop(session_id, None)


def offload_if_large(
    session_id: Optional[str], name: str, text: str, *, threshold: int = _OFFLOAD_THRESHOLD
) -> str:
    """If ``text`` exceeds ``threshold`` chars, store it and return a handle+preview.

    Otherwise return ``text`` unchanged. Best-effort: any failure returns the
    original text so offload never breaks a tool call.

    Memory-backend only (not wired into the live tool-execution pipeline today —
    only the fs_write/fs_read/fs_ls/fs_grep tools below are; those honor
    ``scratch_store_backend``). Kept sync/in-memory-only since converting an
    unused utility to async would add risk with no production benefit.
    """
    if not isinstance(text, str) or len(text) <= threshold:
        return text
    try:
        be = get_backend(session_id)
        path = be.next_offload_path(name)
        be.write(path, text)
        preview = text[:1500]
        return (
            f"[offloaded {len(text)} chars to virtual file {path} — "
            f"read more with fs_read('{path}') or fs_grep]\n\n"
            f"{preview}\n…(truncated; full content in {path})"
        )
    except Exception as exc:  # noqa: BLE001 — offload must never break a tool call
        logger.debug("vfs offload skipped: %s", exc)
        return text


# ── Pluggable backend used by fs_write/fs_read/fs_ls/fs_grep ──────────────────
# Async facade so both the default in-memory path and the opt-in Postgres path
# (settings.scratch_store_backend) share one call signature. The memory branch
# does no real awaiting — it's just a normal sync dict operation wrapped in an
# async def, which is safe and standard.

def _backend_kind() -> str:
    try:
        from app.config import settings
        return getattr(settings, "scratch_store_backend", "memory")
    except Exception:  # noqa: BLE001
        return "memory"


# Per-session asyncio locks for the Postgres backend's load-modify-save
# facade calls. Each vfs_write/append/upsert/prune/offload_if_large call
# hydrates the ENTIRE {path: content} blob, mutates it in-process, and
# writes the whole blob back (see _load_pg_backend/_save_pg_backend) — with
# no lock, two concurrent mutations on the same session (e.g. parallel
# delegated children sharing a persisted session, or concurrent
# offload-wrapped tool calls) silently lose one writer's update. This lock
# only serializes callers within this process; it does not protect against
# a second worker process mutating the same row (the Postgres scratch
# backend is opt-in and single-worker in current deployments).
_pg_locks: Dict[str, "asyncio.Lock"] = {}
_pg_locks_guard: Optional["threading.Lock"] = None


def _pg_lock(session_id: Optional[str]) -> "asyncio.Lock":
    import asyncio as _asyncio
    global _pg_locks_guard
    if _pg_locks_guard is None:
        _pg_locks_guard = threading.Lock()
    sid = session_id or "_default"
    with _pg_locks_guard:
        lock = _pg_locks.get(sid)
        if lock is None:
            lock = _asyncio.Lock()
            _pg_locks[sid] = lock
        return lock


async def _load_pg_backend(session_id: Optional[str]) -> VFSBackend:
    """Hydrate a transient VFSBackend from the Postgres-stored {path: content} blob.

    Reuses VFSBackend's existing bounds-checking / grep / ls logic instead of
    re-implementing it against a second data model — the Postgres row is just
    an at-rest form of the same ``{path: content}`` mapping.
    """
    from app.infrastructure.persistence import execution_scratch_repository
    data = await execution_scratch_repository.get(session_id or "_default", "vfs")
    be = VFSBackend()
    if isinstance(data, dict):
        be._files = dict(data)
    return be


async def _save_pg_backend(session_id: Optional[str], be: VFSBackend) -> None:
    from app.infrastructure.persistence import execution_scratch_repository
    await execution_scratch_repository.set(session_id or "_default", "vfs", dict(be._files))


async def vfs_write(session_id: Optional[str], path: str, content: str) -> str:
    if _backend_kind() == "postgres":
        async with _pg_lock(session_id):
            be = await _load_pg_backend(session_id)
            p = be.write(path, content)
            await _save_pg_backend(session_id, be)
            return p
    return get_backend(session_id).write(path, content)


async def vfs_append(session_id: Optional[str], path: str, text: str) -> str:
    if _backend_kind() == "postgres":
        async with _pg_lock(session_id):
            be = await _load_pg_backend(session_id)
            p = be.append(path, text)
            await _save_pg_backend(session_id, be)
            return p
    return get_backend(session_id).append(path, text)


async def vfs_upsert(session_id: Optional[str], path: str, key: str, text: str) -> str:
    if _backend_kind() == "postgres":
        async with _pg_lock(session_id):
            be = await _load_pg_backend(session_id)
            p = be.upsert(path, key, text)
            await _save_pg_backend(session_id, be)
            return p
    return get_backend(session_id).upsert(path, key, text)


async def vfs_prune(
    session_id: Optional[str], path: str, *,
    keep_last_n: Optional[int] = None, match: Optional[str] = None,
) -> str:
    if _backend_kind() == "postgres":
        async with _pg_lock(session_id):
            be = await _load_pg_backend(session_id)
            p = be.prune(path, keep_last_n=keep_last_n, match=match)
            await _save_pg_backend(session_id, be)
            return p
    return get_backend(session_id).prune(path, keep_last_n=keep_last_n, match=match)


async def vfs_read(
    session_id: Optional[str], path: str, *, offset: int = 0, limit: Optional[int] = None
) -> str:
    if _backend_kind() == "postgres":
        be = await _load_pg_backend(session_id)
        return be.read(path, offset=offset, limit=limit)
    return get_backend(session_id).read(path, offset=offset, limit=limit)


async def vfs_ls(session_id: Optional[str]) -> List[Dict[str, int]]:
    if _backend_kind() == "postgres":
        be = await _load_pg_backend(session_id)
        return be.ls()
    return get_backend(session_id).ls()


async def vfs_grep(
    session_id: Optional[str], pattern: str, *, path_glob: Optional[str] = None, max_hits: int = 100
) -> List[Dict[str, object]]:
    if _backend_kind() == "postgres":
        be = await _load_pg_backend(session_id)
        return be.grep(pattern, path_glob=path_glob, max_hits=max_hits)
    return get_backend(session_id).grep(pattern, path_glob=path_glob, max_hits=max_hits)


async def vfs_drop_session(session_id: Optional[str]) -> None:
    """Drop a session's VFS data from whichever backend is active."""
    if not session_id:
        return
    if _backend_kind() == "postgres":
        from app.infrastructure.persistence import execution_scratch_repository
        await execution_scratch_repository.delete(session_id, "vfs")
    drop_session(session_id)  # also clear the in-memory registry entry, if any
    # Drop the session's mutation lock too, or the module-level registry
    # grows by one dead Lock per session for the life of the process.
    if _pg_locks_guard is not None:
        with _pg_locks_guard:
            _pg_locks.pop(session_id, None)


_OFFLOAD_PREVIEW_CHARS = 800


async def vfs_offload_if_large(
    session_id: Optional[str], name: str, text: str, *, threshold: int = _OFFLOAD_THRESHOLD
) -> str:
    """Backend-pluggable counterpart to :func:`offload_if_large`.

    Same behavior (store + return a handle+preview when ``text`` exceeds
    ``threshold`` chars, pass through unchanged otherwise, never raise), but
    honors ``settings.scratch_store_backend`` like ``vfs_write`` so an
    offloaded file is visible to ``fs_read``/``fs_grep`` regardless of which
    backend is active. This is what the live tool-execution pipeline calls
    (see ``app.harness.tool_offload``); ``offload_if_large`` remains the
    memory-only sync version used by simple/test callers.
    """
    if not isinstance(text, str) or len(text) <= threshold:
        return text
    try:
        if _backend_kind() == "postgres":
            async with _pg_lock(session_id):
                be = await _load_pg_backend(session_id)
                path = be.next_offload_path(name)
                be.write(path, text)
                await _save_pg_backend(session_id, be)
        else:
            be = get_backend(session_id)
            path = be.next_offload_path(name)
            be.write(path, text)
        preview = text[:_OFFLOAD_PREVIEW_CHARS]
        return (
            f"[offloaded {len(text)} chars to virtual file {path} — "
            f"read more with fs_read('{path}') or fs_grep]\n\n"
            f"{preview}\n…(truncated; full content in {path})"
        )
    except Exception as exc:  # noqa: BLE001 — offload must never break a tool call
        logger.debug("vfs offload skipped: %s", exc)
        return text
