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


class VFSBackend:
    """A bounded, in-memory file map for one session."""

    def __init__(self) -> None:
        self._files: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._offload_seq = 0

    def write(self, path: str, content: str) -> str:
        path = _norm(path)
        content = content if isinstance(content, str) else str(content)
        with self._lock:
            prospective = self._total_bytes() - len(self._files.get(path, "").encode()) \
                + len(content.encode())
            if path not in self._files and len(self._files) >= _MAX_FILES:
                raise ValueError(f"vfs: too many files (max {_MAX_FILES})")
            if prospective > _MAX_TOTAL_BYTES:
                raise ValueError(f"vfs: session size limit exceeded (max {_MAX_TOTAL_BYTES} bytes)")
            self._files[path] = content
        return path

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
        with self._lock:
            self._offload_seq += 1
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "tool"))
            return f"/offload/{safe}_{self._offload_seq}.txt"

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
