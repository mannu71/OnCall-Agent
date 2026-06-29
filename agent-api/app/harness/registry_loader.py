"""Populate the (previously orphaned) tool registry at application startup.

``app.core.tool_registry.registry`` was fully implemented but never populated —
tools were constructed ad hoc inside ``strategy.py`` per execution, and nothing
could enumerate the catalog. This loader registers a *discovery* catalog:

  * built-in tools whose schema needs no per-execution config (playbook tools)
    are introspected for an accurate schema;
  * built-in tool *families* that require config to instantiate (CloudWatch,
    code analyzer, DB schema, edit) are registered as static descriptors;
  * MCP servers configured in the DB are discovered and listed as ``mcp`` tools.

Phase 1 is discovery-only: registered handlers raise if invoked, because live
execution still flows through the existing per-execution tool building. Phase 2
wires the loop to resolve tools *from* this registry (and to run the registry's
already-implemented ``get_all_schemas(query=...)`` router before instantiation).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.core.tool_registry import registry

logger = logging.getLogger(__name__)


def _discovery_handler(name: str):
    """A placeholder handler for catalog entries (not executed in Phase 1)."""
    def _handler(*_a, **_k):  # noqa: ANN001
        raise NotImplementedError(
            f"Tool '{name}' is registered for discovery only; live execution "
            f"flows through the per-execution tool builder (Phase 1)."
        )
    return _handler


# Static descriptors for built-in tool families that need per-execution config
# (region, repos, db creds) to instantiate. Schema is intentionally minimal —
# enough for the UI palette and for routing — not the full arg schema.
_BUILTIN_FAMILIES: List[Dict[str, Any]] = [
    {
        "name": "cloudwatch_search_logs",
        "emoji": "🔎",
        "description": "Search CloudWatch Logs Insights across configured log groups.",
        "category": "cloudwatch",
    },
    {
        "name": "cloudwatch_analyze_patterns",
        "emoji": "📊",
        "description": "Summarize error/latency patterns in a CloudWatch log group.",
        "category": "cloudwatch",
    },
    {
        "name": "cloudwatch_detect_anomalies",
        "emoji": "🚨",
        "description": "Detect anomalous spikes vs. baseline in a CloudWatch log group.",
        "category": "cloudwatch",
    },
    {
        "name": "code_search",
        "emoji": "🧭",
        "description": "Semantic + symbol search over an indexed code repository.",
        "category": "code",
    },
    {
        "name": "db_schema_lookup",
        "emoji": "🗄️",
        "description": "Inspect database table/column schema for query building.",
        "category": "database",
    },
    {
        "name": "edit_file",
        "emoji": "✏️",
        "description": "Surgical str-replace edit of a file (gated: requires approval).",
        "category": "edit",
    },
    {
        "name": "create_file",
        "emoji": "📄",
        "description": "Create a new file with given content (gated: requires approval).",
        "category": "edit",
    },
]


def _schema_for(name: str, description: str) -> Dict[str, Any]:
    """Minimal OpenAI-compatible tool schema for catalog/discovery."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _register_introspected_builtins() -> int:
    """Register built-in tools we can introspect with no config (playbook tools)."""
    count = 0
    try:
        from app.workflow.strategies.react.tool_setup import build_playbook_tools
        for tool in build_playbook_tools():
            name = getattr(tool, "name", None)
            if not name:
                continue
            desc = getattr(tool, "description", "") or ""
            registry.register(
                name=name,
                schema=_schema_for(name, desc),
                handler=_discovery_handler(name),
                emoji="📒",
            )
            count += 1
    except Exception as exc:  # noqa: BLE001 — registry load must not break startup
        logger.warning("registry_loader: playbook tool introspection failed: %s", exc)
    return count


def _register_builtin_families() -> int:
    count = 0
    for fam in _BUILTIN_FAMILIES:
        try:
            registry.register(
                name=fam["name"],
                schema=_schema_for(fam["name"], fam["description"]),
                handler=_discovery_handler(fam["name"]),
                emoji=fam.get("emoji", "⚡"),
            )
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("registry_loader: failed to register %s: %s", fam["name"], exc)
    return count


async def _register_mcp_servers() -> int:
    count = 0
    try:
        from app.infrastructure.persistence import mcp_config_repository
        servers = await mcp_config_repository.list_all(include_disabled=False)
        for srv in servers:
            name = srv.get("name")
            if not name:
                continue
            registry.register(
                name=f"mcp:{name}",
                schema=_schema_for(f"mcp:{name}", srv.get("description") or f"MCP server '{name}'"),
                handler=_discovery_handler(f"mcp:{name}"),
                emoji="🔌",
            )
            count += 1
    except Exception as exc:  # noqa: BLE001 — DB may be unavailable at startup
        logger.warning("registry_loader: MCP discovery failed: %s", exc)
    return count


async def load_registry() -> Dict[str, int]:
    """Populate the global registry. Safe to call once at startup (idempotent-ish:
    re-registration overwrites with a warning).

    Returns a small summary dict for logging/observability.
    """
    builtins = _register_introspected_builtins()
    families = _register_builtin_families()
    mcp = await _register_mcp_servers()
    total = len(registry.list_tools())
    logger.info(
        "registry_loader: registered %d playbook + %d builtin-family + %d MCP "
        "= %d total tools in registry",
        builtins, families, mcp, total,
    )
    return {"playbook": builtins, "builtin_families": families, "mcp": mcp, "total": total}
