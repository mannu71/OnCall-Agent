"""Recover a JSON object from an LLM response.

Models wrap JSON in prose, markdown fences, or a preamble ("Here is the
verdict:"). The obvious `re.search(r"\\{.*\\}", text, re.DOTALL)` is GREEDY: it
spans from the first ``{`` to the LAST ``}`` anywhere in the response, so a
single brace in the surrounding prose — or a trailing "note: {see above}" —
makes the captured span unparseable and the whole extraction fail.

This scans for balanced objects instead, ignoring braces inside string literals.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

__all__ = ["extract_json_object", "iter_json_objects"]


def iter_json_objects(text: str) -> Iterator[str]:
    """Yield candidate balanced ``{...}`` spans, outermost first, left to right."""
    if not text:
        return
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : i + 1]
                    start = -1


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Return the first balanced span that parses as a JSON *object*, else None.

    Later spans are tried too: a response like ``"thinking {maybe} ... {\"a\":1}"``
    has an earlier balanced span that is not valid JSON, and the payload is the
    one after it.
    """
    for candidate in iter_json_objects(text or ""):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
