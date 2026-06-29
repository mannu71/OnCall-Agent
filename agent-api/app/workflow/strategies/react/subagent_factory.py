"""Generalized, named subagents (deepagents ``subagents`` / Agent SDK
``AgentDefinition``).

Where ``subagent.py`` exposes a single hard-coded ``delegate_investigation`` tool,
this builds one ``delegate_to_<name>`` tool per definition declared on an agent
profile (``AgentSpec.subagents``). Each subagent runs the SAME harness with a
fresh context window — its own role prompt, capability set and tool subset — and
returns only a concise summary to the parent, the context-hygiene property the
deep-agent docs stress.

Bounds:
  * **Depth cap** (default 1): subagents are built WITHOUT any delegate_* tool, so
    they cannot fan out further.
  * **auto_allow**: the parent's ``delegate_to_<name>`` call is itself gated, so
    approval is requested once at the parent level.

A subagent definition is a dict::

    {"name": "code-specialist",          # required; -> delegate_to_code_specialist
     "description": "…",                 # tool description (when to use it)
     "role_prompt": "You are …",         # role-sentence override for the sub
     "capabilities": ["code_analyzer"],  # extra capability ids
     "tools": ["crawler_*", "fs_*"],     # optional fnmatch allow-list of tool names
     "output_schema": "generic"}         # optional structured-output schema
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SUB_OUTPUT_MAX = 8000


def _tool_name(t: Any) -> str:
    return getattr(t, "name", "") or ""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return s or "subagent"


def build_subagent_tools(
    llm: Any,
    base_tools: List[Any],
    agent_config: Dict[str, Any],
    subagent_defs: List[Dict[str, Any]],
    *,
    parent_execution_id: Optional[str] = None,
    depth_remaining: int = 1,
) -> List[Any]:
    """Build one ``delegate_to_<name>`` StructuredTool per definition."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    if depth_remaining <= 0 or not subagent_defs:
        return []

    # Subagents never receive delegate_* tools (depth cap / no fan-out).
    spawnable = [t for t in base_tools if not _tool_name(t).startswith("delegate_")]

    class _SubInput(BaseModel):
        task: str = PydanticField(description="The focused task to hand to this subagent.")
        context: Optional[str] = PydanticField(
            default=None, description="Optional extra context / hint for the subagent.")

    tools: List[Any] = []
    for _def in subagent_defs:
        if not isinstance(_def, dict) or not _def.get("name"):
            continue
        name = _slug(_def["name"])
        role_prompt = _def.get("role_prompt") or None
        capabilities = _def.get("capabilities") or []
        output_schema = _def.get("output_schema") or None
        tool_globs = _def.get("tools") or None
        description = _def.get("description") or (
            f"Delegate a scoped task to the '{name}' subagent, which runs its own loop "
            f"and returns a concise summary. Use for a separable part of a larger task."
        )

        # Per-subagent tool subset (fnmatch allow-list); default = all spawnable.
        if tool_globs:
            sub_tools = [
                t for t in spawnable
                if any(fnmatch.fnmatch(_tool_name(t), g) for g in tool_globs)
            ]
        else:
            sub_tools = list(spawnable)

        tools.append(
            _make_one(
                StructuredTool, _SubInput, llm, sub_tools, agent_config, name,
                role_prompt, capabilities, output_schema, description,
                parent_execution_id, depth_remaining,
            )
        )
    return tools


def _make_one(
    StructuredTool, _SubInput, llm, sub_tools, agent_config, name, role_prompt,
    capabilities, output_schema, description, parent_execution_id, depth_remaining,
):
    async def _delegate(task: str, context: Optional[str] = None) -> str:
        from app.harness import AgentSpec, build_agent_from_spec
        from app.workflow.strategies.react.agent_runner import execute_agent
        from app.workflow.strategies.react.hitl import make_checkpointer

        q = (task or "").strip()
        if not q:
            return json.dumps({"error": "empty task"})
        if context:
            q += f"\n(Context: {context})"

        sub_id = f"{parent_execution_id or 'sub'}-{name}-{uuid.uuid4().hex[:6]}"
        logger.info("delegate_to_%s: spawning subagent %s", name, sub_id)

        # Reuse parent LLM (a wired subagent model could be resolved here later).
        sub_agent_config = (
            {k: v for k, v in agent_config.items() if k != "subagent_llm_config"}
            if isinstance(agent_config, dict) else (agent_config or {})
        )
        try:
            cp = await make_checkpointer()
            sub_spec = AgentSpec(
                agent_config=sub_agent_config,
                permission_mode="auto_allow",
                session_id=sub_id,
                capabilities=list(capabilities),
                role_prompt=role_prompt,
                output_schema=output_schema,
                # Hand down the remaining depth budget minus this hop.
                subagents=[],  # depth-capped: no further fan-out
            )
            sub_agent = build_agent_from_spec(sub_spec, llm, sub_tools, checkpointer=cp)
            result = await execute_agent(
                sub_agent, q, logger, execution_id=sub_id, thread_id=sub_id,
            )
            answer = (result.get("final_answer") or "").strip() \
                or "(subagent produced no answer)"
            return json.dumps(
                {
                    "subagent": name,
                    "task": task,
                    "answer": answer,
                    "sub_tool_calls": len(result.get("tool_calls", []) or []),
                },
                default=str,
            )[:_SUB_OUTPUT_MAX]
        except Exception as exc:  # noqa: BLE001 — a failed sub-run must not crash the parent
            logger.warning("delegate_to_%s: subagent %s failed: %s", name, sub_id, exc)
            return json.dumps({"subagent": name, "task": task, "error": str(exc)})

    return StructuredTool.from_function(
        coroutine=_delegate,
        name=f"delegate_to_{name}",
        description=description,
        args_schema=_SubInput,
    )
