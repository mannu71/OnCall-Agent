"""TOON (Token-Oriented Object Notation) — compact serialization for LLM payloads.

Encodes a *uniform array of objects* as a single header plus comma-separated rows
(column names written once instead of repeated per object), which is markedly
smaller than JSON for tabular data — typically 30–60% fewer tokens. Scalars are
``key: value``; non-uniform / nested values fall back to compact inline JSON so
nothing is lost.

Designed for the CloudWatch synthesis evidence payload (uniform ``error_patterns``
/ ``anomalies`` / ``drilldown_samples`` arrays). Prepend :data:`TOON_LEGEND` to the
prompt so the model parses the format reliably.
"""
from __future__ import annotations

import json
from typing import Any, List

#: One-line description prepended to the synthesis prompt so the LLM reads TOON.
TOON_LEGEND = (
    "Data below uses TOON (compact tabular notation): an array is written as "
    "`name[count]{col1,col2,...}:` followed by one comma-separated row per item "
    "(values in header-column order). Values containing commas, quotes, colons or "
    "newlines are wrapped in double quotes (with \"\" escaping). Plain scalars are "
    "`key: value`; nested objects appear as inline JSON."
)

_SPECIAL_CHARS = (",", '"', "\n", ":")


def _fmt_scalar(value: Any) -> str:
    """Render a scalar; quote+escape it if it contains TOON-special characters."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(ch in text for ch in _SPECIAL_CHARS):
        return '"' + text.replace('"', '""') + '"'
    return text


def _compact_json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _is_uniform_dict_list(value: Any) -> bool:
    """True for a non-empty list of dicts that all share the same key order."""
    if not isinstance(value, list) or not value:
        return False
    if not all(isinstance(item, dict) for item in value):
        return False
    keys = list(value[0].keys())
    if not keys:
        return False
    return all(list(item.keys()) == keys for item in value)


def _encode_cell(value: Any) -> str:
    """Render one table cell: scalars directly, nested structures as quoted JSON."""
    if isinstance(value, (dict, list)):
        return _fmt_scalar(_compact_json(value))
    return _fmt_scalar(value)


def _encode_table(name: str, rows: List[dict]) -> str:
    cols = list(rows[0].keys())
    lines = [f"{name}[{len(rows)}]{{{','.join(cols)}}}:"]
    for row in rows:
        lines.append("  " + ",".join(_encode_cell(row.get(c)) for c in cols))
    return "\n".join(lines)


def encode_toon(data: Any) -> str:
    """Encode ``data`` (typically a dict) as TOON text.

    Uniform arrays-of-objects become tables; nested/non-uniform values fall back
    to compact inline JSON; scalars become ``key: value`` lines.
    """
    if _is_uniform_dict_list(data):
        return _encode_table("items", data)
    if not isinstance(data, dict):
        if isinstance(data, (dict, list)):
            return _compact_json(data)
        return _fmt_scalar(data)

    lines: List[str] = []
    for key, value in data.items():
        if _is_uniform_dict_list(value):
            lines.append(_encode_table(str(key), value))
        elif isinstance(value, (dict, list)):
            lines.append(f"{key}: {_compact_json(value)}")
        else:
            lines.append(f"{key}: {_fmt_scalar(value)}")
    return "\n".join(lines)
