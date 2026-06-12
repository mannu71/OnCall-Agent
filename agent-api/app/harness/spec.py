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
from typing import Any, Dict, Optional


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
    # Carried for completeness / future use by the loop engine.
    metadata: Dict[str, Any] = field(default_factory=dict)
