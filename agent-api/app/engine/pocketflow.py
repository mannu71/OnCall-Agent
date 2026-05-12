"""Minimal async graph engine for dynamic workflow execution.

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
    class MyNode(BaseNode):
        async def exec(self, prep_res):
            return {"result": "ok"}

    # Build and run a flow from a serialised graph
    flow = await WorkflowGraph.from_workflow_json(workflow_dict, registry)
    result = await flow.run(shared={"inputs": {...}})
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

# Default action returned when post() does not specify one
_DEFAULT_ACTION = "default"


# ─────────────────────────────────────────────────────────────────────────────
# BaseNode
# ─────────────────────────────────────────────────────────────────────────────

class BaseNode(ABC):
    """Base class for all workflow graph nodes.

    Subclasses override ``prep``, ``exec``, and ``post``.  Only ``exec`` is
    abstract — the other two have sensible defaults (pass-through / default).
    """

    def __init__(self, node_id: str = "", config: Optional[Dict[str, Any]] = None):
        self.node_id = node_id
        self.config: Dict[str, Any] = config or {}
        self._successors: Dict[str, "BaseNode"] = {}  # action → next node

    def add_successor(self, node: "BaseNode", action: str = _DEFAULT_ACTION) -> "BaseNode":
        """Wire *node* as the successor for *action*.  Returns *self* for chaining."""
        self._successors[action] = node
        return self

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    async def prep(self, shared: Dict[str, Any]) -> Any:
        """Read inputs from *shared* state.  Returns prep_result for exec()."""
        return shared

    @abstractmethod
    async def exec(self, prep_result: Any) -> Any:
        """Do the main work.  Must be overridden by subclasses."""

    async def post(self, shared: Dict[str, Any], exec_result: Any) -> str:
        """Write results back to *shared* and return a routing action."""
        shared[self.node_id] = exec_result
        return _DEFAULT_ACTION

    # ── Internal runner ───────────────────────────────────────────────────────

    async def _run(self, shared: Dict[str, Any]) -> Optional["BaseNode"]:
        prep_result = await self.prep(shared)
        exec_result = await self.exec(prep_result)
        action = await self.post(shared, exec_result) or _DEFAULT_ACTION
        return self._successors.get(action)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in passthrough node (used for start / end markers in JSON graphs)
# ─────────────────────────────────────────────────────────────────────────────

class PassthroughNode(BaseNode):
    """No-op node — used for START/END placeholders in JSON workflows."""

    async def exec(self, prep_result: Any) -> Any:
        return prep_result


# ─────────────────────────────────────────────────────────────────────────────
# NodeRegistry
# ─────────────────────────────────────────────────────────────────────────────

class NodeRegistry:
    """Maps node type names (strings) to BaseNode subclasses.

    Usage::

        registry = NodeRegistry()

        @registry.register("my_processor")
        class MyProcessor(BaseNode):
            async def exec(self, prep_result):
                return process(prep_result)
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type[BaseNode]] = {
            "passthrough": PassthroughNode,
            "start": PassthroughNode,
            "end": PassthroughNode,
        }

    def register(self, name: str) -> Callable[[Type[BaseNode]], Type[BaseNode]]:
        """Class decorator that registers a node type under *name*."""
        def decorator(cls: Type[BaseNode]) -> Type[BaseNode]:
            self._registry[name] = cls
            return cls
        return decorator

    def register_class(self, name: str, cls: Type[BaseNode]) -> None:
        """Register *cls* under *name* imperatively."""
        self._registry[name] = cls

    def create(self, name: str, node_id: str = "", config: Optional[Dict[str, Any]] = None) -> BaseNode:
        """Instantiate a node of type *name*.

        Raises:
            KeyError: When *name* is not registered.
        """
        cls = self._registry.get(name)
        if cls is None:
            raise KeyError(
                f"Unknown node type '{name}'. "
                f"Registered types: {sorted(self._registry)}"
            )
        return cls(node_id=node_id, config=config or {})

    def is_registered(self, name: str) -> bool:
        return name in self._registry


# ─────────────────────────────────────────────────────────────────────────────
# WorkflowGraph — executes a graph of BaseNode instances
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowGraph:
    """Runs a directed graph of BaseNode instances.

    Build via ``from_workflow_json()`` to instantiate from a ReactFlow JSON
    workflow definition, or construct manually by wiring nodes with
    ``add_successor()``.
    """

    def __init__(self, start_node: BaseNode):
        self.start_node = start_node

    async def run(self, shared: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the graph from start_node to termination.

        Args:
            shared: Mutable state dict passed to every node's prep/post.

        Returns:
            The *shared* dict after all nodes have executed.
        """
        shared = shared or {}
        current: Optional[BaseNode] = self.start_node
        visited: List[str] = []

        while current is not None:
            node_id = current.node_id or type(current).__name__
            if node_id in visited:
                logger.warning(
                    "WorkflowGraph: cycle detected at node '%s' — halting", node_id
                )
                break
            visited.append(node_id)
            logger.debug("WorkflowGraph: executing node '%s'", node_id)
            current = await current._run(shared)

        logger.info("WorkflowGraph: flow complete — visited %d node(s)", len(visited))
        return shared

    # ── Factory: build from ReactFlow JSON ───────────────────────────────────

    @classmethod
    def from_workflow_json(
        cls,
        workflow: Dict[str, Any],
        registry: NodeRegistry,
    ) -> "WorkflowGraph":
        """Instantiate a WorkflowGraph from a ReactFlow workflow definition.

        Args:
            workflow: Dict with ``nodes`` (list) and ``edges`` (list) keys —
                the same JSON structure stored in the workflows table.
            registry: NodeRegistry mapping node type names to BaseNode classes.

        Returns:
            WorkflowGraph rooted at the first node that has no incoming edges.

        Raises:
            ValueError: When the graph is empty or has no reachable start node.
        """
        nodes_def: List[Dict[str, Any]] = workflow.get("nodes", [])
        edges_def: List[Dict[str, Any]] = workflow.get("edges", [])

        if not nodes_def:
            raise ValueError("Workflow has no nodes")

        # Build node instances from the registry
        node_map: Dict[str, BaseNode] = {}
        for n in nodes_def:
            nid = n["id"]
            ntype = n.get("type", "passthrough")
            config = n.get("data", {})
            try:
                node_obj = registry.create(ntype, node_id=nid, config=config)
            except KeyError:
                logger.warning(
                    "WorkflowGraph: unknown node type '%s' (id=%s) — using passthrough",
                    ntype, nid,
                )
                node_obj = PassthroughNode(node_id=nid, config=config)
            node_map[nid] = node_obj

        # Wire edges as successors
        # Edge label (or "default") becomes the action key
        for e in edges_def:
            src_id = e.get("source")
            tgt_id = e.get("target")
            action = e.get("label") or e.get("data", {}).get("action") or _DEFAULT_ACTION
            if src_id in node_map and tgt_id in node_map:
                node_map[src_id].add_successor(node_map[tgt_id], action)

        # Determine start node: node with no incoming edges
        targets: set = {e["target"] for e in edges_def if "target" in e}
        start_candidates = [n for n in node_map if n not in targets]

        if not start_candidates:
            # Cyclic graph — fall back to first node in definition order
            start_id = nodes_def[0]["id"]
        else:
            start_id = start_candidates[0]

        logger.info(
            "WorkflowGraph: built graph with %d nodes, start=%s",
            len(node_map), start_id,
        )
        return cls(start_node=node_map[start_id])


# ─────────────────────────────────────────────────────────────────────────────
# Global default registry — importable by agent node implementations
# ─────────────────────────────────────────────────────────────────────────────

default_registry = NodeRegistry()
