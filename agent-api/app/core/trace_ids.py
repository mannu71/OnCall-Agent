"""Detect correlation / trace / request identifiers in free-text queries.

Used by the CloudWatch chat fast-path: when a user pastes an ID (correlation id,
X-Ray trace id, IIS/Kestrel request id, GUID, or a ``key: value`` form), the
pipeline can go straight to a targeted ``correlate_logs`` instead of running the
broad triage. Pure regex, no dependencies.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# AWS X-Ray trace id: 1-<8 hex>-<24 hex>
_XRAY_RE = re.compile(r"\b1-[0-9a-fA-F]{8}-[0-9a-fA-F]{24}\b")
# UUID / GUID
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# IIS / Kestrel request id, e.g. 0HMN0I47ARND3:00000002
_IIS_RE = re.compile(r"\b[0-9A-Z]{10,}:[0-9A-Fa-f]{8}\b")
# Explicit "correlation id: X" / "requestId=X" / "trace id X" forms.
_KEYED_RE = re.compile(
    r"(?:correlation[\s_-]?id|correlationid|request[\s_-]?id|requestid|"
    r"trace[\s_-]?id|traceid|transaction[\s_-]?id|session[\s_-]?id)"
    r"\s*[:=]?\s*[\"']?([A-Za-z0-9._:\-]{6,})",
    re.IGNORECASE,
)


def _dedup(values: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for v in values:
        v = (v or "").strip().strip("\"'")
        if v and v not in seen:
            seen.append(v)
    return seen


def extract_trace_ids(text: Optional[str]) -> Dict[str, Any]:
    """Return detected identifiers from ``text``.

    Result shape::

        {
          "found": bool,
          "trace_id": "<X-Ray id or None>",
          "correlation_id": "<best correlation/request id or None>",
          "all": {"xray": [...], "uuid": [...], "iis": [...], "keyed": [...]},
        }

    ``trace_id`` is reserved for X-Ray ids; everything else is a
    ``correlation_id`` candidate (keyed forms preferred, then IIS, then UUID).
    """
    text = text or ""
    xray = _dedup(_XRAY_RE.findall(text))
    uuid = _dedup(_UUID_RE.findall(text))
    iis = _dedup(_IIS_RE.findall(text))
    keyed = _dedup(_KEYED_RE.findall(text))

    trace_id = xray[0] if xray else None

    correlation_id: Optional[str] = None
    for group in (keyed, iis, uuid):
        for value in group:
            # Never treat an X-Ray id as the correlation id.
            if value != trace_id and not _XRAY_RE.fullmatch(value):
                correlation_id = value
                break
        if correlation_id:
            break

    return {
        "found": bool(trace_id or correlation_id),
        "trace_id": trace_id,
        "correlation_id": correlation_id,
        "all": {"xray": xray, "uuid": uuid, "iis": iis, "keyed": keyed},
    }
