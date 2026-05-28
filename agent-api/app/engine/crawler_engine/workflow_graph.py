"""Async graph engine for dynamic workflow execution.

Enables dynamic graph instantiation from JSON workflow definitions:
  1. Nodes are registered in ``node_registry`` by class name.
  2. When a user saves a ReactFlow workflow, the backend deserialises
     the JSON, instantiates nodes via ``NodeRegistry.create()``, wires
     successor actions from the edge list, and runs the graph.
  3. LangGraph cannot do this because its graphs are compiled at startup;
     WorkflowGraph builds and runs the graph dynamically at request time.

Node lifecycle (per node per execution):
    prep(shared)   → prep_result      — gather input data from shared state
    exec(prep_res) → exec_result      — do the work (may call LLM / tools)
    post(shared, exec_res) → action   — write results back; return routing action

Flow routing:
    A node's ``post()`` returns an action string (default: "default").
    The flow follows the successor registered for that action.
    If no successor exists for the action the flow terminates.

Usage::

    # Register custom node types
    registry = NodeRegistry()

    @registry.register("my_node")
    class MyNode(GraphNode):
        async def exec(self, prep_res):
            return {"result": "ok"}

    # Build and run a flow from a serialised graph
    flow = WorkflowGraph.from_workflow_json(workflow_dict, registry)
    result = await flow.run(shared={"inputs": {...}})
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

_DEFAULT_ACTION = "default"


# ─────────────────────────────────────────────────────────────────────────────
# GraphNode  (was BaseNode in pocketflow.py)
# ─────────────────────────────────────────────────────────────────────────────

class GraphNode(ABC):
    """Base class for all workflow graph nodes."""

    def __init__(self, node_id: str = "", config: Optional[Dict[str, Any]] = None):
        self.node_id = node_id
        self.config: Dict[str, Any] = config or {}
        self._successors: Dict[str, "GraphNode"] = {}

    def add_successor(self, node: "GraphNode", action: str = _DEFAULT_ACTION) -> "GraphNode":
        """Wire *node* as the successor for *action*. Returns *self* for chaining."""
        self._successors[action] = node
        return self

    async def prep(self, shared: Dict[str, Any]) -> Any:
        return shared

    @abstractmethod
    async def exec(self, prep_result: Any) -> Any: ...

    async def post(self, shared: Dict[str, Any], exec_result: Any) -> str:
        shared[self.node_id] = exec_result
        return _DEFAULT_ACTION

    async def _run(self, shared: Dict[str, Any]) -> Optional["GraphNode"]:
        prep_result = await self.prep(shared)
        exec_result = await self.exec(prep_result)
        action = await self.post(shared, exec_result) or _DEFAULT_ACTION
        return self._successors.get(action)


class PassthroughNode(GraphNode):
    """No-op node — used for START/END placeholders in JSON workflows."""

    async def exec(self, prep_result: Any) -> Any:
        return prep_result


# ─────────────────────────────────────────────────────────────────────────────
# NodeRegistry
# ─────────────────────────────────────────────────────────────────────────────

class NodeRegistry:
    """Maps node type names (strings) to GraphNode subclasses."""

    def __init__(self) -> None:
        self._registry: Dict[str, Type[GraphNode]] = {
            "passthrough": PassthroughNode,
            "start": PassthroughNode,
            "end": PassthroughNode,
        }

    def register(self, name: str) -> Callable[[Type[GraphNode]], Type[GraphNode]]:
        def decorator(cls: Type[GraphNode]) -> Type[GraphNode]:
            self._registry[name] = cls
            return cls
        return decorator

    def register_class(self, name: str, cls: Type[GraphNode]) -> None:
        self._registry[name] = cls

    def create(self, name: str, node_id: str = "", config: Optional[Dict[str, Any]] = None) -> GraphNode:
        cls = self._registry.get(name)
        if cls is None:
            raise KeyError(
                f"Unknown node type '{name}'. Registered: {sorted(self._registry)}"
            )
        return cls(node_id=node_id, config=config or {})

    def is_registered(self, name: str) -> bool:
        return name in self._registry


# ─────────────────────────────────────────────────────────────────────────────
# WorkflowGraph
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowGraph:
    """Runs a directed graph of GraphNode instances."""

    def __init__(self, start_node: GraphNode):
        self.start_node = start_node

    async def run(self, shared: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        shared = shared or {}
        current: Optional[GraphNode] = self.start_node
        visited: List[str] = []

        while current is not None:
            node_id = current.node_id or type(current).__name__
            if node_id in visited:
                logger.warning("WorkflowGraph: cycle at '%s' — halting", node_id)
                break
            visited.append(node_id)
            logger.debug("WorkflowGraph: executing '%s'", node_id)
            current = await current._run(shared)

        logger.info("WorkflowGraph: complete — %d node(s) visited", len(visited))
        return shared

    @classmethod
    def from_workflow_json(
        cls,
        workflow: Dict[str, Any],
        registry: NodeRegistry,
    ) -> "WorkflowGraph":
        """Build a WorkflowGraph from a ReactFlow JSON workflow definition."""
        nodes_def: List[Dict[str, Any]] = workflow.get("nodes", [])
        edges_def: List[Dict[str, Any]] = workflow.get("edges", [])

        if not nodes_def:
            raise ValueError("Workflow has no nodes")

        node_map: Dict[str, GraphNode] = {}
        for n in nodes_def:
            nid = n["id"]
            ntype = n.get("type", "passthrough")
            config = n.get("data", {})
            try:
                node_map[nid] = registry.create(ntype, node_id=nid, config=config)
            except KeyError:
                logger.warning("Unknown node type '%s' (id=%s) — passthrough", ntype, nid)
                node_map[nid] = PassthroughNode(node_id=nid, config=config)

        for e in edges_def:
            src_id = e.get("source")
            tgt_id = e.get("target")
            action = e.get("label") or e.get("data", {}).get("action") or _DEFAULT_ACTION
            if src_id in node_map and tgt_id in node_map:
                node_map[src_id].add_successor(node_map[tgt_id], action)

        targets: set = {e["target"] for e in edges_def if "target" in e}
        start_candidates = [n for n in node_map if n not in targets]
        start_id = start_candidates[0] if start_candidates else nodes_def[0]["id"]

        logger.info("WorkflowGraph: %d nodes, start=%s", len(node_map), start_id)
        return cls(start_node=node_map[start_id])


default_registry = NodeRegistry()
