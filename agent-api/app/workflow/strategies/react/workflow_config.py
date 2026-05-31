"""Workflow graph config extraction for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.workflow.llm_config import LLM_NODE_TYPES, resolve_llm_config
from app.workflow.executor.handlers.cloudwatch import _read_cw_config

logger = logging.getLogger(__name__)

def extract_agent_config(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Extract agent node configuration from workflow."""
    nodes = workflow.get("nodes", [])
    agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
    return agent_node.get("data", {}) if agent_node else {}

async def resolve_llm_config_for_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the LLM config for *workflow*.

    Delegates to :func:`app.workflow.llm_config.resolve_llm_config`
    which implements a clean three-stage pipeline: node-schema
    normalisation → source resolution (inline / named DB / default
    DB) → credential enrichment (API key or AWS Bedrock).
    """
    return await resolve_llm_config(workflow)

def get_connected_node_ids(
    workflow: Dict[str, Any],
    target_type: str,
) -> List[str]:
    """Return IDs of nodes of *target_type* connected to any ``agent`` node.

    Uses undirected BFS across the workflow edges so that connection is
    detected regardless of edge direction.  This is the single source of
    truth for "is this node wired to the agent?".

    Args:
        workflow: Full workflow definition (nodes + edges).
        target_type: The ``type`` value to look for (e.g. ``"tool"``,
            ``"cloudwatchAnalyzer"``).

    Returns:
        List of node IDs of type *target_type* reachable from at least one
        agent node.
    """
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])

    agent_ids = {n["id"] for n in nodes if n.get("type") == "agent"}
    target_ids = {n["id"] for n in nodes if n.get("type") == target_type}

    if not agent_ids or not target_ids:
        return []

    # Build undirected adjacency.
    neighbours: Dict[str, set] = {}
    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src and tgt:
            neighbours.setdefault(src, set()).add(tgt)
            neighbours.setdefault(tgt, set()).add(src)

    # BFS from every agent node.
    visited: set = set()
    queue = list(agent_ids)
    connected: List[str] = []

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current in target_ids:
            connected.append(current)
        for neighbour in neighbours.get(current, set()):
            if neighbour not in visited:
                queue.append(neighbour)

    return connected

def extract_tools_config(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract tool node configurations from workflow.

    Only tool nodes connected to the agent via edges are included.
    This prevents stray/unconnected tool nodes from being registered
    on the agent.
    """
    nodes = workflow.get("nodes", [])
    connected_ids = set(get_connected_node_ids(workflow, "tool"))

    tool_nodes = [
        n for n in nodes
        if n.get("type") == "tool" and n.get("id") in connected_ids
    ]

    if not tool_nodes:
        # Backwards-compat: if the graph has no edges at all (e.g. a
        # minimal/legacy workflow), fall back to including all tool nodes
        # so existing workflows don't break silently.
        edges = workflow.get("edges", [])
        if not edges:
            tool_nodes = [n for n in nodes if n.get("type") == "tool"]
            if tool_nodes:
                logger.info(
                    "ReactStrategy: no edges in workflow — falling back to all %d tool node(s)",
                    len(tool_nodes),
                )

    tools = []
    for tool_node in tool_nodes:
        tool_data = tool_node.get("data", {})
        server_name = tool_data.get("serverName", "")
        node_id = tool_node.get("id", server_name)

        if tool_data.get("command"):
            tools.append({
                "node_id": node_id,
                "name": server_name,
                "command": tool_data.get("command", ""),
                "args": tool_data.get("args", []),
                "env": tool_data.get("env", {}),
            })
        else:
            # Will be resolved from DB below in _setup_tools
            tools.append({
                "node_id": node_id,
                "name": server_name,
                "command": None,
                "args": [],
                "env": {},
            })

    return tools

def extract_cloudwatch_config(
    workflow: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Extract CloudWatch config from CW nodes connected to the agent node.

    Recognises both node types:

    * ``cloudwatchAnalyzer`` (legacy) — config in ``node.data`` with
      camelCase keys.
    * ``cloudwatch_tool`` (new LangflowEditor) — config in ``node.params``
      with snake_case keys.

    Returns a config only when at least one CW node is reachable from an
    ``agent`` node via the workflow's edges, so CW tools are registered
    only when explicitly wired up.

    Returns:
        Merged CloudWatch config dict, or ``None`` if no CW node is
        connected to the agent.
    """
    from app.workflow.executor.handlers.cloudwatch import _read_cw_config

    nodes = workflow.get("nodes", [])

    # Accept either node type. Merge the two ID lists, preserving order.
    connected_legacy = get_connected_node_ids(workflow, "cloudwatchAnalyzer")
    connected_new = get_connected_node_ids(workflow, "cloudwatch_tool")
    connected_cw_ids = list(connected_legacy) + [
        i for i in connected_new if i not in connected_legacy
    ]

    if not connected_cw_ids:
        return None

    cw_nodes = {
        n["id"]: n for n in nodes
        if n.get("type") in ("cloudwatchAnalyzer", "cloudwatch_tool")
    }

    # Merge configs from all connected CW nodes.
    merged_log_groups: List[str] = []
    merged_region = "us-east-1"
    merged_profile: Optional[str] = None
    merged_excludes: List[str] = []

    for cw_id in connected_cw_ids:
        cfg = _read_cw_config(cw_nodes[cw_id])
        for lg in cfg["log_groups"]:
            if lg and lg not in merged_log_groups:
                merged_log_groups.append(lg)
        if cfg["aws_region"]:
            merged_region = cfg["aws_region"]
        if cfg["aws_profile"]:
            merged_profile = cfg["aws_profile"]
        for ex in cfg.get("severity_excludes") or []:
            if ex and ex not in merged_excludes:
                merged_excludes.append(ex)

    logger.info(
        "ReactStrategy: %d CW node(s) connected to agent "
        "(legacy=%d, new=%d), log_groups=%s",
        len(connected_cw_ids), len(connected_legacy), len(connected_new),
        merged_log_groups,
    )

    return {
        "log_groups": merged_log_groups,
        "aws_region": merged_region,
        "aws_profile": merged_profile,
        "severity_excludes": merged_excludes,
    }

def extract_code_analyzer_config(
    workflow: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Extract Code Analyzer config from codeAnalyzer nodes connected to the agent.

    Merges repo lists from all connected ``codeAnalyzer`` nodes, deduplicating
    by repo name.  First occurrence (BFS order) wins on name conflicts.

    Returns:
        Dict with ``repos`` list, or ``None`` if no codeAnalyzer node is connected.
    """
    nodes = workflow.get("nodes", [])
    connected_ids = get_connected_node_ids(workflow, "codeAnalyzer")

    if not connected_ids:
        return None

    ca_nodes = {n["id"]: n for n in nodes if n.get("type") == "codeAnalyzer"}

    merged_repos: List[Dict[str, Any]] = []
    seen_names: Dict[str, str] = {}  # name → first path (for conflict detection)

    for ca_id in connected_ids:
        if ca_id not in ca_nodes:
            continue
        ca_data = ca_nodes[ca_id].get("data", {})
        for repo in ca_data.get("repos", []):
            rname = repo.get("name", "")
            rpath = repo.get("path", "")
            if not rname:
                continue
            if rname in seen_names:
                if seen_names[rname] != rpath:
                    logger.warning(
                        "ReactStrategy: duplicate repo name %r in codeAnalyzer nodes "
                        "— first occurrence (path=%r) wins; ignoring path=%r",
                        rname, seen_names[rname], rpath,
                    )
                continue
            seen_names[rname] = rpath
            merged_repos.append(repo)

    if not merged_repos:
        return None

    logger.info(
        "ReactStrategy: %d codeAnalyzer node(s) connected to agent, repos=%s",
        len(connected_ids),
        [r.get("name") for r in merged_repos],
    )

    return {"repos": merged_repos}
