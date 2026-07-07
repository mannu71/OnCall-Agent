"""Agent Harness — the single ReAct agent runtime for the workflow platform.

  * ``envelopes`` — standardized ``ToolResult`` / ``NodeOutput`` observation
    envelopes with a uniform output cap.
  * ``registry_loader`` — startup population of the (previously orphaned)
    ``app.core.tool_registry.registry`` with built-in + MCP-discovered tools,
    exposed via ``GET /api/v1/tools``.

The in-house ``build_agent`` (``app.harness.react_agent``) is the sole agent
builder — the alternate agent-builder path has been removed.
"""
from typing import Any, List, Optional

from app.harness.envelopes import (
    NodeOutput,
    ToolError,
    ToolResult,
    ToolStatus,
    cap_text,
)
from app.harness.spec import AgentSpec


def build_agent_from_spec(
    spec: AgentSpec,
    llm: Any,
    tools: List[Any],
    checkpointer: Any = None,
    execution_port: Any = None,
) -> Any:
    """Build a LangGraph ReAct agent from an :class:`AgentSpec` + runtime objects.

    Single entry point that unpacks the spec into the underlying
    ``react_agent.build_agent`` call, so all call sites stay in sync.
    """
    from app.harness.react_agent import build_agent as _build_agent

    return _build_agent(
        llm,
        tools,
        spec.agent_config,
        has_cloudwatch=spec.has_cloudwatch,
        has_code_analyzer=spec.has_code_analyzer,
        checkpointer=checkpointer,
        session_id=spec.session_id,
        permission_mode=spec.permission_mode,
        execution_port=execution_port,
        policies=spec.policies,
        capabilities=spec.capabilities,
        role_prompt=spec.role_prompt,
        planning=spec.planning,
        filesystem=spec.filesystem,
        subagents=spec.subagents,
        sandbox=spec.sandbox,
        verify=bool(getattr(spec, "verify_command", None)),
    )


__all__ = [
    "AgentSpec",
    "NodeOutput",
    "ToolError",
    "ToolResult",
    "ToolStatus",
    "cap_text",
    "build_agent_from_spec",
]
