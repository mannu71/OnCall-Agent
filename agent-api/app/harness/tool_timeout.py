"""Per-tool wall-clock cap.

One hung tool call must not consume a whole investigation. LangGraph has no
per-tool timeout of its own, so without this a tool that never returns is
bounded only by the run-deadline backstop — by which point the turn is over.

The cap is the smaller of ``agent_tool_call_timeout_seconds`` and whatever is
left on the run deadline (:mod:`app.harness.run_budget`): there is no point
letting a tool run past the moment the whole run must stop.

A timeout is surfaced to the model as an ordinary error string, never raised.
The turn continues so the model can adapt — narrow the query, try another tool
— which is strictly more useful than failing the run.

Implementation note: this wraps each tool's async ``coroutine`` and returns a
*copy* of the tool, rather than wrapping the tool object itself. The tool must
stay a real ``BaseTool``: ``bind_tools`` converts it to a provider tool schema,
and a duck-typed proxy fails that conversion. Copying also means the caller's
tool instances are never mutated, so a tool shared between the parent agent and
a subagent does not inherit the other's cap.

Disabled by default (knob 0 and no deadline → tools are returned untouched, so
an unconfigured deployment pays nothing).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional

from app.config import settings
from app.harness.run_budget import get_run_budget

logger = logging.getLogger(__name__)


def _configured_cap() -> float:
    try:
        return float(getattr(settings, "agent_tool_call_timeout_seconds", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def effective_timeout() -> Optional[float]:
    """``min(configured cap, time left on the run deadline)``, or ``None`` when
    neither is set. Never returns a non-positive value: a run already past its
    deadline still gives a tool a floor of 1s, so an in-flight call can finish
    honestly instead of being cancelled the instant it starts."""
    caps: List[float] = []
    cap = _configured_cap()
    if cap > 0:
        caps.append(cap)
    budget = get_run_budget()
    if budget is not None and budget.deadline_monotonic is not None:
        caps.append(max(1.0, budget.deadline_monotonic - time.monotonic()))
    return min(caps) if caps else None


def _wrap_coroutine(fn: Any, tool_name: str) -> Any:
    async def _capped(*args: Any, **kwargs: Any) -> Any:
        timeout = effective_timeout()
        if not timeout or timeout <= 0:
            return await fn(*args, **kwargs)
        try:
            return await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "tool_timeout: '%s' exceeded %.0fs and was cancelled",
                tool_name, timeout,
            )
            # Same wording the previous (native-engine) implementation used, so
            # trajectories and failure fingerprints do not diverge on phrasing.
            return (
                f"Error: tool '{tool_name}' timed out after {timeout:.0f}s and was "
                f"cancelled. Try a narrower query or a different approach."
            )

    return _capped


def wrap_tools_with_timeout(tools: List[Any]) -> List[Any]:
    """Apply the per-tool cap, or return the tools untouched when no cap could
    ever apply (knob unset AND no run deadline configured).

    Two shapes are handled, because the action space mixes them:

    * ``coroutine``-backed tools (``StructuredTool``: playbook, crawler,
      delegate, …) have no timeout of their own, so their coroutine is wrapped.
    * Tools that already own a timeout FIELD (``MCPToolWrapper.tool_timeout``,
      which defaults to the MCP manager's 60s) are already bounded; here we only
      TIGHTEN that field to the configured cap so the operator-facing knob
      actually reaches them. Never loosened — a tool's own stricter limit wins.

    Anything else is passed through: a sync-only tool cannot be cancelled by
    ``asyncio``, so a timeout there would be a lie.
    """
    if _configured_cap() <= 0 and float(
        getattr(settings, "agent_run_deadline_seconds", 0.0) or 0.0
    ) <= 0:
        return tools

    cap = _configured_cap()
    out: List[Any] = []
    wrapped_n = 0
    tightened_n = 0
    passed_n = 0
    for tool in tools:
        name = getattr(tool, "name", "unknown")
        fn = getattr(tool, "coroutine", None)
        if callable(fn):
            try:
                out.append(tool.model_copy(update={"coroutine": _wrap_coroutine(fn, name)}))
                wrapped_n += 1
                continue
            except Exception as exc:  # noqa: BLE001 — a cap must never break assembly
                logger.debug("tool_timeout: could not wrap '%s' (%s)", name, exc)
                out.append(tool)
                continue
        # Self-bounding tool: tighten its own field rather than double-wrapping.
        if cap > 0 and hasattr(tool, "tool_timeout"):
            try:
                existing = getattr(tool, "tool_timeout", None)
                if existing is None or float(existing) > cap:
                    out.append(tool.model_copy(update={"tool_timeout": cap}))
                    tightened_n += 1
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.debug("tool_timeout: could not tighten '%s' (%s)", name, exc)
        out.append(tool)
        passed_n += 1
    if wrapped_n or tightened_n:
        logger.info(
            "tool_timeout: %d tool(s) capped, %d tightened to their own limit, "
            "%d already-bounded/sync left as-is (of %d)",
            wrapped_n, tightened_n, passed_n, len(tools),
        )
    return out


__all__ = ["wrap_tools_with_timeout", "effective_timeout"]
