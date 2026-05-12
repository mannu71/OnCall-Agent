"""Tool-call loop guardrails for the ReAct agent.

Prevents the agent from spinning in repeated identical tool-call loops
by tracking call signatures per turn and returning warn / block / halt
decisions that the runtime can act on.

The controller is **intentionally side-effect free**: it tracks observations
and returns decisions.  Runtime code (``react.py``) decides whether a
decision becomes a warning injected into the next message, a synthetic
tool result, or a controlled halt.

Three detection modes
---------------------
1. **Exact failure** — the same tool+args hash failed N times.
   Warn after ``exact_failure_warn_after``, block after
   ``exact_failure_block_after``.

2. **Same-tool failure** — any variant of the same tool name keeps failing.
   Warn after ``same_tool_failure_warn_after``, halt after
   ``same_tool_failure_halt_after``.

3. **Idempotent no-progress** — a read-only tool (search, read_file, etc.)
   returns the identical result repeatedly.  Warn after
   ``no_progress_warn_after``, block after ``no_progress_block_after``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, FrozenSet, Mapping, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tool classification sets
# ─────────────────────────────────────────────────────────────────────────────

IDEMPOTENT_TOOL_NAMES: FrozenSet[str] = frozenset({
    # File-system reads
    "read_file",
    "search_files",
    "mcp_filesystem_read_file",
    "mcp_filesystem_read_text_file",
    "mcp_filesystem_read_multiple_files",
    "mcp_filesystem_list_directory",
    "mcp_filesystem_list_directory_with_sizes",
    "mcp_filesystem_directory_tree",
    "mcp_filesystem_get_file_info",
    "mcp_filesystem_search_files",
    # Web / search
    "web_search",
    "web_extract",
    "session_search",
    # Browser inspection (snapshot = read-only)
    "browser_snapshot",
    "browser_console",
    "browser_get_images",
    # Domain-specific read tools
    "cloudwatch_get_logs",
    "cloudwatch_describe_alarms",
    "search_code",
    "get_function",
    "get_callers",
    "query_database",
})

MUTATING_TOOL_NAMES: FrozenSet[str] = frozenset({
    "terminal",
    "execute_code",
    "write_file",
    "patch",
    "todo",
    "memory",
    "skill_manage",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_scroll",
    "browser_navigate",
    "send_message",
    "cronjob",
    "delegate_task",
    "process",
    # Domain-specific mutating tools
    "create_alert",
    "resolve_alert",
    "upsert_pattern",
})


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn tool-call loop detection.

    Warnings are on by default and never block execution.  Hard stops are
    opt-in — set ``hard_stop_enabled=True`` (or ``GUARDRAIL_HARD_STOP=true``
    env var) to enable circuit-breaker behaviour in production.
    """

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False

    # Exact same tool+args failure thresholds
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5

    # Same tool (any args) failure thresholds
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8

    # Idempotent tool returning identical result thresholds
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5

    idempotent_tools: FrozenSet[str] = field(
        default_factory=lambda: IDEMPOTENT_TOOL_NAMES
    )
    mutating_tools: FrozenSet[str] = field(
        default_factory=lambda: MUTATING_TOOL_NAMES
    )

    @classmethod
    def from_env(cls) -> "ToolCallGuardrailConfig":
        """Build config from environment variables (production override)."""
        import os
        hard_stop = os.getenv("GUARDRAIL_HARD_STOP", "false").lower() in {
            "1", "true", "yes", "on",
        }
        return cls(hard_stop_enabled=hard_stop)


# ─────────────────────────────────────────────────────────────────────────────
# Value types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCallSignature:
    """Stable, non-reversible identity for a (tool_name, args) pair."""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(
        cls,
        tool_name: str,
        args: Mapping[str, Any] | None,
    ) -> "ToolCallSignature":
        canonical = _canonical_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    """Decision returned by the guardrail controller.

    Actions:
        allow — proceed normally
        warn  — proceed but inject a warning into the next message
        block — do not execute; return a synthetic error result
        halt  — stop the agent turn entirely
    """

    action: str = "allow"          # allow | warn | block | halt
    code: str = "allow"            # machine-readable reason code
    message: str = ""              # human-readable guidance for the LLM
    tool_name: str = ""
    count: int = 0
    signature: Optional[ToolCallSignature] = None

    @property
    def allows_execution(self) -> bool:
        """True when the tool call should proceed (allow or warn)."""
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        """True when the turn must be stopped."""
        return self.action in {"block", "halt"}

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def canonical_tool_args(args: Mapping[str, Any]) -> str:
    """Return sorted, compact JSON for a tool arguments mapping."""
    if not isinstance(args, Mapping):
        raise TypeError(f"tool args must be a mapping, got {type(args).__name__}")
    return json.dumps(
        args,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """Heuristic classifier — used when callers don't pass an explicit ``failed`` flag.

    Returns:
        (failed: bool, annotation: str)
    """
    if result is None:
        return False, ""

    if tool_name == "terminal":
        import json as _json
        try:
            data = _json.loads(result)
            if isinstance(data, dict):
                exit_code = data.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    return True, f" [exit {exit_code}]"
        except (_json.JSONDecodeError, TypeError):
            pass
        return False, ""

    lower = result[:500].lower()
    if any(kw in lower for kw in ('"error"', '"failed"')):
        return True, " [error]"
    if result.startswith(("Error:", "Failed:", "Exception:", "[Tool Error]")):
        return True, " [error]"

    return False, ""


def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    """Build a synthetic tool-result JSON string for a blocked tool call.

    Used by ``react.py`` to insert a ToolMessage without actually executing
    the blocked tool, keeping the Anthropic API message sequence valid.
    """
    return json.dumps(
        {"error": decision.message, "guardrail": decision.to_metadata()},
        ensure_ascii=False,
    )


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    """Append inline guidance to a tool result string for warn/halt decisions."""
    if decision.action not in {"warn", "halt"} or not decision.message:
        return result
    label = (
        "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    )
    suffix = (
        f"\n\n[{label}: {decision.code}; "
        f"count={decision.count}; {decision.message}]"
    )
    return (result or "") + suffix


# ─────────────────────────────────────────────────────────────────────────────
# Controller
# ─────────────────────────────────────────────────────────────────────────────

class ToolCallGuardrailController:
    """Per-turn controller for repeated failed / non-progressing tool calls.

    Instantiate one controller per agent turn and call:
    - ``before_call()`` — may block before execution (idempotent no-progress
      and exact-failure checks with hard_stop_enabled).
    - ``after_call()``  — records the outcome and returns warn/halt decisions.
    - ``reset_for_turn()`` — clears all per-turn counters (call at turn start
      or when the agent gets a new human message).
    """

    def __init__(self, config: ToolCallGuardrailConfig | None = None) -> None:
        self.config = config or ToolCallGuardrailConfig.from_env()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        # Maps signature → (result_hash, repeat_count) for idempotent tools
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._halt_decision: Optional[ToolGuardrailDecision] = None

    @property
    def halt_decision(self) -> Optional[ToolGuardrailDecision]:
        """The most recent halt/block decision, or None."""
        return self._halt_decision

    # ── Before execution ──────────────────────────────────────────────────────

    def before_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
    ) -> ToolGuardrailDecision:
        """Check whether a tool call should be pre-emptively blocked.

        Only fires hard blocks when ``hard_stop_enabled=True``.
        """
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))

        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        # Exact failure block
        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"Blocked {tool_name}: the same call failed {exact_count} times "
                    "with identical arguments. Stop retrying unchanged — change "
                    "strategy or explain the blocker."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        # Idempotent no-progress block
        if self._is_idempotent(tool_name):
            record = self._no_progress.get(signature)
            if record is not None:
                _, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    decision = ToolGuardrailDecision(
                        action="block",
                        code="idempotent_no_progress_block",
                        message=(
                            f"Blocked {tool_name}: returned the same result "
                            f"{repeat_count} times. Use the result already provided "
                            "or try a different query."
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                    )
                    self._halt_decision = decision
                    return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    # ── After execution ───────────────────────────────────────────────────────

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
    ) -> ToolGuardrailDecision:
        """Record the outcome of a tool call and return a decision.

        Args:
            tool_name: Name of the tool that was called.
            args:      Arguments passed to the tool.
            result:    String output from the tool (may be an error message).
            failed:    Explicit failure flag.  When ``None``, heuristic
                       classifier is used (see ``classify_tool_failure``).

        Returns:
            ToolGuardrailDecision with action allow / warn / halt.
        """
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)

        if failed is None:
            failed, _ = classify_tool_failure(tool_name, result)

        # ── Failure path ──────────────────────────────────────────────────────
        if failed:
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._no_progress.pop(signature, None)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            # Hard halt on same-tool saturation
            if (
                self.config.hard_stop_enabled
                and same_count >= self.config.same_tool_failure_halt_after
            ):
                decision = ToolGuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    message=(
                        f"Stopped {tool_name}: failed {same_count} times this turn. "
                        "Choose a different approach instead of retrying the same path."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )
                self._halt_decision = decision
                logger.warning(
                    "guardrail: HALT — %s (tool=%s)", decision.code, tool_name
                )
                return decision

            # Exact failure warn
            if (
                self.config.warnings_enabled
                and exact_count >= self.config.exact_failure_warn_after
            ):
                logger.debug(
                    "guardrail: warn exact_failure — %s (count=%d)", tool_name, exact_count
                )
                return ToolGuardrailDecision(
                    action="warn",
                    code="repeated_exact_failure_warning",
                    message=(
                        f"{tool_name} failed {exact_count} times with identical arguments. "
                        "Inspect the error and change strategy instead of retrying unchanged."
                    ),
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                )

            # Same-tool failure warn
            if (
                self.config.warnings_enabled
                and same_count >= self.config.same_tool_failure_warn_after
            ):
                logger.debug(
                    "guardrail: warn same_tool_failure — %s (count=%d)", tool_name, same_count
                )
                return ToolGuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    message=(
                        f"{tool_name} failed {same_count} times this turn. "
                        "Change approach before retrying."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )

            return ToolGuardrailDecision(
                tool_name=tool_name, count=exact_count, signature=signature
            )

        # ── Success path ──────────────────────────────────────────────────────
        # Clear failure counters on success
        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        if not self._is_idempotent(tool_name):
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        # Idempotent tool — track result hash for no-progress detection
        r_hash = _result_hash(result)
        previous = self._no_progress.get(signature)
        repeat_count = 1
        if previous is not None and previous[0] == r_hash:
            repeat_count = previous[1] + 1
        self._no_progress[signature] = (r_hash, repeat_count)

        if (
            self.config.warnings_enabled
            and repeat_count >= self.config.no_progress_warn_after
        ):
            logger.debug(
                "guardrail: warn idempotent_no_progress — %s (count=%d)",
                tool_name, repeat_count,
            )
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_no_progress_warning",
                message=(
                    f"{tool_name} returned the same result {repeat_count} times. "
                    "Use the result already provided or change the query."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )

        return ToolGuardrailDecision(
            tool_name=tool_name, count=repeat_count, signature=signature
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _canonical_args(args: Mapping[str, Any]) -> str:
    return json.dumps(
        args,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _result_hash(result: str | None) -> str:
    """Hash a tool result for idempotent-no-progress detection.

    Parses JSON if possible (to normalise whitespace), falls back to
    raw string.
    """
    if result is None:
        return _sha256("")
    try:
        parsed = json.loads(result)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (json.JSONDecodeError, TypeError):
        canonical = result
    return _sha256(canonical)


def _coerce_args(args: Any) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
