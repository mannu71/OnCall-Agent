"""Validate an :class:`AgentSpecConfig` before import.

Enforces the allowlist tenets: policies must name registered handlers, MCP
servers must be resolvable (stdio command or remote
url), and sub-agent / builtin references must be well-formed. Returns a list of
human-readable error strings (empty == valid) so callers can surface all problems
at once rather than failing on the first.
"""
from __future__ import annotations

from typing import List

from app.spec.types import AgentSpecConfig


def validate_spec(spec: AgentSpecConfig) -> List[str]:
    errors: List[str] = []

    # ── policies must resolve against the registry allowlist ───────────────────
    if spec.policies:
        try:
            from app.core.policy import build_policy_set
            build_policy_set(spec.policies)
        except Exception as exc:  # PolicyConfigError or similar
            errors.append(f"policies: {exc}")

    # ── MCP servers must be resolvable ─────────────────────────────────────────
    seen_mcp = set()
    for tool in spec.tools.mcp:
        if not tool.name:
            errors.append("tools.mcp: an entry is missing 'name'")
            continue
        if tool.name in seen_mcp:
            errors.append(f"tools.mcp: duplicate server name {tool.name!r}")
        seen_mcp.add(tool.name)
        if not tool.command and not tool.url:
            errors.append(
                f"tools.mcp[{tool.name}]: must declare either 'command' (stdio) "
                "or 'url' (remote)"
            )

    # ── builtins must be known platform tools (allowlist) ──────────────────────
    known = _known_builtin_names()
    if known:
        for name in spec.tools.builtins:
            if name not in known:
                errors.append(
                    f"tools.builtins: unknown built-in tool {name!r} "
                    "(not in the platform tool registry)"
                )

    # ── sub-agent references must be valid identifiers ─────────────────────────
    for agent_name in spec.tools.agents:
        if not agent_name or not agent_name.replace("-", "").replace("_", "").isalnum():
            errors.append(f"tools.agents: invalid sub-agent name {agent_name!r}")

    return errors


def _known_builtin_names() -> set:
    """Best-effort set of registered built-in tool names; empty when unavailable."""
    try:
        from app.core.tools.tool_registry import registry
        names = set()
        for getter in ("list_names", "names", "all"):
            fn = getattr(registry, getter, None)
            if callable(fn):
                result = fn()
                names = {getattr(t, "name", t) if not isinstance(t, str) else t for t in result}
                break
        return {str(n) for n in names}
    except Exception:  # noqa: BLE001 — strict builtin check is optional
        return set()
