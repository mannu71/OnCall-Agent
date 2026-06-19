"""Reversible pseudonymization vault.

A :class:`PseudonymVault` holds a per-session bijection between real PII values
and stable placeholders (``[EMAIL_1]``, ``[SSN_2]`` …). The LLM only ever sees
placeholders; the vault re-hydrates them in the final answer shown to the user.

Design constraints:
  * **Stable within a session** — the same real value always maps to the same
    placeholder, so the agent can correlate references across turns/tool calls.
  * **In-memory only** — vaults are never persisted; raw PII lives only for the
    request/session lifetime. The redaction *summary* (counts + placeholders +
    masked previews) is safe to surface to the UI; raw values are not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .classifier import ALL_ENTITY_TYPES, detect

# Matches any placeholder we emit, for re-hydration: [TYPE_N].
_PLACEHOLDER_RE = re.compile(r"\[([A-Z_]+)_(\d+)\]")


def _mask(value: str, kind: str) -> str:
    """Return a non-reversible preview of *value* safe to show in the UI."""
    if kind == "EMAIL" and "@" in value:
        local, _, domain = value.partition("@")
        dom_head = domain.split(".")[0]
        return f"{local[:1]}•••@{dom_head[:1]}•••"
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 4:
        return f"•••• {digits[-4:]}"
    if len(value) <= 2:
        return "••"
    return f"{value[:1]}•••{value[-1:]}"


@dataclass
class PseudonymVault:
    """Per-session reversible map between real PII and placeholders."""

    entity_types: Tuple[str, ...] = ALL_ENTITY_TYPES
    _fwd: Dict[str, str] = field(default_factory=dict)        # real value -> placeholder
    _rev: Dict[str, str] = field(default_factory=dict)        # placeholder -> real value
    _meta: Dict[str, str] = field(default_factory=dict)       # placeholder -> entity type
    _counters: Dict[str, int] = field(default_factory=dict)   # entity type -> next index

    def _placeholder_for(self, value: str, kind: str) -> str:
        existing = self._fwd.get(value)
        if existing is not None:
            return existing
        idx = self._counters.get(kind, 0) + 1
        self._counters[kind] = idx
        placeholder = f"[{kind}_{idx}]"
        self._fwd[value] = placeholder
        self._rev[placeholder] = value
        self._meta[placeholder] = kind
        return placeholder

    def pseudonymize(self, text: str) -> str:
        """Replace every detected PII span in *text* with a stable placeholder."""
        if not text:
            return text
        spans = detect(text, entity_types=self.entity_types)
        if not spans:
            return text
        # Replace right-to-left so earlier offsets stay valid.
        out = text
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            placeholder = self._placeholder_for(span.value, span.type)
            out = out[: span.start] + placeholder + out[span.end :]
        return out

    def rehydrate(self, text: str) -> str:
        """Replace known placeholders in *text* with their real values."""
        if not text or not self._rev:
            return text

        def _sub(m: "re.Match[str]") -> str:
            return self._rev.get(m.group(0), m.group(0))

        return _PLACEHOLDER_RE.sub(_sub, text)

    def summary(self) -> List[Dict[str, str]]:
        """UI-safe redaction summary. Contains NO raw values.

        Returns one row per placeholder: ``{"type", "placeholder", "preview"}``.
        """
        rows: List[Dict[str, str]] = []
        for placeholder, value in self._rev.items():
            kind = self._meta.get(placeholder, "PII")
            rows.append({
                "type": kind,
                "placeholder": placeholder,
                "preview": _mask(value, kind),
            })
        return rows

    def __len__(self) -> int:
        return len(self._rev)
