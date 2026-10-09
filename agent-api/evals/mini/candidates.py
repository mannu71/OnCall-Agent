"""Candidates scored by the bake-off, one dict per role.

Each candidate is a plain function over one input. Today's production
behaviour is registered as ``baseline`` for every role; model candidates are
added here as they are vendored (see the mini-model plan, milestone M0), and
must beat the baseline on the role's gate before shipping.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple


# ── router: text -> "chat" | "lookup" | "investigate" ────────────────────────
def router_baseline(text: str) -> str:
    """Today: small talk is answered directly, everything else is investigated.

    There is no lookup path yet, so this baseline never predicts "lookup".
    """
    from app.core.quality.intent import is_conversational

    return "chat" if is_conversational(text) else "investigate"


# ── pii: text -> [(type, start, end)] ────────────────────────────────────────
def pii_baseline(text: str) -> List[Tuple[str, int, int]]:
    """Today's regex detector over every configured entity type."""
    from app.core.privacy.classifier import ALL_ENTITY_TYPES, detect

    return [(s.type, s.start, s.end) for s in detect(text, entity_types=ALL_ENTITY_TYPES)]


# ── injection: text -> flagged? ──────────────────────────────────────────────
def injection_baseline(text: str) -> bool:
    """Today's regex scanner (raises on a match)."""
    from app.core.security import InjectionError, scan_injection

    try:
        scan_injection(text)
    except InjectionError:
        return True
    return False


# ── templates: log line -> group key ─────────────────────────────────────────
def template_baseline(text: str) -> str:
    """Today's CloudWatch normalizer; lines with the same output share a group."""
    from app.mcp.tools.watch_tools import _normalize_message

    return _normalize_message(text)


CANDIDATES: Dict[str, Dict[str, Callable]] = {
    "router": {"baseline": router_baseline},
    "pii": {"baseline": pii_baseline},
    "injection": {"baseline": injection_baseline},
    "templates": {"baseline": template_baseline},
}
