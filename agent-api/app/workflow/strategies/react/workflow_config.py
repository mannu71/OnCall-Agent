"""Workflow graph config extraction for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.workflow.llm_config import (
    LLM_NODE_TYPES,
    resolve_llm_config,
    resolve_llm_config_for_consumer_port,
)
from app.workflow.executor.handlers.cloudwatch import _read_cw_config

logger = logging.getLogger(__name__)

def extract_agent_config(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Extract agent node configuration from workflow.

    Nodes ship in two dialects: legacy config lives under ``node['data']`` while
    the LangflowEditor writes config to the node's top-level ``node['params']``.
    A ``params``-dialect agent node has no ``data`` at all, so reading only
    ``data`` silently dropped the ENTIRE agent profile (subagents, planning,
    role prompt, instructions, output schema) — e.g. a squad's ``subagents`` was
    never seen, so no delegate tool was built and squad tools leaked onto the
    main agent. Merge both dialects: ``data`` wins on key conflicts (explicit
    legacy override) and a ``params`` key is preserved so callers that look one
    level deeper (``resolve_profile_fields`` reads ``cfg['params'][k]``) keep
    working.
    """
    nodes = workflow.get("nodes", [])
    agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
    if not agent_node:
        return {}
    data = agent_node.get("data") or {}
    params = agent_node.get("params") or {}
    if not params:
        return data
    merged = {**params, **data}
    merged.setdefault("params", params)
    return merged

def find_agent_node_id(workflow: Dict[str, Any]) -> Optional[str]:
    """Return the id of the first ``agent`` node (or None)."""
    for n in workflow.get("nodes", []) or []:
        if n.get("type") == "agent":
            return n.get("id")
    return None

def find_code_analyzer_node_id(workflow: Dict[str, Any]) -> Optional[str]:
    """Return the id of the first Code Crawler node (or None).

    Accepts both dialects (``code_search_tool`` / ``codeAnalyzer``). Used to
    resolve the model wired to the Code Crawler node's ``lm`` port.
    """
    from app.workflow.code_analyzer_config import CODE_ANALYZER_NODE_TYPES
    for n in workflow.get("nodes", []) or []:
        if n.get("type") in CODE_ANALYZER_NODE_TYPES:
            return n.get("id")
    return None

async def resolve_llm_config_for_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the agent's main LLM config for *workflow*.

    Node-level gateway: if a Language Model node is wired to the agent's ``lm``
    port, that model wins (per-workflow source of truth). Otherwise fall back to
    :func:`app.workflow.llm_config.resolve_llm_config` (first LLM node → global
    "agent" role → first DB row), so unwired/legacy workflows are unchanged.
    """
    agent_id = find_agent_node_id(workflow)
    if agent_id:
        try:
            wired = await resolve_llm_config_for_consumer_port(workflow, agent_id, "lm")
            if wired:
                logger.info(
                    "ReactStrategy: agent main model from wired lm port = %s",
                    wired.get("model"),
                )
                return wired
        except Exception as exc:  # noqa: BLE001 — never break on resolution
            logger.warning(
                "ReactStrategy: agent lm-port resolution failed (%s) — falling back",
                exc,
            )
    return await resolve_llm_config(workflow)

# Node ``type`` values that represent a Memory node wired to the agent's
# ``memory`` input port. Kept here next to the connectivity helper so callers
# share one definition.
MEMORY_NODE_TYPES = ("vector_memory", "memory")

# Shared "resource provider" nodes: a Language Model or Memory node wired into a
# consumer's optional ``lm``/``memory`` port. The connectivity BFS may LAND on
# one (to detect a direct agent↔resource link) but must never traverse THROUGH
# it. A model/memory node shared between the agent and a tool node's ``lm`` port
# would otherwise bridge that tool onto the agent even though the user never
# wired it to the agent's tools port — the reported bug where an agent sharing a
# multi-model node with an unwired Code Search node still bound codegraph tools.
RESOURCE_NODE_TYPES = tuple(dict.fromkeys(LLM_NODE_TYPES + MEMORY_NODE_TYPES))


def has_memory_node(workflow: Dict[str, Any]) -> bool:
    """True when a Memory node is connected to the agent (drives ``spec.memory``)."""
    return bool(get_connected_node_ids(workflow, MEMORY_NODE_TYPES))


def get_connected_node_ids(
    workflow: Dict[str, Any],
    target_type: "str | tuple[str, ...]",
) -> List[str]:
    """Return IDs of nodes of *target_type* connected to any ``agent`` node.

    Uses undirected BFS across the workflow edges so that connection is
    detected regardless of edge direction.  This is the single source of
    truth for "is this node wired to the agent?".

    Args:
        workflow: Full workflow definition (nodes + edges).
        target_type: The ``type`` value(s) to look for. Accepts a single string
            (e.g. ``"tool"``) or a tuple of types (e.g. both code-analyzer
            dialects).

    Returns:
        List of node IDs whose type matches *target_type*, reachable from at
        least one agent node.
    """
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])

    target_types = (target_type,) if isinstance(target_type, str) else tuple(target_type)
    agent_ids = {n["id"] for n in nodes if n.get("type") == "agent"}
    target_ids = {n["id"] for n in nodes if n.get("type") in target_types}

    if not agent_ids or not target_ids:
        return []

    node_type = {n.get("id"): n.get("type") for n in nodes if n.get("id")}

    # Build undirected adjacency from edges PLUS ``parentId`` containment: a squad
    # member carries ``parentId`` = its ``subagent_window`` frame (no edge), and
    # the frame has a real edge to the agent's Subagents port — so a contained
    # tool node is reachable agent → frame → member and its config still gets
    # built (it's scoped to the delegate child later by strict scoping).
    neighbours: Dict[str, set] = {}

    def _link(a: Optional[str], b: Optional[str]) -> None:
        if a and b:
            neighbours.setdefault(a, set()).add(b)
            neighbours.setdefault(b, set()).add(a)

    for edge in edges:
        _link(edge.get("source"), edge.get("target"))
    for n in nodes:
        _link(n.get("id"), n.get("parentId"))

    # BFS from every agent node. A resource node (shared LM/memory) is recorded
    # if it is itself a target, but we never expand its neighbours — see
    # RESOURCE_NODE_TYPES. Agent start nodes always expand (never resource types).
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
        if current not in agent_ids and node_type.get(current) in RESOURCE_NODE_TYPES:
            continue  # do not bridge through a shared resource provider
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

async def resolve_default_tools_config() -> List[Dict[str, Any]]:
    """Return the gateway's default MCP tool configs for the agent role.

    Reads the ``agent`` MCP role assignment and synthesizes the same tool-config
    dicts :func:`extract_tools_config` produces. ``setup_tools`` resolves
    command/args/env from the ``mcp_servers`` DB row by name, so only the name is
    required here. Used only when a workflow wires no tool nodes; returns ``[]``
    (and logs a warning) on any error.
    """
    try:
        from app.infrastructure.persistence import mcp_role_repository
        names = await mcp_role_repository.list_for_role("agent")
    except Exception as exc:  # noqa: BLE001 — defaults must never break a run
        logger.warning("ReactStrategy: could not load default MCP servers: %s", exc)
        return []

    return [
        {
            "node_id": f"gateway-{name}",
            "name": name,
            "command": None,
            "args": [],
            "env": {},
        }
        for name in names
    ]

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

    Merges repo lists from all connected code-analyzer nodes (both the
    ``codeAnalyzer`` and ``code_search_tool`` dialects), deduplicating by repo
    name.  First occurrence (BFS order) wins on name conflicts.

    Returns:
        Dict with ``repos`` list and ``backend`` string, or ``None`` if no
        code-analyzer node is connected.
    """
    from app.workflow.code_analyzer_config import (
        CODE_ANALYZER_NODE_TYPES,
        DEFAULT_CODE_ANALYZER_BACKEND,
        read_code_analyzer_backend,
        read_code_analyzer_repos,
    )

    nodes = workflow.get("nodes", [])
    connected_ids = get_connected_node_ids(workflow, CODE_ANALYZER_NODE_TYPES)

    if not connected_ids:
        return None

    ca_nodes = {
        n["id"]: n for n in nodes if n.get("type") in CODE_ANALYZER_NODE_TYPES
    }

    merged_repos: List[Dict[str, Any]] = []
    seen_names: Dict[str, str] = {}  # name → first path (for conflict detection)
    backend: str = DEFAULT_CODE_ANALYZER_BACKEND
    backend_set = False

    for ca_id in connected_ids:
        if ca_id not in ca_nodes:
            continue
        # First connected node's backend wins; warn on a conflicting second one
        # (mirrors the repo-name conflict handling below).
        node_backend = read_code_analyzer_backend(ca_nodes[ca_id])
        if not backend_set:
            backend = node_backend
            backend_set = True
        elif node_backend != backend:
            logger.warning(
                "ReactStrategy: conflicting code-analyzer backends across nodes "
                "(%r kept, %r ignored on node %s)",
                backend, node_backend, ca_id,
            )
        for repo in read_code_analyzer_repos(ca_nodes[ca_id]):
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
        "ReactStrategy: %d codeAnalyzer node(s) connected to agent, backend=%s, repos=%s",
        len(connected_ids),
        backend,
        [r.get("name") for r in merged_repos],
    )

    return {"repos": merged_repos, "backend": backend}


def extract_subagents_config(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return specialist subagent definitions from any ``subagents`` nodes wired to the agent.

    A ``subagents`` node holds ``params.subagents`` as a JSON array of
    ``{name, system, tools}`` dicts (serialized by the UI editor).  All
    connected nodes are merged in BFS order; duplicates by ``name`` are skipped
    (first occurrence wins).
    """
    import json as _json

    nodes = workflow.get("nodes", [])
    connected_ids = set(get_connected_node_ids(workflow, "subagents"))

    if not connected_ids:
        return []

    subagent_nodes = [n for n in nodes if n.get("type") == "subagents" and n.get("id") in connected_ids]

    merged: List[Dict[str, Any]] = []
    seen_names: set = set()

    for node in subagent_nodes:
        params = node.get("params") or node.get("data") or {}
        raw = params.get("subagents", "")
        if not raw:
            continue
        if isinstance(raw, list):
            defs = raw
        elif isinstance(raw, str):
            try:
                defs = _json.loads(raw)
                if not isinstance(defs, list):
                    continue
            except Exception:  # noqa: BLE001
                continue
        else:
            continue

        for d in defs:
            if not isinstance(d, dict) or not d.get("name"):
                continue
            name = d["name"].strip()
            if name in seen_names:
                continue
            seen_names.add(name)
            merged.append(d)

    if merged:
        logger.info(
            "ReactStrategy: %d subagent specialist(s) from %d wired subagents node(s)",
            len(merged), len(subagent_nodes),
        )

    return merged
