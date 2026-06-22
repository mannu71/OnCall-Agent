"""Per-node-type handlers for VisualWorkflowExecutor.

Each handler module registers an async ``execute(executor, node, context)`` function
that mirrors the original method signature with ``self`` renamed to ``executor``.
The dispatcher in ``visual_workflow_executor._execute_node`` looks up the handler
in :data:`HANDLERS` by node type.
"""
from typing import Awaitable, Callable, Dict, Any

HandlerFn = Callable[..., Awaitable[Dict[str, Any]]]
HANDLERS: Dict[str, HandlerFn] = {}


def register(node_type: str):
    def deco(fn: HandlerFn) -> HandlerFn:
        HANDLERS[node_type] = fn
        return fn
    return deco


# Import handler modules to trigger registration. Keep at bottom to avoid cycles.
from . import scheduler  # noqa: E402,F401
from . import orchestrator  # noqa: E402,F401
from . import tool  # noqa: E402,F401
from . import database  # noqa: E402,F401  — new LangflowEditor 'database' node type
from . import agent  # noqa: E402,F401
from . import batch_agent  # noqa: E402,F401
from . import cloudwatch  # noqa: E402,F401
from . import language_model  # noqa: E402,F401  — new LangflowEditor 'language_model' node type
from . import router  # noqa: E402,F401  — new LangflowEditor 'router' node type
from . import wiki  # noqa: E402,F401  — new Wiki output node type
from . import code_analyzer  # noqa: E402,F401
from . import vector_memory  # noqa: E402,F401  — semantic recall node
