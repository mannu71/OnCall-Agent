"""On-demand subagent delegation (claude-code "spawn" pattern).

Exposes a single agent-callable tool, ``delegate_investigation``, that runs a
SCOPED sub-question on an independent, bounded sub-agent and returns only a
concise summary. This keeps the parent's context focused: instead of inlining
every find/read/trace step for a side-branch, the parent delegates it and gets
back just the conclusion (the sub-agent's own multi-step loop happens off to the
side and its intermediate tool noise never enters the parent's history).

Bounds:
  * **Depth 1** — the sub-agent is built WITHOUT the delegate tool, so it cannot
    spawn further subagents (no unbounded fan-out).
  * **auto_allow** — the sub-agent runs with permission gating disabled (the
    parent's delegate call is itself gated as an ``ask`` tool, so approval is
    requested once, at the parent level).

Reuses ``build_agent`` + ``execute_agent`` so the sub-agent has the same tools,
model, recursion-recovery and checkpointer behaviour as a top-level run.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Token optimization: the sub-agent's multi-step tool noise stays off to the
# side; the parent only ingests this concise summary, capped here so a verbose
# sub-run can't bloat the parent's per-iteration context. (claude-code fork
# "report only the essentials" pattern.)
_DELEGATE_OUTPUT_MAX = 8000


def build_delegate_tool(
    llm: Any,
    base_tools: List[Any],
    agent_config: dict,
    parent_execution_id: Optional[str] = None,
) -> Any:
    """Build the ``delegate_investigation`` StructuredTool.

    Args:
        llm:          The same chat model the parent uses (sub-agent inherits it).
        base_tools:   Snapshot of the parent's tools; the delegate tool itself is
                      filtered out so the sub-agent is depth-1.
        agent_config: Agent node config (instructions etc.) — reused for the sub.
        parent_execution_id: For namespacing the sub-run's thread id.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    class _DelegateInput(BaseModel):
        subtask: str = PydanticField(
            description="The focused sub-question to investigate independently.")
        repo: Optional[str] = PydanticField(
            default=None, description="Repository to scope the sub-investigation to.")
        focus: Optional[str] = PydanticField(
            default=None, description="Optional extra focus / hint for the sub-agent.")

    # Depth-1: the sub-agent never receives the delegate tool.
    _sub_tools = [t for t in base_tools if getattr(t, "name", "") != "delegate_investigation"]

    async def _delegate(subtask: str, repo: Optional[str] = None, focus: Optional[str] = None) -> str:
        from app.harness import AgentSpec, build_agent_from_spec
        from app.workflow.strategies.react.agent_runner import execute_agent
        from app.workflow.strategies.react.hitl import make_checkpointer

        q = (subtask or "").strip()
        if not q:
            return json.dumps({"error": "empty subtask"})
        if repo:
            q += f"\n(Repository: {repo})"
        if focus:
            q += f"\n(Focus: {focus})"

        sub_id = f"{parent_execution_id or 'sub'}-{uuid.uuid4().hex[:8]}"
        logger.info("delegate_investigation: spawning sub-agent %s for: %s", sub_id, q[:80])

        # Subagent model precedence (node-level gateway):
        #   1. a model wired to the agent's "subagent" port (per-workflow) —
        #      stashed by the strategy on agent_config["subagent_llm_config"];
        #   2. the global "subagent" role assignment (fallback);
        #   3. reuse the parent's LLM instance.
        # Any failure falls back to the parent LLM — a bad sub-LLM must not break
        # delegation.
        sub_llm = llm
        try:
            from app.workflow.strategies.react.llm_factory import build_llm
            wired_cfg = (
                agent_config.get("subagent_llm_config")
                if isinstance(agent_config, dict) else None
            )
            if wired_cfg:
                sub_llm = build_llm(wired_cfg)
                logger.info(
                    "delegate_investigation: sub-agent %s using wired subagent model=%s",
                    sub_id, wired_cfg.get("model"),
                )
            else:
                from app.infrastructure.persistence import model_role_repository
                if await model_role_repository.get("subagent"):
                    from app.workflow.llm_config import resolve_llm_config_for_role
                    sub_cfg = await resolve_llm_config_for_role("subagent")
                    sub_llm = build_llm(sub_cfg)
                    logger.info(
                        "delegate_investigation: sub-agent %s using role model=%s",
                        sub_id, sub_cfg.get("model"),
                    )
                else:
                    logger.info(
                        "delegate_investigation: sub-agent %s reusing parent LLM", sub_id
                    )
        except Exception as exc:  # noqa: BLE001 — fall back to parent LLM
            logger.warning(
                "delegate_investigation: sub-agent %s LLM resolution failed (%s) — using parent LLM",
                sub_id, exc,
            )
            sub_llm = llm

        try:
            cp = await make_checkpointer()
            # Spawn the depth-1 sub-agent THROUGH the harness facade — same entry
            # point as a top-level run, with a child spec (auto_allow because the
            # parent's delegate call was already gated).
            # Strip the parent's stashed subagent config from the child spec — it
            # carries resolved credentials and is irrelevant to a depth-1 sub-run
            # (which can't delegate further anyway).
            sub_agent_config = (
                {k: v for k, v in agent_config.items() if k != "subagent_llm_config"}
                if isinstance(agent_config, dict) else agent_config
            )
            sub_spec = AgentSpec(
                agent_config=sub_agent_config,
                has_code_analyzer=True,
                permission_mode="auto_allow",
                session_id=sub_id,
            )
            sub_agent = build_agent_from_spec(sub_spec, sub_llm, _sub_tools, checkpointer=cp)
            result = await execute_agent(
                sub_agent, q, logger, execution_id=sub_id, thread_id=sub_id,
            )
            answer = (result.get("final_answer") or "").strip() or "(sub-investigation produced no answer)"
            payload = json.dumps(
                {
                    "subtask": subtask,
                    "answer": answer,
                    "sub_tool_calls": len(result.get("tool_calls", []) or []),
                },
                default=str,
            )
            return payload[:_DELEGATE_OUTPUT_MAX]
        except Exception as exc:  # noqa: BLE001 — a failed sub-run must not crash the parent
            logger.warning("delegate_investigation: sub-agent %s failed: %s", sub_id, exc)
            return json.dumps({"subtask": subtask, "error": str(exc)})

    return StructuredTool.from_function(
        coroutine=_delegate,
        name="delegate_investigation",
        description=(
            "Delegate a SCOPED sub-question to an independent sub-agent that runs its own "
            "find/read/trace loop and returns ONLY a concise summary. WHEN TO USE: a broad "
            "question with separable parts (e.g. several services / modules / areas) — delegate "
            "each part so your own context stays focused on synthesis. The sub-agent cannot "
            "delegate further (depth 1). WHEN NOT TO USE: a single direct lookup — just use "
            "crawler_find_symbol / crawler_search_semantic yourself."
        ),
        args_schema=_DelegateInput,
    )
