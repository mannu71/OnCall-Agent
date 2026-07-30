"""Helpers shared by the config mixins and by callers outside this package.

Kept separate from ``__init__`` so a mixin can import a constant without
importing the assembled ``Settings`` (which imports the mixins — a cycle).
"""
from __future__ import annotations

from typing import Any


def parse_env_bool(value: Any, *, default: bool = True) -> bool:
    """Parse common truthy/falsey environment string values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("false", "0", "no", "off")


# CSV of fnmatch patterns always stripped from delegated child tool sets. Shared
# with app.harness.subagent_factory so its no-settings fallback (DB-free tests)
# can't drift from this default. Children are investigate/read-only by default;
# the parent orchestrator owns all mutations — fs_write*/fs_append/fs_upsert/
# fs_prune together cover every VFS mutation (metamemory files included).
DEFAULT_DELEGATION_BLOCKED_TOOLS = (
    "delegate_*,apply_fix,edit_file,fs_write*,fs_append,fs_upsert,fs_prune,"
    "run_command,write_todos,send_*,wiki_*"
)
