"""Per-tool permission gatekeeping (rules + ask-in-chat).

Mirrors claude-code's ``hasPermissionsToUseTool``: every tool call is classified
``allow | ask | deny`` by name-pattern rules, with a precedence of
deny > ask > allow. Read-only investigation tools auto-allow; mutating tools
(``save_playbook``, ``execute_skill``, ``crawler_index_repo``, ``delegate_*``,
``*_write`` …) require approval.

Enforcement wraps each tool's coroutine:
  * **allow** → run unchanged.
  * **deny**  → return a structured "blocked" result (the ReAct loop continues).
  * **ask**   → call LangGraph ``interrupt()`` so the chat surfaces an
    approve/deny card; on resume the tool runs (approved) or returns blocked.

Modes:
  * ``default``    — apply the rules (ask gates risky tools).
  * ``auto_allow`` — skip ask prompts (everything not denied runs).
  * ``plan``       — dry-run: risky tools stay ``ask`` (used to preview actions).
"""
from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

PermissionMode = str  # "default" | "auto_allow" | "plan"
Behavior = str        # "allow" | "ask" | "deny"

# Mutating / side-effectful tools that require approval by default.
DEFAULT_ASK_PATTERNS = (
    "save_playbook",
    "patch_playbook",
    "execute_skill",
    "crawler_index_repo",
    "delegate_investigation",
    "edit_file",
    "create_file",   # NOTE: the "*_create" suffix glob below does NOT match this
    "apply_patch",
    "run_command",
    "*_write",
    "*_delete",
    "*_update",
    "*_insert",
    "*_create",
)
# Nothing denied outright by default; operators can add patterns here / via config.
DEFAULT_DENY_PATTERNS: tuple = ()


def _match(name: str, patterns) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def evaluate(
    tool_name: str,
    mode: PermissionMode = "default",
    ask_patterns=DEFAULT_ASK_PATTERNS,
    deny_patterns=DEFAULT_DENY_PATTERNS,
) -> Behavior:
    """Classify a tool call. Precedence: deny > ask > allow."""
    name = tool_name or ""
    if _match(name, deny_patterns):
        return "deny"
    is_ask = _match(name, ask_patterns)
    if mode == "auto_allow":
        return "allow"          # deny already handled above
    # default and plan both gate risky tools as "ask"
    return "ask" if is_ask else "allow"


def _clone_with_coroutine(tool: Any, coroutine) -> Any:
    """Return a copy of *tool* with a replaced async implementation.

    Keeps name / description / args_schema identical so the model-facing tool
    schema (and the Bedrock/Anthropic prompt-cache prefix) is unchanged.
    """
    from langchain_core.tools import StructuredTool
    return StructuredTool.from_function(
        coroutine=coroutine,
        name=tool.name,
        description=tool.description,
        args_schema=getattr(tool, "args_schema", None),
    )


# How long an ``ask`` tool blocks waiting for operator approval before it
# auto-denies (keeps a forgotten approval from hanging the request forever).
_APPROVAL_TIMEOUT_SECONDS = 300.0


async def _record_approval_pending(
    execution_id: Optional[str],
    request_id: str,
    tool_name: str,
    summary: str,
) -> None:
    """Best-effort durable audit of a newly-opened approval gate."""
    if not execution_id:
        return
    try:
        from app.infrastructure.persistence import tool_approval_repository
        await tool_approval_repository.record_pending(
            execution_id, request_id, tool_name, summary,
        )
    except Exception as exc:  # noqa: BLE001 — audit must never break the gate
        logger.debug("tool_permissions: approval audit (pending) skipped: %s", exc)


async def _record_approval_decision(
    execution_id: Optional[str],
    request_id: str,
    decision: str,
    *,
    reason: Optional[str] = None,
) -> None:
    """Best-effort durable audit of a resolved approval decision."""
    if not execution_id:
        return
    try:
        from app.infrastructure.persistence import tool_approval_repository
        await tool_approval_repository.record_decision(
            execution_id, request_id, decision, reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 — audit must never break the gate
        logger.debug("tool_permissions: approval audit (decision) skipped: %s", exc)


def _json_safe(value: Any) -> Any:
    """Coerce *value* into something ``json.dumps`` (and the SSE layer) accept.

    Tool kwargs can contain Pydantic models, sets, bytes, etc. that would make
    the approval card payload unserializable — which is what previously left the
    card with no context. Anything not natively JSON-friendly is stringified.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _summarize_args(tool_name: str, args: dict) -> str:
    """Build a short human-readable approval message including key arguments.

    Falls back to a bare ``Approve running '<tool>'?`` when there is nothing
    useful to surface, so the card is never blank.
    """
    if not isinstance(args, dict) or not args:
        return f"Approve running '{tool_name}'?"
    # Surface the most operator-relevant identifiers if present.
    target = args.get("file") or args.get("path") or args.get("repo") or args.get("table")
    if target:
        return f"Approve running '{tool_name}' on {target}?"
    # Otherwise name the first one or two scalar args.
    parts = []
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)) and v not in (None, ""):
            sval = str(v)
            parts.append(f"{k}={sval[:60]}")
        if len(parts) >= 2:
            break
    if parts:
        return f"Approve running '{tool_name}' ({', '.join(parts)})?"
    return f"Approve running '{tool_name}'?"


def wrap_tools_with_permissions(
    tools: List[Any],
    mode: PermissionMode = "default",
    execution_id: Optional[str] = None,
    execution_port: Any = None,
) -> List[Any]:
    """Wrap each tool with its permission behavior. Returns a new list.

    For ``ask`` tools, when an ``execution_port`` + ``execution_id`` are given the
    wrapper publishes a ``hitl_pause`` (approve/deny card in chat) and blocks on
    the execution's ``hitl_queue`` until the operator decides — reusing the same
    ``POST /executions/{id}/approve`` channel as supervisor HITL. Without that
    channel it fails safe (blocks the tool with an explanatory message).
    """
    import asyncio
    import uuid

    wrapped: List[Any] = []
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        behavior = evaluate(name, mode)
        if behavior == "allow":
            wrapped.append(tool)
            continue

        original = getattr(tool, "coroutine", None)

        if behavior == "deny":
            async def _denied(*_a, __name=name, **_k) -> str:
                logger.info("tool_permissions: DENY %s", __name)
                return json.dumps({
                    "blocked": True, "tool": __name,
                    "reason": f"Tool '{__name}' is blocked by permission policy.",
                })
            wrapped.append(_clone_with_coroutine(tool, _denied))
            continue

        # behavior == "ask" — block on operator approval, routed by request_id.
        # IMPORTANT: a model turn can emit SEVERAL ask-tool calls at once (e.g.
        # multiple edit_file). Each must wait for ITS OWN approval. A single
        # shared queue mis-delivers an approval to whichever waiter happens to
        # win queue.get(), leaving the rest hung ("stuck after approval"). So we
        # register a per-request_id Future and the approve endpoint resolves the
        # matching one. See app/api/v1/endpoints/executions.py::approve_hitl_request.
        async def _gated(*_a, __orig=original, __name=name, **kwargs) -> Any:
            runtime = execution_port.get_runtime(execution_id) if (execution_port and execution_id) else None
            if not isinstance(runtime, dict) or not runtime:
                logger.warning("tool_permissions: ASK %s has no approval channel; blocking", __name)
                return json.dumps({
                    "blocked": True, "tool": __name,
                    "reason": f"Tool '{__name}' requires approval but no approval channel is "
                              f"available. Re-run with permission_mode=auto_allow to allow.",
                })
            approvals = runtime.get("tool_approvals")
            if approvals is None:
                approvals = {}
                runtime["tool_approvals"] = approvals

            request_id = uuid.uuid4().hex
            decision_future: "asyncio.Future" = asyncio.get_event_loop().create_future()
            approvals[request_id] = decision_future
            safe_args = _json_safe(kwargs)
            _summary = _summarize_args(__name, safe_args)

            # Durable audit: write a pending row BEFORE blocking so the approval
            # is visible across page reloads / other operators, and the eventual
            # decision is recorded even if this process restarts mid-wait. Best
            # effort — never let an audit write break the live gate.
            await _record_approval_pending(execution_id, request_id, __name, _summary)

            try:
                await execution_port.publish_hitl_pause(execution_id, {
                    "request_id": request_id,
                    "draft_answer": "",
                    "message": _summary,
                    "type": "tool_approval",
                    "tool": __name,
                    "args": safe_args,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("tool_permissions: ASK %s publish failed (%s)", __name, exc)

            logger.info("tool_permissions: ASK %s → awaiting approval (request_id=%s)", __name, request_id)
            try:
                decision = await asyncio.wait_for(decision_future, timeout=_APPROVAL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.info("tool_permissions: ASK %s → approval timed out", __name)
                await _record_approval_decision(
                    execution_id, request_id, "timeout",
                    reason="Approval timed out",
                )
                return json.dumps({
                    "blocked": True, "tool": __name,
                    "reason": f"Tool '{__name}' was not approved in time and was skipped.",
                })
            finally:
                approvals.pop(request_id, None)

            approved = isinstance(decision, dict) and decision.get("approved") is True
            _reason = decision.get("reason") if isinstance(decision, dict) else None
            if not approved:
                logger.info("tool_permissions: ASK %s → denied by operator", __name)
                await _record_approval_decision(
                    execution_id, request_id, "denied", reason=_reason,
                )
                return json.dumps({
                    "blocked": True, "tool": __name,
                    "reason": _reason or f"Tool '{__name}' was not approved by the operator.",
                })
            logger.info("tool_permissions: ASK %s → approved", __name)
            await _record_approval_decision(
                execution_id, request_id, "approved", reason=_reason,
            )
            return await __orig(*_a, **kwargs)

        wrapped.append(_clone_with_coroutine(tool, _gated))

    return wrapped


# Idempotency marker so a result already capped (here or by a per-family cap)
# is never re-truncated / double-suffixed.
_TRUNCATION_MARKER = "[truncated"


def _cap_text(value: Any, max_chars: int, tool_name: str) -> Any:
    """Cap a tool result to *max_chars*, appending a recovery hint when cut.

    Non-string results pass through unchanged (they're already structured and
    typically small). Accuracy-safe: nothing is summarized — the model is told
    exactly how to fetch the remainder with a narrower query.
    """
    if not isinstance(value, str) or max_chars <= 0 or len(value) <= max_chars:
        return value
    if _TRUNCATION_MARKER in value[-200:]:
        return value  # already capped by a per-family guard
    total = len(value)
    suffix = (
        f"\n…[truncated: showing first {max_chars} of {total} chars from "
        f"'{tool_name}'. Narrow the query (WHERE/LIMIT, date range, name_like, "
        f"drill_down, or pagination) and call again to retrieve the rest.]"
    )
    return value[:max_chars] + suffix


def wrap_tools_with_output_cap(tools: List[Any], max_chars: int) -> List[Any]:
    """Wrap each tool's coroutine so its result is capped to *max_chars*.

    A universal token safety-net: an unbounded tool result is replayed in the
    message history on every subsequent ReAct iteration, so a single large dump
    silently inflates input cost across the whole loop. Tools that already cap
    their own output (MCP via ``_arun``; crawler/delegate via per-family limits)
    are unaffected — MCP wrappers expose no ``coroutine`` so they're skipped, and
    already-capped strings are detected via the truncation marker.

    Applied AFTER the permission wrap so the model-facing tool schema (and the
    Bedrock/Anthropic prompt-cache prefix) is unchanged.
    """
    if not max_chars or max_chars <= 0:
        return tools
    capped: List[Any] = []
    for tool in tools:
        original = getattr(tool, "coroutine", None)
        if original is None:
            capped.append(tool)  # e.g. MCPToolWrapper — self-caps in _arun
            continue

        async def _capped(*_a, __orig=original, __name=getattr(tool, "name", "") or "", **kwargs) -> Any:
            out = await __orig(*_a, **kwargs)
            return _cap_text(out, max_chars, __name)

        capped.append(_clone_with_coroutine(tool, _capped))
    return capped
