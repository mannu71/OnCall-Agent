"""Virtual filesystem — session-scoped scratch space for context offload.

Context-offload scratch space: instead of letting a large tool result bloat every
subsequent LLM turn, the agent can write it to a virtual file and later read back
just the part it needs. The
backend is pluggable; the default is a per-session in-memory store with the same
lifecycle as the privacy vault (bound at run start, dropped at run end) so nothing
persists beyond the run.

Public surface mirrors ``app.core.privacy``:
  * ``bind_session(session_id)`` / ``drop_session(session_id)`` (sync, memory-only)
  * ``get_backend(session_id)`` → the :class:`VFSBackend` for a session (memory-only)
  * ``build_vfs_tools(session_id)`` → fs_write / fs_read / fs_ls / fs_grep /
    fs_append / fs_upsert / fs_prune tools
  * ``offload_if_large(session_id, name, text)`` → store + return a handle preview
    (memory-only; not wired into the live tool pipeline today)
  * ``vfs_write`` / ``vfs_append`` / ``vfs_upsert`` / ``vfs_prune`` / ``vfs_read``
    / ``vfs_ls`` / ``vfs_grep`` / ``vfs_drop_session`` — async, backend-pluggable
    (``settings.scratch_store_backend``: "memory" default or "postgres"); what
    ``build_vfs_tools`` actually calls.

Off by default: tools are only assembled when an agent profile sets
``filesystem: true`` (``AgentSpec.filesystem``).
"""
from __future__ import annotations

from app.core.vfs.backend import (
    VFSBackend,
    bind_session,
    drop_session,
    get_backend,
    offload_if_large,
    vfs_offload_if_large,
    vfs_write,
    vfs_append,
    vfs_upsert,
    vfs_prune,
    vfs_read,
    vfs_ls,
    vfs_grep,
    vfs_drop_session,
)
from app.core.vfs.tools import build_vfs_tools

__all__ = [
    "VFSBackend",
    "bind_session",
    "drop_session",
    "get_backend",
    "offload_if_large",
    "vfs_offload_if_large",
    "build_vfs_tools",
    "vfs_write",
    "vfs_append",
    "vfs_upsert",
    "vfs_prune",
    "vfs_read",
    "vfs_ls",
    "vfs_grep",
    "vfs_drop_session",
]
