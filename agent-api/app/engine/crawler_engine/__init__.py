"""crawler_engine — generic graph engine package.

NOTE: despite the package name, this is the app's generic DAG engine, NOT the
deleted Python code crawler. It is used by ``visual_workflow_executor`` and
``dynamic_loader`` to run ReactFlow user-built workflows.

Exports two surfaces:

1. **Async dynamic-graph engine** (used by visual_workflow_executor for
   ReactFlow user-built workflows):
       GraphNode, PassthroughNode, NodeRegistry, WorkflowGraph, default_registry

   ``BaseNode`` is aliased to ``GraphNode`` for backwards compatibility with
   any code that previously imported it from ``app.engine.pocketflow``.

2. **Async/sync flow framework** (simple sequential DAGs with ``>>`` chaining
   and automatic retries):
       AsyncNode, AsyncFlow, AsyncBatchNode, AsyncParallelBatchNode,
       AsyncBatchFlow, AsyncParallelBatchFlow,
       Node, BatchNode, Flow, BatchFlow
"""

# ── Dynamic async graph engine (WorkflowGraph / ReactFlow integration) ────────
from app.engine.crawler_engine.workflow_graph import (
    GraphNode,
    PassthroughNode,
    NodeRegistry,
    WorkflowGraph,
    default_registry,
)

# Backwards-compat alias — preserves any ``from app.engine.pocketflow import BaseNode``
BaseNode = GraphNode

# ── Crawler flow framework (sequential DAG, AsyncNode-based) ──────────────────
from app.engine.crawler_engine.core import (
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
    # Crawler flow framework
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
