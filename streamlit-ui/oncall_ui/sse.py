"""Minimal Server-Sent Events parser for agent-api's progress streams."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator


@dataclass
class SseEvent:
    event: str
    data: Dict[str, Any]


def parse_payload(raw: str) -> Dict[str, Any]:
    """Decode one ``data:`` payload.

    agent-api serialises ``ExecutionEvent`` as ``{event_type, data: {...},
    timestamp}``; the nested ``data`` is merged up so handlers can read
    ``tool`` / ``token`` / ``node_id`` directly (same unwrap the React client
    did). Flat events pass through unchanged; non-JSON becomes ``{message}``.
    """
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {"message": raw} if raw else {}
    if not isinstance(obj, dict):
        return {"value": obj}
    inner = obj.get("data")
    if isinstance(inner, dict):
        return {**obj, **inner}
    return obj


def iter_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Group raw SSE lines into events (blank line terminates an event)."""
    event_type = "message"
    data_lines: list[str] = []
    for line in lines:
        if line is None:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        line = line.rstrip("\r")
        if line == "":
            if data_lines:
                yield SseEvent(event_type, parse_payload("\n".join(data_lines)))
            event_type, data_lines = "message", []
            continue
        if line.startswith(":"):
            continue  # comment / heartbeat
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value or "message"
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield SseEvent(event_type, parse_payload("\n".join(data_lines)))
