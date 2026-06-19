"""PII detection for the cloud (Bedrock) boundary.

Regex-first, deterministic detection of personally identifiable information so it
can be pseudonymized before leaving the process. Unlike ``app/core/redact.py``
(a one-way scrub of *credentials* for logs), this module feeds a *reversible*
pseudonymization vault (see :mod:`app.core.privacy.vault`): detected spans are
swapped for stable placeholders the LLM reasons over, then re-hydrated in the
final answer.

Detection is precision-biased: a false negative leaks PII to the cloud, but a
false positive only makes the model reason over a placeholder, so patterns are
kept tight (e.g. credit cards are Luhn-validated) to avoid mangling unrelated
text like ordinary long digit strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Pattern, Tuple

# Entity-type constants (kept as plain strings so they round-trip through config
# and JSON without an enum import on the hot path).
EMAIL = "EMAIL"
PHONE = "PHONE"
SSN = "SSN"
CREDIT_CARD = "CREDIT_CARD"
IP = "IP"
ACCOUNT_ID = "ACCOUNT_ID"

ALL_ENTITY_TYPES: Tuple[str, ...] = (EMAIL, PHONE, SSN, CREDIT_CARD, IP, ACCOUNT_ID)


@dataclass(frozen=True)
class Span:
    """A detected PII occurrence: ``text[start:end]`` is ``value`` of ``type``."""

    type: str
    start: int
    end: int
    value: str


# Ordered by priority: earlier patterns win when spans overlap (see _dedupe).
# CREDIT_CARD before PHONE/ACCOUNT_ID so a 16-digit card is not mis-tagged.
_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    (EMAIL, re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (SSN, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # 13–19 digit runs allowing spaces/dashes; Luhn-validated below.
    (CREDIT_CARD, re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # E.164 and common national formats: +1 415 555 0132, (415) 555-0132, etc.
    (PHONE, re.compile(
        r"(?<!\d)(?:\+?\d{1,3}[ .\-]?)?(?:\(\d{2,4}\)[ .\-]?|\d{2,4}[ .\-])"
        r"\d{3,4}[ .\-]?\d{3,4}(?!\d)"
    )),
    (IP, re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
    )),
    # 12-digit AWS account ids (often appear bare or in ARNs).
    (ACCOUNT_ID, re.compile(r"\b\d{12}\b")),
]


def _luhn_ok(digits: str) -> bool:
    """Return True if *digits* (a string of digits) passes the Luhn checksum."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect(text: str, *, entity_types: Tuple[str, ...] = ALL_ENTITY_TYPES) -> List[Span]:
    """Return non-overlapping PII spans in *text*, restricted to *entity_types*.

    Spans are returned sorted by ``start``. On overlap, the higher-priority
    pattern (earlier in ``_PATTERNS``) wins, so a credit card is never also
    reported as a phone number or account id.
    """
    if not text:
        return []
    enabled = set(entity_types)
    found: List[Span] = []
    for kind, pattern in _PATTERNS:
        if kind not in enabled:
            continue
        for m in pattern.finditer(text):
            raw = m.group(0)
            if kind == CREDIT_CARD:
                digits = re.sub(r"\D", "", raw)
                if len(digits) < 13 or not _luhn_ok(digits):
                    continue
            found.append(Span(type=kind, start=m.start(), end=m.end(), value=raw))
    return _dedupe(found)


def _dedupe(spans: List[Span]) -> List[Span]:
    """Drop spans that overlap an already-accepted, higher-priority span."""
    # Priority = pattern order; emit in document order but resolve overlaps by it.
    priority: Dict[str, int] = {kind: i for i, (kind, _) in enumerate(_PATTERNS)}
    spans_sorted = sorted(spans, key=lambda s: (s.start, priority.get(s.type, 99)))
    accepted: List[Span] = []
    occupied_end = -1
    for span in spans_sorted:
        if span.start < occupied_end:
            continue  # overlaps a kept span
        accepted.append(span)
        occupied_end = span.end
    return accepted
