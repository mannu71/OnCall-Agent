"""Environment-first verification tracking (3.1).

Tools whose successful call means "the agent changed code" (edit_file /
create_file) are expected to be followed by a run_verify call before a run
can honestly report success. This module owns the scanning logic that derives
the ``verify_pending`` / ``verify_last_passed`` signals for the LangGraph path
(``app.harness.agent_runner``) — matching edit_tools.py's tool names and the
harness's own "# Verify your changes" system-prompt discipline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, List, Optional

# Tools whose successful call sets verify_pending=True.
EDIT_TOOL_NAMES = frozenset({"edit_file", "create_file"})


@dataclass
class VerifyState:
    """The two verify signals tracked across a run."""

    pending: bool = False
    last_passed: Optional[bool] = None


def scan_tool_calls(state: VerifyState, tool_calls: List[Any], tool_messages: List[Any]) -> None:
    """Update *state* in place from one turn's (tool_calls, tool_messages) pair.

    ``tool_calls`` entries may be plain dicts ({"name": ...})
    or LangChain tool_call dicts (LangGraph: {"name": ...} too — same shape).
    ``tool_messages`` entries must expose ``.content`` (a JSON string) or be
    plain strings. Best-effort — never raises.
    """
    for tc, msg in zip(tool_calls, tool_messages):
        name = (tc or {}).get("name") or ""
        if name not in EDIT_TOOL_NAMES and name != "run_verify":
            continue
        content = getattr(msg, "content", msg)
        try:
            payload = json.loads(content) if isinstance(content, str) else {}
        except Exception:  # noqa: BLE001
            payload = {}
        if name in EDIT_TOOL_NAMES:
            if payload.get("ok") is True:
                state.pending = True
        else:  # run_verify
            state.pending = False
            state.last_passed = bool(payload.get("ok"))


def scan_one(state: VerifyState, tool_name: str, output_str: Any) -> None:
    """Update *state* in place from a single tool call/result pair — the
    LangGraph streaming path's granularity (one on_tool_end event at a
    time, rather than a batch of same-turn tool calls)."""
    scan_tool_calls(state, [{"name": tool_name}], [output_str])


__all__ = ["EDIT_TOOL_NAMES", "VerifyState", "scan_tool_calls", "scan_one"]
