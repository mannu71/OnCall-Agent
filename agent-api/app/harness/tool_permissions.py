"""Per-tool permission gatekeeping (rules + ask-in-chat).

Mirrors claude-code's ``hasPermissionsToUseTool``: every tool call is classified
``allow | ask | deny`` by name-pattern rules, with a precedence of
deny > ask > allow. Read-only investigation tools auto-allow; mutating tools
(``save_playbook``, ``edit_file``, ``delegate_*``,
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

import asyncio
import fnmatch
import json
import logging
import uuid
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

PermissionMode = str  # "default" | "auto_allow" | "plan"
Behavior = str        # "allow" | "ask" | "deny"

# Mutating / side-effectful tools that require approval by default.
DEFAULT_ASK_PATTERNS = (
    "save_playbook",
    "patch_playbook",
    "pin_fact",
    "delegate_investigation",
    "edit_file",
    "create_file",   # NOTE: the "*_create" suffix glob below does NOT match this
    "apply_patch",
    "run_command",
    "run_verify",
    # VFS mutations. fs_write matches "*_write" already; the others match no
    # suffix glob, so name them so they route through the (Supervisor) gate too.
    "fs_append",
    "fs_upsert",
    "fs_prune",
    "*_write",
    "*_delete",
    "*_update",
    "*_insert",
    "*_create",
)
# Nothing denied outright by default; operators can add patterns here / via config.
DEFAULT_DENY_PATTERNS: tuple = ()

# Generic mutation verbs used as a LAST-RESORT signal for tools whose names the
# patterns above don't match and that carry no MCP read-only/destructive
# annotation — chiefly user-added MCP servers, whose tool names we can't
# enumerate ahead of time. Deliberately generic (no server- or tool-specific
# names) so any newly-wired server is classified without a code change. Matched
# as whole words against the leaf tool name ("{server}__{leaf}" → "{leaf}"),
# so read verbs (get/list/search/describe/query/fetch/read) are never caught.
_MUTATION_VERBS = frozenset({
    "create", "update", "delete", "remove", "write", "add", "set", "put",
    "post", "patch", "publish", "upload", "merge", "rename", "move", "insert",
    "edit", "destroy", "revoke", "grant", "assign", "close", "cancel",
    "approve", "reject", "comment", "link", "deploy", "send", "archive",
})


def _match(name: str, patterns) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _leaf(name: str) -> str:
    """Leaf tool name — strips the ``{server}__`` prefix on MCP composite names."""
    return name.rsplit("__", 1)[-1] if "__" in name else name


def _looks_like_mutation(tool_name: str) -> bool:
    """Generic verb heuristic: is any ``_``-delimited word of the leaf name a
    known mutation verb? Catches ``wit_create_work_item`` / ``add_comment`` that
    the suffix globs miss, while leaving ``describe_log_groups`` alone."""
    return any(w in _MUTATION_VERBS for w in _leaf(tool_name or "").lower().split("_"))


def classify_tool_mutation(tool: Any) -> Optional[bool]:
    """Best-effort: does *tool* mutate state?

    ``True``/``False`` come from explicit MCP annotations
    (``read_only_hint`` / ``destructive_hint``, populated by the MCP adapter)
    when present — the authoritative, server-declared signal. Otherwise a
    generic verb heuristic on the name yields ``True`` (looks mutating) or
    ``None`` (unknown). Never infers ``False`` from the name alone: the absence
    of a known verb doesn't prove a tool is read-only.
    """
    destructive = getattr(tool, "destructive_hint", None)
    read_only = getattr(tool, "read_only_hint", None)
    if destructive is True:
        return True
    if read_only is True:
        return False
    if read_only is False:
        return True
    return True if _looks_like_mutation(getattr(tool, "name", "") or "") else None


def evaluate(
    tool_name: str,
    mode: PermissionMode = "default",
    ask_patterns=DEFAULT_ASK_PATTERNS,
    deny_patterns=DEFAULT_DENY_PATTERNS,
    mutates: Optional[bool] = None,
) -> Behavior:
    """Classify a tool call. Precedence: deny > ask > allow.

    ``mutates`` is an optional tool-driven signal (from
    :func:`classify_tool_mutation`) for tools the static patterns can't name —
    e.g. user-added MCP servers. It only ever *promotes* an unmatched tool to
    ``ask`` (``True``) or confirms ``allow`` (``False``); explicit operator
    deny/ask patterns still win. When ``mutates`` is omitted, the same verb
    heuristic is applied to the name directly so name-only callers stay dynamic.
    """
    name = tool_name or ""
    if _match(name, deny_patterns):
        return "deny"
    if mode == "auto_allow":
        return "allow"          # deny already handled above
    if _match(name, ask_patterns):
        return "ask"
    # Static patterns didn't match — fall back to the dynamic, tool-driven signal.
    if mutates is True:
        return "ask"
    if mutates is False:
        return "allow"          # MCP server declared this tool read-only
    # Unknown: last-resort generic verb heuristic on the name.
    return "ask" if _looks_like_mutation(name) else "allow"


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
    *,
    risk_tier: Optional[str] = None,
    supervisor_verdict: Optional[str] = None,
    supervisor_reasoning: Optional[str] = None,
) -> None:
    """Best-effort durable audit of a newly-opened approval gate."""
    if not execution_id:
        return
    try:
        from app.infrastructure.persistence import tool_approval_repository
        await tool_approval_repository.record_pending(
            execution_id, request_id, tool_name, summary,
            risk_tier=risk_tier,
            supervisor_verdict=supervisor_verdict,
            supervisor_reasoning=supervisor_reasoning,
        )
    except Exception as exc:  # noqa: BLE001 — audit must never break the gate
        logger.debug("tool_permissions: approval audit (pending) skipped: %s", exc)


async def _record_approval_decision(
    execution_id: Optional[str],
    request_id: str,
    decision: str,
    *,
    reason: Optional[str] = None,
    decided_by: Optional[str] = None,
) -> None:
    """Best-effort durable audit of a resolved approval decision."""
    if not execution_id:
        return
    try:
        from app.infrastructure.persistence import tool_approval_repository
        await tool_approval_repository.record_decision(
            execution_id, request_id, decision, reason=reason, decided_by=decided_by,
        )
    except Exception as exc:  # noqa: BLE001 — audit must never break the gate
        logger.debug("tool_permissions: approval audit (decision) skipped: %s", exc)


async def _publish_supervisor_decision(
    execution_port: Any,
    execution_id: Optional[str],
    action_name: str,
    verdict: str,
    reasoning: Optional[str],
    risk_tier: Optional[str],
) -> None:
    """Emit a non-blocking ``supervisor_decision`` status event so the trace
    shows a Supervisor-auto-decided action (no human card was raised)."""
    if not execution_id or execution_port is None:
        return
    publish = getattr(execution_port, "publish_supervisor_decision", None)
    if publish is None:
        return
    try:
        await publish(execution_id, {
            "tool": action_name,
            "verdict": verdict,
            "reasoning": reasoning,
            "risk_tier": risk_tier,
        })
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the gate
        logger.debug("tool_permissions: supervisor_decision publish skipped: %s", exc)


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


async def request_action_approval(
    execution_id: Optional[str],
    execution_port: Any,
    action_name: str,
    args: Optional[dict] = None,
    summary: Optional[str] = None,
    *,
    risk_tier: Optional[str] = None,
    supervisor_verdict: Optional[str] = None,
    supervisor_reasoning: Optional[str] = None,
    timeout_s: float = _APPROVAL_TIMEOUT_SECONDS,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Request human approval for a single write-class action and block until
    it resolves (or times out). Returns ``(approved, reason, decided_by)``.

    This is the shared approval primitive: it registers a per-``request_id``
    Future on the execution runtime, writes the durable pending audit row
    (carrying any Action Supervisor verdict), publishes the ``hitl_pause`` card,
    and awaits ``POST /executions/{id}/approve``. It powers both the per-tool
    ask-gate (:func:`wrap_tools_with_permissions`) and non-tool call sites such
    as the wiki-publish workflow handler, so their approval UX and audit trail
    are identical.

    Fail-safe: when no approval channel is available (no execution port/runtime)
    it returns ``(False, ...)`` — a high-risk action is skipped rather than run
    unreviewed.
    """
    runtime = execution_port.get_runtime(execution_id) if (execution_port and execution_id) else None
    if not isinstance(runtime, dict) or not runtime:
        logger.warning(
            "tool_permissions: action '%s' has no approval channel; blocking", action_name,
        )
        return False, (
            f"Action '{action_name}' requires approval but no approval channel is available."
        ), None

    approvals = runtime.get("tool_approvals")
    if approvals is None:
        approvals = {}
        runtime["tool_approvals"] = approvals

    request_id = uuid.uuid4().hex
    decision_future: "asyncio.Future" = asyncio.get_event_loop().create_future()
    approvals[request_id] = decision_future
    safe_args = _json_safe(args or {})
    _summary = summary or _summarize_args(action_name, safe_args)

    # Action Supervisor review (inline). When enabled and the caller didn't
    # already supply a verdict, review the action so the risk tier + advisory
    # verdict are recorded and shown on the approval card.
    _enforce = False
    if supervisor_verdict is None:
        try:
            from app.core.supervision import action_supervisor as _sup
            if _sup.is_enabled():
                _ctx = runtime.get("agent_context") if isinstance(runtime, dict) else None
                _verdict = await _sup.review(
                    action_name, args or {}, execution_id=execution_id, context=_ctx,
                )
                risk_tier = risk_tier or _verdict.risk_tier
                supervisor_verdict = _verdict.decision
                supervisor_reasoning = _verdict.reasoning
                _enforce = not _sup.is_shadow_mode()
        except Exception as exc:  # noqa: BLE001 — supervisor must never break the gate
            logger.warning("tool_permissions: action supervisor review error (%s): %s", action_name, exc)

    await _record_approval_pending(
        execution_id, request_id, action_name, _summary,
        risk_tier=risk_tier,
        supervisor_verdict=supervisor_verdict,
        supervisor_reasoning=supervisor_reasoning,
    )

    # Enforcement (non-shadow): the Supervisor decides LOW-risk actions itself —
    # no human card. HIGH-risk actions, and any "escalate", always fall through
    # to the human card below (the verdict rides along as advice).
    if _enforce and risk_tier == "low" and supervisor_verdict in ("approve", "deny"):
        approvals.pop(request_id, None)
        _approved = supervisor_verdict == "approve"
        await _record_approval_decision(
            execution_id, request_id,
            "approved" if _approved else "denied",
            reason=supervisor_reasoning, decided_by="supervisor",
        )
        await _publish_supervisor_decision(
            execution_port, execution_id, action_name,
            supervisor_verdict, supervisor_reasoning, risk_tier,
        )
        logger.info(
            "tool_permissions: %s '%s' auto-%s by supervisor",
            risk_tier, action_name, "approved" if _approved else "denied",
        )
        return _approved, supervisor_reasoning, "supervisor"

    try:
        await execution_port.publish_hitl_pause(execution_id, {
            "request_id": request_id,
            "draft_answer": "",
            "message": _summary,
            "type": "tool_approval",
            "tool": action_name,
            "args": safe_args,
            "risk_tier": risk_tier,
            "supervisor_verdict": supervisor_verdict,
            "supervisor_reasoning": supervisor_reasoning,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_permissions: ASK %s publish failed (%s)", action_name, exc)

    logger.info("tool_permissions: ASK %s → awaiting approval (request_id=%s)", action_name, request_id)
    try:
        decision = await asyncio.wait_for(decision_future, timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.info("tool_permissions: ASK %s → approval timed out", action_name)
        await _record_approval_decision(
            execution_id, request_id, "timeout", reason="Approval timed out",
        )
        return False, f"Action '{action_name}' was not approved in time.", None
    finally:
        approvals.pop(request_id, None)

    approved = isinstance(decision, dict) and decision.get("approved") is True
    reason = decision.get("reason") if isinstance(decision, dict) else None
    decided_by = decision.get("decided_by") if isinstance(decision, dict) else None
    await _record_approval_decision(
        execution_id, request_id,
        "approved" if approved else "denied",
        reason=reason, decided_by=decided_by,
    )
    return approved, reason, decided_by


def wrap_tools_with_permissions(
    tools: List[Any],
    mode: PermissionMode = "default",
    execution_id: Optional[str] = None,
    execution_port: Any = None,
    ask_patterns=DEFAULT_ASK_PATTERNS,
    deny_patterns=DEFAULT_DENY_PATTERNS,
) -> List[Any]:
    """Wrap each tool with its permission behavior. Returns a new list.

    ``ask_patterns`` / ``deny_patterns`` default to the module constants so legacy
    callers are unchanged; the policy engine
    (:func:`app.core.policy.apply_to_tools`) passes resolved patterns instead.

    For ``ask`` tools, when an ``execution_port`` + ``execution_id`` are given the
    wrapper publishes a ``hitl_pause`` (approve/deny card in chat) and blocks on
    the execution's ``hitl_queue`` until the operator decides — reusing the same
    ``POST /executions/{id}/approve`` channel as supervisor HITL. Without that
    channel it fails safe (blocks the tool with an explanatory message).
    """
    wrapped: List[Any] = []
    for tool in tools:
        name = getattr(tool, "name", "") or ""
        mutates = classify_tool_mutation(tool)
        behavior = evaluate(
            name, mode, ask_patterns=ask_patterns, deny_patterns=deny_patterns,
            mutates=mutates,
        )
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
        async def _gated(*_a, __orig=original, __tool=tool, __name=name, **kwargs) -> Any:
            # Delegate the register→publish→await→audit dance to the shared
            # primitive so this gate and non-tool call sites (wiki publish, etc.)
            # behave identically. Returns (approved, reason, decided_by).
            approved, reason, _decided_by = await request_action_approval(
                execution_id, execution_port, __name, kwargs,
            )
            if not approved:
                return json.dumps({
                    "blocked": True, "tool": __name,
                    "reason": reason or f"Tool '{__name}' was not approved by the operator.",
                })
            # Tools that expose a plain `.coroutine` (StructuredTool) are invoked
            # directly. Tools that only implement `_arun` (MCPToolWrapper, several
            # builtin BaseTools like fs_write) have no `.coroutine`, so `__orig`
            # is None — fall back to the tool's own async invoke instead of
            # crashing with "'NoneType' object is not callable".
            if __orig is not None:
                return await __orig(*_a, **kwargs)
            return await __tool.ainvoke(kwargs)

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
            from app.config import settings
            if settings.compression_all_tools and isinstance(out, str):
                # Compress-then-cap through the shared helper. MCP wrappers are
                # excluded above (no coroutine), so no tool is ever compressed
                # twice. The cap is identical to the no-compression path.
                from app.core.context.tool_output import compress_then_cap
                return await compress_then_cap(
                    out,
                    lambda t: _cap_text(t, max_chars, __name),
                    tool_name=__name,
                )
            return _cap_text(out, max_chars, __name)

        capped.append(_clone_with_coroutine(tool, _capped))
    return capped
