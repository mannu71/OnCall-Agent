"""Reversible PII pseudonymization for the cloud (Bedrock) boundary.

Public surface:
  * :func:`get_vault` — fetch (or lazily create) the per-session vault.
  * :func:`pseudonymize` / :func:`rehydrate` — convenience wrappers keyed by
    ``session_id``.
  * :func:`redaction_summary` — UI-safe summary (counts/placeholders/previews,
    never raw values).
  * :func:`is_enabled` — honour the ``pii_pseudonymization_enabled`` flag.

Vaults are held in a process-local registry keyed by ``session_id``. They hold
raw PII only in memory for the request/session lifetime and are dropped via
:func:`drop_vault` when a run finishes.
"""
from __future__ import annotations

from contextvars import ContextVar
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from .classifier import ALL_ENTITY_TYPES, Span, detect
from .vault import PseudonymVault

__all__ = [
    "PseudonymVault",
    "Span",
    "detect",
    "get_vault",
    "drop_vault",
    "pseudonymize",
    "rehydrate",
    "rehydrate_obj",
    "redaction_summary",
    "is_enabled",
    "bind_session",
    "active_session",
    "pseudonymize_active",
]

_VAULTS: Dict[str, PseudonymVault] = {}
_LOCK = Lock()

# The session whose vault should pseudonymize tool output produced deep inside
# the agent loop (MCP/crawler/CloudWatch), where threading a session_id through
# every call site is impractical. The strategy binds this once per run.
_ACTIVE_SESSION: ContextVar[Optional[str]] = ContextVar("privacy_active_session", default=None)


def bind_session(session_id: Optional[str]) -> None:
    """Bind *session_id* as the active pseudonymization session for this context."""
    _ACTIVE_SESSION.set(session_id)


def active_session() -> Optional[str]:
    """Return the session bound via :func:`bind_session`, if any."""
    return _ACTIVE_SESSION.get()


def pseudonymize_active(text: str) -> str:
    """Pseudonymize *text* with the active session's vault. No-op when unbound/off."""
    if not text or not is_enabled():
        return text
    return get_vault(_ACTIVE_SESSION.get()).pseudonymize(text)


def _configured_entity_types() -> Tuple[str, ...]:
    try:
        from app.config import settings
        configured = tuple(getattr(settings, "pii_entity_types", None) or ())
        return configured or ALL_ENTITY_TYPES
    except Exception:  # noqa: BLE001 — config import must never break the path
        return ALL_ENTITY_TYPES


def is_enabled() -> bool:
    """True when PII pseudonymization is turned on (default True)."""
    try:
        from app.config import settings
        return bool(getattr(settings, "pii_pseudonymization_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def get_vault(session_id: Optional[str]) -> PseudonymVault:
    """Return the vault for *session_id*, creating it on first use.

    A ``None``/empty session id gets a shared anonymous vault so callers without
    a session still pseudonymize consistently within the process.
    """
    key = session_id or "__anon__"
    with _LOCK:
        vault = _VAULTS.get(key)
        if vault is None:
            vault = PseudonymVault(entity_types=_configured_entity_types())
            _VAULTS[key] = vault
        return vault


def drop_vault(session_id: Optional[str]) -> None:
    """Discard the vault (and its raw PII) for *session_id*. Best-effort."""
    key = session_id or "__anon__"
    with _LOCK:
        _VAULTS.pop(key, None)


def pseudonymize(text: str, session_id: Optional[str]) -> str:
    """Pseudonymize *text* using the session vault. No-op when disabled."""
    if not text or not is_enabled():
        return text
    return get_vault(session_id).pseudonymize(text)


def rehydrate(text: str, session_id: Optional[str]) -> str:
    """Restore real values in *text* from the session vault. No-op when disabled."""
    if not text or not is_enabled():
        return text
    return get_vault(session_id).rehydrate(text)


def rehydrate_obj(obj: Any, session_id: Optional[str]) -> Any:
    """Deep-rehydrate placeholder strings inside dicts/lists (e.g. structured output)."""
    if isinstance(obj, str):
        return rehydrate(obj, session_id)
    if isinstance(obj, dict):
        return {k: rehydrate_obj(v, session_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rehydrate_obj(v, session_id) for v in obj]
    return obj


def redaction_summary(session_id: Optional[str]) -> List[Dict[str, str]]:
    """UI-safe redaction summary for the session. Empty list when disabled."""
    if not is_enabled():
        return []
    key = session_id or "__anon__"
    with _LOCK:
        vault = _VAULTS.get(key)
    return vault.summary() if vault else []
