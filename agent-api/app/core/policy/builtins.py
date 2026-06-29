"""Built-in policy handlers.

Each factory returns a :class:`~app.core.policy.types.Policy` that contributes
its effect into the resolve builder. Names here are the stable identifiers used
in declarative config (``{"type": "cost_budget", "params": {...}}``) and are the
allowlist enforced by the registry — unknown names are rejected, so config can
never inject an arbitrary callable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from app.core.tool_guardrails import ToolCallGuardrailConfig
from app.core.policy.types import Policy, _ResolveBuilder

# OS / shell / filesystem-mutating tools that should require approval. Kept here
# (not in tool_permissions) because it is a *policy* choice, not a hard default.
_OS_TOOL_PATTERNS: Tuple[str, ...] = (
    "run_command",
    "terminal",
    "execute_code",
    "process",
    "edit_file",
    "create_file",
    "apply_patch",
    "*_write",
    "*_delete",
)


@dataclass
class _AskOnOsTools:
    name: str = "ask_on_os_tools"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.add_ask(*_OS_TOOL_PATTERNS)


@dataclass
class _AskTools:
    patterns: Tuple[str, ...]
    name: str = "ask_tools"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.add_ask(*self.patterns)


@dataclass
class _DenyTools:
    patterns: Tuple[str, ...]
    name: str = "deny_tools"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.add_deny(*self.patterns)


@dataclass
class _AllowTools:
    patterns: Tuple[str, ...]
    name: str = "allow_tools"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.add_allow(*self.patterns)


@dataclass
class _CostBudget:
    max_cost_usd: float
    ask_thresholds_usd: Tuple[float, ...] = ()
    name: str = "cost_budget"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.budget_cost_usd = float(self.max_cost_usd)
        for t in self.ask_thresholds_usd:
            builder.ask_cost_thresholds_usd.append(float(t))


@dataclass
class _MaxToolCalls:
    limit: int
    name: str = "max_tool_calls_per_session"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.max_tool_calls = int(self.limit)


@dataclass
class _OutputCap:
    max_chars: int
    name: str = "output_cap"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.output_cap_chars = int(self.max_chars)


@dataclass
class _LoopGuardrails:
    hard_stop: bool = False
    exact_failure_block_after: int = 5
    same_tool_failure_halt_after: int = 8
    no_progress_block_after: int = 5
    name: str = "loop_guardrails"

    def contribute(self, builder: _ResolveBuilder) -> None:
        builder.guardrail_config = ToolCallGuardrailConfig(
            hard_stop_enabled=self.hard_stop,
            exact_failure_block_after=self.exact_failure_block_after,
            same_tool_failure_halt_after=self.same_tool_failure_halt_after,
            no_progress_block_after=self.no_progress_block_after,
        )


# ── Factory helpers (name → callable building a Policy from params) ────────────

def ask_on_os_tools() -> Policy:
    return _AskOnOsTools()


def ask_tools(patterns: List[str]) -> Policy:
    return _AskTools(patterns=tuple(patterns))


def deny_tools(patterns: List[str]) -> Policy:
    return _DenyTools(patterns=tuple(patterns))


def allow_tools(patterns: List[str]) -> Policy:
    return _AllowTools(patterns=tuple(patterns))


def cost_budget(max_cost_usd: float, ask_thresholds_usd: List[float] | None = None) -> Policy:
    return _CostBudget(
        max_cost_usd=max_cost_usd,
        ask_thresholds_usd=tuple(ask_thresholds_usd or ()),
    )


def max_tool_calls_per_session(limit: int) -> Policy:
    return _MaxToolCalls(limit=limit)


def output_cap(max_chars: int) -> Policy:
    return _OutputCap(max_chars=max_chars)


def loop_guardrails(
    hard_stop: bool = False,
    exact_failure_block_after: int = 5,
    same_tool_failure_halt_after: int = 8,
    no_progress_block_after: int = 5,
) -> Policy:
    return _LoopGuardrails(
        hard_stop=hard_stop,
        exact_failure_block_after=exact_failure_block_after,
        same_tool_failure_halt_after=same_tool_failure_halt_after,
        no_progress_block_after=no_progress_block_after,
    )
