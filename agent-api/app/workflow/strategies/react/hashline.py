"""Hashline — content-hash-anchored line edits.

Problem it solves
-----------------
``edit_file``'s str-replace contract requires ``old_string`` to appear EXACTLY
once and to be echoed back verbatim — brittle (duplicate snippets / drifted
whitespace fail the edit) and token-heavy (the model must re-quote whole blocks).

Hashline instead anchors an edit to ``(line_number, short_content_hash)`` pairs:

* **Drift-tolerant** — the hash is located anywhere in the file, so lines inserted
  above the anchor don't break it (the line number only disambiguates when the
  same content appears more than once).
* **Fail-clean** — if the anchored line's *content* changed, its hash no longer
  matches and the edit raises rather than silently hitting the wrong line.
* **Cheap** — anchors are ``L<n>#<hash>`` tokens, not re-quoted source blocks.

A read view annotates each line as ``L<n>#<hash>: <text>`` (see :func:`annotate`)
so the model can cite anchors it has actually seen.

Pure / synchronous — trivially unit-testable; no IO here.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Accepts "L42#a1b2c3", "42#a1b2c3", "42:a1b2c3" (case-insensitive hash).
_ANCHOR_RE = re.compile(r"^\s*[lL]?(\d+)\s*[#:]\s*([0-9a-fA-F]{4,16})\s*$")

# Hash prefix length emitted by annotate(). Short enough to be cheap, long enough
# to make accidental collisions within one file vanishingly unlikely.
_HASH_LEN = 8


class HashlineError(ValueError):
    """Raised when an anchor cannot be resolved unambiguously."""


def line_hash(line: str, length: int = _HASH_LEN) -> str:
    """Stable short hash of a single line's content (newline-insensitive)."""
    norm = line.rstrip("\r\n")
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:length]


def annotate(content: str) -> str:
    """Return *content* with each line prefixed ``L<n>#<hash>: ``.

    This is the view an edit-oriented read emits so the model can cite anchors.
    """
    lines = content.splitlines()
    return "\n".join(f"L{i + 1}#{line_hash(l)}: {l}" for i, l in enumerate(lines))


def parse_anchor(anchor: str) -> Tuple[int, str]:
    """Parse ``L<n>#<hash>`` (or ``<n>:<hash>``) into ``(line_no, hash)``."""
    m = _ANCHOR_RE.match(anchor or "")
    if not m:
        raise HashlineError(
            f"malformed anchor {anchor!r} — expected 'L<line>#<hash>' "
            f"(e.g. 'L42#a1b2c3d4') as shown in the annotated read"
        )
    return int(m.group(1)), m.group(2).lower()


def _locate(lines: List[str], line_no: int, want_hash: str) -> int:
    """Return the 0-based index of the line matching *want_hash*.

    Prefers the expected ``line_no`` when it matches; otherwise locates the hash
    anywhere (drift tolerance), disambiguating multiple matches by proximity to
    the expected line. Raises :class:`HashlineError` when nothing matches.
    """
    n = len(want_hash)
    expected_idx = line_no - 1

    # Fast path: the line is exactly where the model said it was.
    if 0 <= expected_idx < len(lines) and line_hash(lines[expected_idx])[:n] == want_hash:
        return expected_idx

    matches = [i for i, ln in enumerate(lines) if line_hash(ln)[:n] == want_hash]
    if not matches:
        raise HashlineError(
            f"anchor L{line_no}#{want_hash} not found — the line's content has "
            f"changed since it was read; re-read the file and retry"
        )
    # Drifted: pick the occurrence closest to where the model expected it.
    return min(matches, key=lambda i: abs(i - expected_idx))


@dataclass
class HashlineResult:
    updated: str
    start_line: int      # 1-based, resolved
    end_line: int        # 1-based, resolved
    removed: int         # lines removed
    added: int           # lines added


def apply_edit(
    content: str,
    start_anchor: str,
    new_text: str,
    end_anchor: Optional[str] = None,
) -> HashlineResult:
    """Replace the anchored line span ``[start_anchor..end_anchor]`` with *new_text*.

    A single-line edit omits *end_anchor* (defaults to *start_anchor*). The
    replacement text may be any number of lines (including empty, i.e. deletion).
    Raises :class:`HashlineError` on ambiguous / stale anchors or an inverted span.
    """
    # splitlines() drops the trailing newline; we restore it from the original so
    # an edit that doesn't touch the last line preserves file-final-newline state.
    lines = content.splitlines()
    s_line, s_hash = parse_anchor(start_anchor)
    e_line, e_hash = parse_anchor(end_anchor) if end_anchor else (s_line, s_hash)

    start_idx = _locate(lines, s_line, s_hash)
    end_idx = _locate(lines, e_line, e_hash)
    if end_idx < start_idx:
        raise HashlineError(
            f"end anchor (L{e_line}) resolves before start anchor (L{s_line})"
        )

    new_lines = new_text.splitlines() if new_text else []
    updated_lines = lines[:start_idx] + new_lines + lines[end_idx + 1:]

    trailing = "\n" if content.endswith("\n") else ""
    updated = "\n".join(updated_lines) + (trailing if updated_lines else "")
    return HashlineResult(
        updated=updated,
        start_line=start_idx + 1,
        end_line=end_idx + 1,
        removed=(end_idx - start_idx + 1),
        added=len(new_lines),
    )
