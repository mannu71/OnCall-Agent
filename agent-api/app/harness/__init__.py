"""Agent Harness — a cohesive runtime layer for the workflow platform.

Phase 1 (foundation) deliberately introduces only the *contract* pieces that the
later harness extraction builds on, without changing runtime behaviour:

  * ``envelopes`` — standardized ``ToolResult`` / ``NodeOutput`` observation
    envelopes with a uniform output cap.
  * ``registry_loader`` — startup population of the (previously orphaned)
    ``app.core.tool_registry.registry`` with built-in + MCP-discovered tools,
    exposed via ``GET /api/v1/tools``.

The agent loop itself still lives in ``app.workflow.strategies.react``; Phase 2
consolidates ``agent_builder`` + ``agent_runner`` + ``tool_permissions`` + ``hitl``
+ ``subagent`` behind this package. See the plan at
``~/.claude/plans/i-am-building-a-enchanted-neumann.md``.
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
    ``agent_builder.build_agent`` call, so the four-or-so call sites stay in sync.
    """
    # Official-stack path: when the deepagents harness is selected, build the
    # agent with deepagents instead of the in-house ReAct wrapper. Same Bedrock
    # model + tools. The choice is per-node (``spec.harness``, set from the agent
    # node's Harness dropdown) and falls back to the global ``settings.harness``.
    try:
        from app.harness.spec_factory import resolve_harness
        _effective = spec.harness or resolve_harness(spec.agent_config)
        if _effective == "deepagents":
            from app.harness.deep_agent import build_deep_agent
            return build_deep_agent(
                spec, llm, tools, checkpointer=checkpointer, execution_port=execution_port,
            )
    except Exception as _da_exc:  # noqa: BLE001 — never break legacy on flag check
        import logging
        logging.getLogger(__name__).warning(
            "deepagents harness build failed (%s) — using legacy", _da_exc
        )

    from app.legacy.react_agent import build_agent as _build_agent

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
