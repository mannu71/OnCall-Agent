"""Convert an :class:`AgentSpecConfig` into the platform's workflow schema.

Produces a ``{nodes, edges}`` dict that the existing ReactFlow executor runs
unchanged: one ``agent`` node (instructions + policies), an optional
``language_model`` node wired to the agent's ``lm`` port, and one ``tool`` node
per declared MCP server wired to the agent. The dialects used here match
``app.workflow.strategies.react.workflow_config`` (``language_model``/``params``,
``sourceSlot``/``targetSlot``), so resolution behaves like a UI-built workflow.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.spec.types import AgentSpecConfig

_AGENT_ID = "agent"
_LLM_ID = "llm"


def import_spec_to_workflow(spec: AgentSpecConfig) -> Dict[str, Any]:
    """Return a workflow dict (name/description/nodes/edges) for *spec*."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    # ── Agent node ─────────────────────────────────────────────────────────────
    agent_data: Dict[str, Any] = {
        "instructions": spec.instructions,
        "agentMode": "single",
    }
    # Configurable-agent fields: a referenced profile supplies defaults at run
    # time; inline spec fields override it (mirrors node-config precedence).
    if spec.profile:
        agent_data["profile"] = spec.profile
    if spec.role_prompt:
        agent_data["rolePrompt"] = spec.role_prompt
    if spec.output_schema:
        agent_data["outputSchema"] = spec.output_schema
        agent_data["outputMode"] = "structured"
    if spec.capabilities:
        agent_data["capabilities"] = list(spec.capabilities)
    if spec.policies:
        agent_data["policies"] = spec.policies
    if spec.params:
        agent_data["params"] = dict(spec.params)
    if spec.llm and spec.llm.reasoning_effort:
        agent_data.setdefault("params", {})["reasoningEffort"] = spec.llm.reasoning_effort
    nodes.append({
        "id": _AGENT_ID,
        "type": "agent",
        "position": {"x": 400, "y": 100},
        "data": agent_data,
    })

    # ── Language Model node (optional) ─────────────────────────────────────────
    if spec.llm and spec.llm.model:
        llm_params: Dict[str, Any] = {"llm": spec.llm.model}
        if spec.llm.max_completion_tokens:
            llm_params["maxTokens"] = spec.llm.max_completion_tokens
        nodes.append({
            "id": _LLM_ID,
            "type": "language_model",
            "position": {"x": 100, "y": 100},
            "params": llm_params,
        })
        edges.append({
            "id": f"e-{_LLM_ID}-{_AGENT_ID}",
            "source": _LLM_ID,
            "target": _AGENT_ID,
            "sourceSlot": f"lm::{spec.llm.model}",
            "targetSlot": "lm",
        })

    # ── MCP tool nodes ─────────────────────────────────────────────────────────
    for i, tool in enumerate(spec.tools.mcp):
        node_id = f"tool-{i}"
        data: Dict[str, Any] = {"serverName": tool.name}
        if tool.command:
            data.update({"command": tool.command, "args": list(tool.args), "env": dict(tool.env)})
        if tool.url:
            data["url"] = tool.url
            if tool.headers:
                data["headers"] = dict(tool.headers)
        nodes.append({
            "id": node_id,
            "type": "tool",
            "position": {"x": 700, "y": 100 + i * 120},
            "data": data,
        })
        edges.append({
            "id": f"e-{node_id}-{_AGENT_ID}",
            "source": node_id,
            "target": _AGENT_ID,
        })

    return {
        "name": spec.name,
        "description": spec.description,
        "type": "workflow",
        "nodes": nodes,
        "edges": edges,
    }
