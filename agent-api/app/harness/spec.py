"""AgentSpec — a declarative description of an agent to build/run.

The spec captures everything the LangGraph agent factory needs *except* the
runtime objects (llm, tools, checkpointer), which are passed alongside. Today it
unifies the two previously-duplicated ``build_agent(...)`` call sites in
``ReactStrategy`` (initial build + supervisor-retry rebuild) behind one source of
truth; Phase 2's later steps grow it into the full harness input (budgets,
permission policy, subagent depth).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentSpec:
    """Declarative inputs for building a ReAct agent.

    Derived from workflow node config (see ``spec_factory.build_agent_spec``).
    """

    agent_config: Dict[str, Any]
    has_cloudwatch: bool = False
    has_code_analyzer: bool = False
    permission_mode: str = "default"
    session_id: Optional[str] = None
    # Declarative governance policy set (see ``app.core.policy``). When None the
    # policy engine falls back to platform defaults (= pre-policy behaviour).
    policies: Optional[List[Dict[str, Any]]] = None
    # ── Configurable-agent layer (Phase 0+) ──────────────────────────────────
    # Extra composable capability ids beyond the runtime-derived investigation
    # trio (see ``app.harness.capabilities``). Empty = current behaviour.
    capabilities: List[str] = field(default_factory=list)
    # Profile-supplied full role-sentence override; None keeps the derived one.
    role_prompt: Optional[str] = None
    # Structured-output schema name (see ``output_registry``); None/"investigation"
    # keeps the InvestigationReport default.
    output_schema: Optional[str] = None
    # Deep-agent feature flags / definitions (wired in later phases). Off/empty by
    # default so the investigation path is unchanged.
    planning: bool = False
    filesystem: bool = False
    subagents: List[Dict[str, Any]] = field(default_factory=list)
    # Per-workflow capability toggles (default off). ``auto_learn`` gates the
    # post-run learning loop; ``sandbox`` wires the isolated run_command tool;
    # ``memory`` gates semantic recall + post-run capture and is driven by a
    # Memory node connected to the agent (see ``has_memory`` in spec_factory).
    auto_learn: bool = False
    sandbox: bool = False
    memory: bool = False
    # Edit→verify→fix loop (P1). ``verify_command`` is the operator-configured command
    # the ``run_verify`` tool runs against the real repo dir after an edit; empty
    # leaves the tool out entirely (no-op). image/timeout are optional overrides.
    verify_command: Optional[str] = None
    verify_image: Optional[str] = None
    verify_timeout: Optional[int] = None
    # Carried for completeness / future use by the loop engine.
    metadata: Dict[str, Any] = field(default_factory=dict)
