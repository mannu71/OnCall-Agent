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


class AgentHarness:
    """Cohesive facade over the ReAct agent runtime.

    Phase 2 introduces the seam: a single place to *build* an agent from an
    :class:`AgentSpec` (replacing duplicated ``build_agent`` call sites) and to
    *run* the bounded supervisor loop. It delegates to the proven
    ``app.workflow.strategies.react`` internals rather than relocating them, so
    the 100%-accuracy loop is preserved while callers gain one entry point.
    """

    def build_agent(
        self,
        spec: AgentSpec,
        llm: Any,
        tools: List[Any],
        checkpointer: Any = None,
        execution_port: Any = None,
    ) -> Any:
        """Build a LangGraph ReAct agent from a spec + runtime objects."""
        from app.workflow.strategies.react.agent_builder import build_agent as _build_agent

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
        )


# Module-level singleton — stateless, safe to share.
harness = AgentHarness()

__all__ = [
    "AgentHarness",
    "AgentSpec",
    "NodeOutput",
    "ToolError",
    "ToolResult",
    "ToolStatus",
    "cap_text",
    "harness",
]
