"""ID-grounding guard — flag identifiers an agent cited that no tool produced.

A lightweight, log-only hallucination signal for the agent's FINAL narrative: an
engineer-grep-able identifier (UUID, AWS request/trace id, long hex) that appears
in the answer but in neither the user's query nor any tool output is likely
fabricated. This module only *detects and reports* — it never blocks or rewrites
an answer (the agent's own evidence discipline and the eval harness own the
quality bar; this is production telemetry).

The regex mirrors ``evals/accuracy/graders.py`` rather than importing it: ``app``
is baked into the image while ``evals`` is copied in for runs, so app code must
not depend on it.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Set

# Tokens that look like identifiers an engineer would grep for. Kept in sync with
# evals/accuracy/graders.py::_ID_PATTERNS.
_ID_PATTERNS = [
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",  # uuid
    r"\b[0-9a-f]{16,}\b",                       # long hex (trace/span ids)
    r"\breq-[0-9a-zA-Z]{6,}\b",                 # req-xxxx
    r"\b1-[0-9a-f]{8}-[0-9a-f]{24}\b",          # aws xray trace id
]
_ID_RE = re.compile("|".join(_ID_PATTERNS))

# Common English/hex false positives we never count as IDs.
_STOPWORDS = {"deadbeef", "facade", "decade", "beaded", "defaced"}


def extract_ids(text: str) -> List[str]:
    """Pull ID-like tokens out of text (deduped, order-preserving)."""
    out: List[str] = []
    seen: Set[str] = set()
    for m in _ID_RE.findall(text or ""):
        tok = m if isinstance(m, str) else next((g for g in m if g), "")
        low = tok.lower()
        if tok and low not in _STOPWORDS and low not in seen:
            seen.add(low)
            out.append(tok)
    return out


def ungrounded_ids(narrative: str, evidence_ids: Iterable[str], allow_ids: Iterable[str] = ()) -> List[str]:
    """Return IDs cited in *narrative* that appear in neither evidence nor allow-list.

    ``evidence_ids`` are IDs seen in tool outputs; ``allow_ids`` are IDs the user
    supplied in the query (legitimately echoed back). Comparison is case-insensitive.
    """
    grounded = {i.lower() for i in evidence_ids} | {i.lower() for i in allow_ids}
    return [i for i in extract_ids(narrative) if i.lower() not in grounded]
