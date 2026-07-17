"""Tool catalog API — exposes the populated tool registry.

Backs a future UI tool palette (drag-from-catalog) and gives operators a single
place to see every tool the platform knows about (built-in + MCP-discovered),
instead of the catalog being implicit in hardcoded editor ``NODE_TYPES``.
"""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter

from app.core.tools.tool_registry import registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["tools"])


def _source_of(name: str) -> str:
    return "mcp" if name.startswith("mcp:") else "builtin"


@router.get("")
async def list_tools() -> Dict[str, Any]:
    """Return the full tool catalog from the registry.

    Each entry: ``name``, ``description``, ``emoji``, ``schema``, ``source``
    (builtin|mcp), and ``available``.
    """
    tools: List[Dict[str, Any]] = []
    for name in registry.list_tools():
        schema = registry.get_schema(name) or {}
        description = ""
        fn = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(fn, dict):
            description = fn.get("description", "")
        tools.append({
            "name": name,
            "description": description,
            "emoji": registry.get_emoji(name),
            "schema": schema,
            "source": _source_of(name),
            "available": registry.is_available(name),
        })
    tools.sort(key=lambda t: (t["source"], t["name"]))
    return {"count": len(tools), "tools": tools}


# Separate router (no /tools prefix) for the node-schema catalog.
node_schemas_router = APIRouter(prefix="/node-schemas", tags=["node-schemas"])


@node_schemas_router.get("")
async def get_node_schemas_endpoint() -> Dict[str, Any]:
    """Return the canonical per-node-type param contract (backend source of truth)."""
    from app.workflow.schema.node_catalog import get_node_schemas
    return get_node_schemas()
