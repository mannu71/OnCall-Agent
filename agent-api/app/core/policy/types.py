"""Core types for the declarative policy engine.

The design is a small set of *named*, *parameterized* policy handlers that compose
into a single governance decision. Adapted to this platform: rather than evaluate
each action against a live handler chain, our policies **compile** (``contribute``)
into a :class:`ResolvedPolicy` — the concrete enforcement configuration the
existing machinery already consumes:

* tool gating → ``ask_patterns`` / ``deny_patterns`` (``react.tool_permissions``)
* output cap  → ``output_cap_chars``  (``react.tool_permissions.wrap_tools_with_output_cap``)
* loop guard  → ``guardrail_config``  (``core.tool_guardrails.ToolCallGuardrailController``)
* cost / call → ``budget_cost_usd`` / ``max_tool_calls`` (runtime checks)

This keeps the proven enforcement code (and the 100%-accuracy ReAct loop)
unchanged while giving operators one declarative place to say "for this workflow:
cap cost at $5, ask on shell, max 50 tool calls".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional, Protocol, Tuple

from app.core.tool_guardrails import ToolCallGuardrailConfig


class PolicyAction(str, Enum):
    """The three terminal decisions a policy can produce for an action.

    Mirrors the ``allow | ask | deny`` vocabulary already used by
    ``react.tool_permissions.evaluate``. The richer ``warn | block | halt``
    vocabulary for the loop guardrail lives in
    :class:`app.core.tool_guardrails.ToolGuardrailDecision` and is reused as-is.
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class RuntimeDecision:
    """Outcome of a runtime budget/quota check (cost, tool-call count)."""

    action: PolicyAction = PolicyAction.ALLOW
    reason: str = ""
    deciding_policy: str = ""

    @property
    def blocks(self) -> bool:
        return self.action is PolicyAction.DENY


@dataclass
class ResolvedPolicy:
    """The compiled enforcement configuration for one agent build.

    Defaults are deliberately empty/None so that an *empty* policy set, combined
    with :meth:`with_defaults`, reproduces today's behaviour exactly.
    """

    # Tool gating (fnmatch patterns consumed by react.tool_permissions).
    ask_patterns: Tuple[str, ...] = ()
    deny_patterns: Tuple[str, ...] = ()
    allow_patterns: Tuple[str, ...] = ()  # force-allow, wins over ask (not deny)

    # Action Supervisor risk tiers (fnmatch patterns). low → the Supervisor LLM
    # may auto-decide; high → escalate to a human (Supervisor verdict advisory).
    # Empty → fall back to the platform defaults / global settings CSVs.
    low_risk_patterns: Tuple[str, ...] = ()
    high_risk_patterns: Tuple[str, ...] = ()

    # Universal tool-result cap (chars). None → use platform default.
    output_cap_chars: Optional[int] = None

    # Cost / quota ceilings (advisory; enforced by runtime helpers).
    budget_cost_usd: Optional[float] = None
    ask_cost_thresholds_usd: Tuple[float, ...] = ()
    max_tool_calls: Optional[int] = None

    # Loop guardrail configuration. None → ToolCallGuardrailConfig.from_env().
    guardrail_config: Optional[ToolCallGuardrailConfig] = None

    def effective_ask_patterns(self) -> Tuple[str, ...]:
        """Ask patterns minus any explicitly force-allowed pattern."""
        if not self.allow_patterns:
            return self.ask_patterns
        allow = set(self.allow_patterns)
        return tuple(p for p in self.ask_patterns if p not in allow)

    def with_defaults(
        self,
        *,
        default_ask_patterns: Tuple[str, ...],
        default_output_cap_chars: int,
        default_low_risk_patterns: Tuple[str, ...] = (),
        default_high_risk_patterns: Tuple[str, ...] = (),
    ) -> "ResolvedPolicy":
        """Merge platform defaults so an empty set is a no-op and policies are additive.

        The default ask patterns are always seeded as a *baseline* and any
        policy-added ask patterns extend them — so adding e.g. ``cost_budget`` or
        ``ask_on_os_tools`` never silently drops the safe default gates
        (``save_playbook``, ``*_update`` …). To force-allow a defaulted gate, use
        the ``allow_tools`` policy (subtracted in :meth:`effective_ask_patterns`).
        """
        merged_ask = list(default_ask_patterns)
        for p in self.ask_patterns:
            if p not in merged_ask:
                merged_ask.append(p)
        return replace(
            self,
            ask_patterns=tuple(merged_ask),
            output_cap_chars=(
                self.output_cap_chars
                if self.output_cap_chars is not None
                else default_output_cap_chars
            ),
            # Tiers are a straight fallback: a policy that set them wins, else the
            # platform defaults apply. (Not additive — tiers are a partition.)
            low_risk_patterns=self.low_risk_patterns or default_low_risk_patterns,
            high_risk_patterns=self.high_risk_patterns or default_high_risk_patterns,
        )


@dataclass
class _ResolveBuilder:
    """Mutable accumulator policies write into during ``contribute``."""

    ask_patterns: List[str] = field(default_factory=list)
    deny_patterns: List[str] = field(default_factory=list)
    allow_patterns: List[str] = field(default_factory=list)
    low_risk_patterns: List[str] = field(default_factory=list)
    high_risk_patterns: List[str] = field(default_factory=list)
    output_cap_chars: Optional[int] = None
    budget_cost_usd: Optional[float] = None
    ask_cost_thresholds_usd: List[float] = field(default_factory=list)
    max_tool_calls: Optional[int] = None
    guardrail_config: Optional[ToolCallGuardrailConfig] = None

    def add_ask(self, *patterns: str) -> None:
        for p in patterns:
            if p not in self.ask_patterns:
                self.ask_patterns.append(p)

    def add_deny(self, *patterns: str) -> None:
        for p in patterns:
            if p not in self.deny_patterns:
                self.deny_patterns.append(p)

    def add_allow(self, *patterns: str) -> None:
        for p in patterns:
            if p not in self.allow_patterns:
                self.allow_patterns.append(p)

    def add_low_risk(self, *patterns: str) -> None:
        for p in patterns:
            if p not in self.low_risk_patterns:
                self.low_risk_patterns.append(p)

    def add_high_risk(self, *patterns: str) -> None:
        for p in patterns:
            if p not in self.high_risk_patterns:
                self.high_risk_patterns.append(p)

    def finish(self) -> ResolvedPolicy:
        return ResolvedPolicy(
            ask_patterns=tuple(self.ask_patterns),
            deny_patterns=tuple(self.deny_patterns),
            allow_patterns=tuple(self.allow_patterns),
            low_risk_patterns=tuple(self.low_risk_patterns),
            high_risk_patterns=tuple(self.high_risk_patterns),
            output_cap_chars=self.output_cap_chars,
            budget_cost_usd=self.budget_cost_usd,
            ask_cost_thresholds_usd=tuple(sorted(self.ask_cost_thresholds_usd)),
            max_tool_calls=self.max_tool_calls,
            guardrail_config=self.guardrail_config,
        )


class Policy(Protocol):
    """A named, parameterized governance rule.

    Implementations contribute their effect into the shared
    :class:`_ResolveBuilder`; the registry composes many policies into one
    :class:`ResolvedPolicy`.
    """

    name: str

    def contribute(self, builder: _ResolveBuilder) -> None:  # pragma: no cover - protocol
        ...
