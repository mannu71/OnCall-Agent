"""Native turn-loop engine — loop state.

A single append-only message list is the loop's sole source of truth
(never reordered or removed); ``TurnLoopState`` bundles everything else that
changes across iterations so the loop can mutate it atomically at named
"continue sites" (mirroring the reference ReAct-loop pattern this
re-architecture follows). ``TokenLedger`` is the one place token counts are
accumulated, replacing the duplicated counting that used to live in
``supervisor_loop`` / ``TokenUsageCallback`` / the result dict separately.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, List, Optional


class ContinueReason(str, enum.Enum):
    """Why the loop is about to run another iteration — logged at the single
    mutation point (``continue_site``) so every extra turn is auditable."""

    TOOL_RESULTS = "tool_results"
    REACTIVE_COMPACT_RETRY = "reactive_compact_retry"
    MAX_OUTPUT_TOKENS_ESCALATE = "max_output_tokens_escalate"
    MAX_OUTPUT_TOKENS_RECOVERY = "max_output_tokens_recovery"
    COLLAPSE_DRAIN_RETRY = "collapse_drain_retry"
    TOKEN_BUDGET_CONTINUATION = "token_budget_continuation"
    MIDTHOUGHT_CONTINUATION = "midthought_continuation"


class StopReason(str, enum.Enum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    PROMPT_TOO_LONG = "prompt_too_long"
    MAX_TURNS = "max_turns"
    TOKEN_BUDGET = "token_budget"
    HITL_PAUSED = "hitl_paused"


class TerminalState(str, enum.Enum):
    """User/dashboard-facing outcome, derived from StopReason + supervisor
    verdict + verify status (see app.harness.terminal_state). Distinct from
    StopReason: StopReason is "why the loop stopped" (engine-internal);
    TerminalState is "was this actually a success" (what a caller/dashboard
    should trust). An exhausted or unverified run never self-reports
    ``success`` — see the loop-engineering discipline this maps to:
    "an error or an exhausted budget never counts as success"."""

    SUCCESS = "success"
    NO_OP = "no_op"
    BLOCKED = "blocked"
    STALLED = "stalled"
    EXHAUSTED = "exhausted"
    UNVERIFIED = "unverified"


@dataclass
class TokenLedger:
    """Wraps a single ``TokenUsageCallback`` — the one source of truth for
    token accounting across a native loop run (mirrors the LangGraph path's
    callback, so both engines report identical fields)."""

    callback: Any = None

    def __post_init__(self) -> None:
        if self.callback is None:
            from app.core.streaming.callbacks import TokenUsageCallback
            self.callback = TokenUsageCallback()

    @property
    def input_tokens(self) -> int:
        return self.callback.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.callback.output_tokens

    @property
    def cache_read_tokens(self) -> int:
        return self.callback.cache_read_tokens

    @property
    def cache_creation_tokens(self) -> int:
        return self.callback.cache_creation_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TurnLoopState:
    messages: List[Any]  # append-only source of truth; never reordered
    turn_count: int = 0
    continue_reason: Optional[ContinueReason] = None
    stop_reason: Optional[StopReason] = None
    has_attempted_reactive_compact: bool = False
    max_output_tokens_escalated: bool = False
    max_output_tokens_override: Optional[int] = None
    max_output_tokens_recovery_count: int = 0
    did_forced_synthesis: bool = False
    did_midthought_continuation: bool = False
    withheld_errors: List[Any] = field(default_factory=list)
    ledger: TokenLedger = field(default_factory=TokenLedger)
    truncated: bool = False
    # Environment-first verification (3.1): True from the turn an edit_file/
    # create_file call succeeds until run_verify is subsequently called (pass
    # or fail — being called at all clears "pending"). Only meaningful when
    # run_verify is actually bound (AgentSpec.verify_command configured);
    # stays False for runs that never touch an edit tool.
    verify_pending: bool = False
    verify_last_passed: Optional[bool] = None


__all__ = [
    "ContinueReason", "StopReason", "TerminalState", "TokenLedger", "TurnLoopState",
]
