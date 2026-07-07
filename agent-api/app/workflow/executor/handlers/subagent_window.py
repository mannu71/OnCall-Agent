"""Subagent Window node handler — a visual grouping container.

The ``subagent_window`` node is a UI-only construct: it visually groups the
tool nodes that make up a specialist "squad" (its children carry
``parentId: <window id>``) and feeds the connected agent through the
``specialists`` edge. The squad's actual definition (name, tools, model) is
flattened by the UI onto the AGENT node's ``subagents`` field before save, so
the backend never needs to resolve anything from the window itself.

Without a handler the executor logged the window as an *"Unknown node type …
skipping"* warning on every run and marked it *skipped* on the canvas — noisy
and misleading, since it is behaving exactly as intended. This handler mirrors
the other config-provider nodes (e.g. ``language_model``): it does no work and
just returns a ``success`` status so the canvas live-status overlay shows the
window as a normal, resolved node.
"""
import logging
from typing import Any, Dict

from . import register

logger = logging.getLogger(__name__)


@register("subagent_window")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """No-op: the window is a visual container; its squad config lives on the
    connected agent node. Return success so it isn't reported as skipped."""
    params = node.get("params") or node.get("data") or {}
    label = params.get("name") or node.get("name") or "Subagent Window"
    logger.debug("subagent_window node %s ('%s'): visual container — no-op", node.get("id"), label)
    return {
        "status": "success",
        "output": f"Subagent group '{label}' (visual container; specialists resolved on the agent node)",
    }
