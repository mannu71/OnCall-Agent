"""Workflow node-graph dialect handling and save-time validation.

Two node dialects coexist (see the project memory ``project_node_dialects``):
legacy ReactFlow stores config under ``node.data.*`` (``codeAnalyzer``,
``cloudwatchAnalyzer``, ``logGroups`` …); the newer LangflowEditor stores it
under ``node.params.*`` (``code_search_tool``, ``cloudwatch_tool``, ``groups`` …).

This module provides:

  * :func:`get_node_param` — single dialect-agnostic accessor (params first,
    then data) so every reader uses one rule.
  * :func:`normalize_workflow_dialect` — NON-DESTRUCTIVE save-time normalization:
    guarantees ``params`` is populated from ``data`` where missing, while leaving
    ``data`` intact so existing legacy readers keep working ("normalize on save,
    accept both on read").
  * :func:`validate_workflow` — per-node-type required-field checks, reusing the
    existing dialect-aware readers where they already exist. Returns a list of
    human-readable errors (empty == valid); the API layer turns a non-empty list
    into a 400.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_node_param(node: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present value for *keys*, checking ``params`` then ``data``.

    Example: ``get_node_param(node, "serverName", "server_name")``.
    """
    params = node.get("params") or {}
    data = node.get("data") or {}
    for key in keys:
        if key in params and params[key] not in (None, ""):
            return params[key]
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def normalize_workflow_dialect(workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure every node's ``params`` is populated from legacy ``data`` (in place).

    Non-destructive: copies keys from ``data`` into ``params`` only when the key
    is absent from ``params``; never deletes ``data``. This means a workflow saved
    by the legacy editor becomes readable by ``params``-based readers without
    breaking anything that still reads ``data``.
    """
    nodes = workflow_dict.get("nodes")
    if not isinstance(nodes, list):
        return workflow_dict

    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict) or not data:
            continue
        params = node.get("params")
        if not isinstance(params, dict):
            params = {}
            node["params"] = params
        for key, value in data.items():
            params.setdefault(key, value)
    return workflow_dict


# Node type groupings (both dialects).
_AGENT_TYPES = ("agent",)
_LLM_TYPES = ("llm", "language_model")
_TOOL_TYPES = ("tool", "mcp_server")
_CLOUDWATCH_TYPES = ("cloudwatchAnalyzer", "cloudwatch_tool")
_CODE_TYPES = ("codeAnalyzer", "code_search_tool")


def _llm_node_has_model(node: Dict[str, Any]) -> bool:
    """Check if an LLM/language_model node has a model configured.

    Handles both dialects:
    - Scalar keys: params.model / params.modelId / params.llm / etc.
    - LangflowEditor multi-select: params.models (list of str or {name})
    - LangflowEditor comma-joined: params.llm "A,B"
    """
    # Check scalar keys first (legacy + anthropic_model / openai_model)
    scalar = get_node_param(
        node, "model", "modelId", "model_id", "modelName",
        "configName", "llmConfigId", "llm",
    )
    if scalar:
        return True
    # params.models list (LangflowEditor language_model node)
    params = node.get("params") or {}
    models = params.get("models")
    if isinstance(models, list):
        return any(
            (isinstance(m, str) and m.strip()) or
            (isinstance(m, dict) and m.get("name", "").strip())
            for m in models
        )
    return False


def _tool_node_has_server(node: Dict[str, Any]) -> bool:
    """Check if a tool/mcp_server node has an MCP server configured.

    Handles:
    - legacy tool node: params.serverName / params.server_name / params.name / params.command
    - LangflowEditor mcp_server node: params.servers (comma-joined or list)
    """
    command = get_node_param(node, "command")
    if command:
        return True
    # Scalar server name keys (legacy tool node)
    server = get_node_param(node, "serverName", "server_name", "name")
    if server:
        return True
    # LangflowEditor mcp_server: params.servers (comma-joined string or list)
    params = node.get("params") or {}
    servers = params.get("servers")
    if isinstance(servers, list):
        return any(s for s in servers if s)
    if isinstance(servers, str):
        return any(s.strip() for s in servers.split(","))
    return False


def validate_workflow(workflow_dict: Dict[str, Any]) -> List[str]:
    """Validate per-node required config. Returns a list of error strings."""
    errors: List[str] = []
    nodes = workflow_dict.get("nodes")
    if not isinstance(nodes, list):
        return errors  # structural emptiness handled elsewhere

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        label = (
            get_node_param(node, "label")
            or node.get("name")
            or f"{ntype or 'node'} {node.get('id', '?')}"
        )

        if ntype in _CLOUDWATCH_TYPES:
            if not _cloudwatch_has_groups(node):
                errors.append(f"CloudWatch node '{label}' has no log groups configured.")

        elif ntype in _CODE_TYPES:
            if not _code_has_repos(node):
                errors.append(f"Code analyzer node '{label}' has no repositories configured.")

        elif ntype in _TOOL_TYPES:
            if not _tool_node_has_server(node):
                errors.append(f"Tool node '{label}' has no MCP server selected.")

        elif ntype in _LLM_TYPES:
            if not _llm_node_has_model(node):
                errors.append(f"LLM node '{label}' has no model selected.")

    errors.extend(_validate_memory_wiring(workflow_dict))
    return errors


def _is_truthy(value: Any) -> bool:
    """Coerce a dialect param (bool or "true"/"false"/"1" string) to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _node_label(node: Dict[str, Any]) -> str:
    return (
        get_node_param(node, "label")
        or node.get("name")
        or f"{node.get('type') or 'node'} {node.get('id', '?')}"
    )


def _validate_memory_wiring(workflow_dict: Dict[str, Any]) -> List[str]:
    """Cross-node memory rules — the first connectivity-aware validation.

    Enforces the "attach a Memory node and pick types" contract:
      1. An agent with Auto-learn on MUST have a Memory node wired.
      2. A wired Memory node must have at least one memory type selected
         (an explicitly-emptied selection is rejected; an absent selection
         defaults to all types and is fine, for backward compatibility).
      3. Auto-learn writes to the semantic + KB tiers, so both must be selected
         on the connected Memory node.
    """
    errors: List[str] = []
    nodes = workflow_dict.get("nodes")
    if not isinstance(nodes, list):
        return errors

    # Lazy import to avoid any import-time cycle (this module loads early).
    try:
        from app.workflow.strategies.react.workflow_config import (
            get_memory_config,
            _read_memory_types,
        )
    except Exception as exc:  # noqa: BLE001 — never block save on an import hiccup
        logger.debug("validate_workflow: memory-config import failed (%s)", exc)
        return errors

    # Rule 2: every wired Memory node needs a non-empty type selection.
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "vector_memory":
            continue
        present, types = _read_memory_types(node)
        if present and not types:
            errors.append(
                f"Memory node '{_node_label(node)}': select at least one memory "
                "type (semantic, pinned, kb, or session)."
            )

    # Rules 1 & 3: agents with Auto-learn need a properly-typed Memory node.
    agent_nodes = [
        n for n in nodes
        if isinstance(n, dict) and n.get("type") == "agent"
        and _is_truthy(get_node_param(n, "autoLearn", "auto_learn"))
    ]
    if agent_nodes:
        mem = get_memory_config(workflow_dict)
        for agent in agent_nodes:
            label = _node_label(agent)
            if not mem.enabled:
                errors.append(
                    f"Agent '{label}' has Auto-learn enabled but no Memory node is "
                    "connected — attach a Memory node and select memory types."
                )
                continue
            missing = [t for t in ("semantic", "kb") if t not in mem.types]
            if missing:
                errors.append(
                    f"Agent '{label}' has Auto-learn enabled, which writes to the "
                    f"{' and '.join(missing)} memory tier(s) — enable "
                    f"{' and '.join(missing)} on the connected Memory node."
                )

    return errors


def _cloudwatch_has_groups(node: Dict[str, Any]) -> bool:
    """Reuse the canonical CloudWatch config reader to check for log groups."""
    try:
        from app.workflow.executor.handlers.cloudwatch import _read_cw_config
        cfg = _read_cw_config(node)
        return bool(cfg.get("log_groups"))
    except Exception as exc:  # noqa: BLE001 — fall back to a direct read
        logger.debug("validate_workflow: _read_cw_config failed (%s); direct read", exc)
        raw = get_node_param(node, "groups", "logGroups", default=[])
        if isinstance(raw, str):
            return any(g.strip() for g in raw.split(","))
        return bool(raw)


def _code_has_repos(node: Dict[str, Any]) -> bool:
    """Reuse the canonical code-analyzer repo reader (handles both dialects)."""
    try:
        from app.workflow.code_analyzer_config import read_code_analyzer_repos
        return bool(read_code_analyzer_repos(node))
    except Exception as exc:  # noqa: BLE001
        logger.debug("validate_workflow: read_code_analyzer_repos failed (%s)", exc)
        return bool(get_node_param(node, "repos", default=None))
