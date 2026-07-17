"""graph_engine — the app's generic DAG / workflow-graph engine.

Used by ``visual_workflow_executor`` and ``dynamic_loader`` to run ReactFlow
user-built workflows. Exports two surfaces:

1. **Async dynamic-graph engine** (WorkflowGraph / ReactFlow integration):
       GraphNode, PassthroughNode, NodeRegistry, WorkflowGraph, default_registry

   ``BaseNode`` is aliased to ``GraphNode`` for backwards compatibility.

2. **Async/sync flow framework** (simple sequential DAGs with ``>>`` chaining
   and automatic retries):
       AsyncNode, AsyncFlow, AsyncBatchNode, AsyncParallelBatchNode,
       AsyncBatchFlow, AsyncParallelBatchFlow,
       Node, BatchNode, Flow, BatchFlow
"""

# ── Dynamic async graph engine (WorkflowGraph / ReactFlow integration) ────────
from app.workflow.graph_engine.workflow_graph import (
    GraphNode,
    PassthroughNode,
    NodeRegistry,
    WorkflowGraph,
    default_registry,
)

# Backwards-compat alias for code that imports ``BaseNode`` instead of ``GraphNode``.
BaseNode = GraphNode

# ── Flow framework (sequential DAG, AsyncNode-based) ──────────────────────────
from app.workflow.graph_engine.core import (
    SyncBaseNode,
    Node,
    BatchNode,
    Flow,
    BatchFlow,
    AsyncNode,
    AsyncFlow,
    AsyncBatchNode,
    AsyncParallelBatchNode,
    AsyncBatchFlow,
    AsyncParallelBatchFlow,
)

__all__ = [
    # Dynamic async graph engine
    "GraphNode",
    "BaseNode",
    "PassthroughNode",
    "NodeRegistry",
    "WorkflowGraph",
    "default_registry",
    # Flow framework
    "SyncBaseNode",
    "Node",
    "BatchNode",
    "Flow",
    "BatchFlow",
    "AsyncNode",
    "AsyncFlow",
    "AsyncBatchNode",
    "AsyncParallelBatchNode",
    "AsyncBatchFlow",
    "AsyncParallelBatchFlow",
]
