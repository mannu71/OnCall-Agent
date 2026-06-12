"""Canonical node-type catalog — the backend's source of truth for node params.

The React editor hardcodes a ``NODE_TYPES`` map describing each node's UI slots.
That map also implicitly encodes what the *backend* requires (which params, under
which keys, in which dialect) — knowledge that drifts from the executor. This
catalog publishes the backend contract: per node type, its category, accepted
param keys (both dialects), and which are required for a valid workflow. It is
kept in sync with :func:`app.workflow.schema.workflow_schema.validate_workflow`.

Exposed via ``GET /api/v1/node-schemas`` so the UI (and any external client) can
build/validate node config against the real backend expectations instead of a
copy that silently goes stale.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Each entry: type → {category, label, params: [{name, dialect_keys, required, type, desc}]}
NODE_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "agent": {
        "category": "Agents",
        "label": "Agent",
        "params": [
            {"name": "instructions", "dialect_keys": ["instructions"], "required": False,
             "type": "text", "desc": "System/role instructions for the agent."},
            {"name": "permissionMode", "dialect_keys": ["permissionMode"], "required": False,
             "type": "enum", "options": ["default", "auto_allow", "plan"],
             "desc": "Tool gatekeeping mode."},
            {"name": "supervisor_enabled", "dialect_keys": ["supervisor_enabled"], "required": False,
             "type": "bool", "desc": "Enable the quality supervisor retry loop."},
            {"name": "outputMode", "dialect_keys": ["outputMode"], "required": False,
             "type": "enum", "options": ["text", "structured"],
             "desc": "Return a validated InvestigationReport alongside prose."},
        ],
    },
    "language_model": {
        "category": "Models",
        "label": "Language Model",
        "params": [
            {"name": "model",
             "dialect_keys": ["model", "modelId", "model_id", "modelName",
                              "configName", "llmConfigId", "llm"],
             "required": True, "type": "string", "desc": "Model id or LLM config reference."},
            {"name": "temperature", "dialect_keys": ["temp", "temperature"], "required": False,
             "type": "number", "desc": "Sampling temperature."},
            {"name": "system", "dialect_keys": ["system"], "required": False,
             "type": "text", "desc": "System message."},
        ],
    },
    "cloudwatch_tool": {
        "category": "Tools",
        "label": "CloudWatch",
        "params": [
            {"name": "log_groups", "dialect_keys": ["groups", "logGroups"], "required": True,
             "type": "list", "desc": "CloudWatch log groups to scan (≥1 required)."},
            {"name": "region", "dialect_keys": ["region", "awsRegion"], "required": False,
             "type": "string", "desc": "AWS region (default us-east-1)."},
            {"name": "profile", "dialect_keys": ["profile", "awsProfile"], "required": False,
             "type": "string", "desc": "AWS profile name."},
            {"name": "analysis", "dialect_keys": ["analysis", "analysisType"], "required": False,
             "type": "enum", "options": ["error-patterns", "metrics", "alarms", "anomalies"],
             "desc": "Analyzer focus."},
        ],
    },
    "code_search_tool": {
        "category": "Tools",
        "label": "Code Analyzer",
        "params": [
            {"name": "repos", "dialect_keys": ["repos"], "required": True,
             "type": "list", "desc": "Indexed repositories to search (≥1 required)."},
        ],
    },
    "tool": {
        "category": "Tools",
        "label": "MCP Tool",
        "params": [
            {"name": "serverName", "dialect_keys": ["serverName", "server_name", "name"],
             "required": True, "type": "string", "desc": "MCP server to connect."},
            {"name": "command", "dialect_keys": ["command"], "required": False,
             "type": "string", "desc": "Inline command (overrides DB-stored server)."},
        ],
    },
    "database": {
        "category": "Tools",
        "label": "Database",
        "params": [
            {"name": "serverName", "dialect_keys": ["serverName", "name"], "required": False,
             "type": "string", "desc": "DB MCP server for schema lookups."},
        ],
    },
    "schedule": {
        "category": "Inputs",
        "label": "Schedule",
        "params": [
            {"name": "frequency", "dialect_keys": ["frequency"], "required": False,
             "type": "enum",
             "options": ["Every 5 min", "Every 15 min", "Every 30 min", "Hourly",
                         "Daily", "Weekly", "Monthly"],
             "desc": "How often to run."},
            {"name": "time", "dialect_keys": ["time"], "required": False,
             "type": "string", "desc": "Local time HH:MM."},
            {"name": "tz", "dialect_keys": ["tz"], "required": False,
             "type": "string", "desc": "IANA timezone."},
        ],
    },
    "orchestrator": {
        "category": "Tools",
        "label": "SQL Orchestrator",
        "params": [
            {"name": "fileName", "dialect_keys": ["fileName", "sqlFile"], "required": True,
             "type": "string", "desc": "SQL file attached to the node."},
        ],
    },
}

# Legacy dialect aliases (old type name → canonical type in this catalog).
LEGACY_TYPE_ALIASES = {
    "cloudwatchAnalyzer": "cloudwatch_tool",
    "codeAnalyzer": "code_search_tool",
    "llm": "language_model",
    "scheduler": "schedule",
}


def get_node_schemas() -> Dict[str, Any]:
    """Return the full node-schema catalog plus legacy type aliases."""
    return {
        "node_types": NODE_SCHEMAS,
        "legacy_aliases": LEGACY_TYPE_ALIASES,
        "count": len(NODE_SCHEMAS),
    }


def list_required_params(node_type: str) -> List[str]:
    """Required param names for a node type (canonical or legacy)."""
    canonical = LEGACY_TYPE_ALIASES.get(node_type, node_type)
    schema = NODE_SCHEMAS.get(canonical)
    if not schema:
        return []
    return [p["name"] for p in schema["params"] if p.get("required")]
