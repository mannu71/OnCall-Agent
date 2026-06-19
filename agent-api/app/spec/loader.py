"""Load an agent spec from a directory or an in-memory dict.

Filesystem layout:

    my-agent/
      config.yaml         # AgentSpecConfig fields (instructions may be a file ref)
      AGENTS.md           # default instructions file
      tools/mcp/*.yaml    # one SpecMCPTool per file

``instructions`` in config.yaml may be inline text or a path relative to the spec
dir; when omitted, ``AGENTS.md`` is used if present.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml

from app.spec.types import AgentSpecConfig, SpecMCPTool


def load_spec_dict(data: Dict[str, Any]) -> AgentSpecConfig:
    """Validate an in-memory spec dict into an :class:`AgentSpecConfig`."""
    return AgentSpecConfig.model_validate(data)


def load_spec_dir(path: str) -> AgentSpecConfig:
    """Load and validate a spec from a directory on disk."""
    if not os.path.isdir(path):
        raise FileNotFoundError(f"spec directory not found: {path}")

    config_path = os.path.join(path, "config.yaml")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"config.yaml not found in {path}")
    with open(config_path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    # Resolve instructions: inline text, a file ref, or default AGENTS.md.
    raw["instructions"] = _resolve_instructions(path, raw.get("instructions"))

    # Merge tools/mcp/*.yaml into tools.mcp.
    mcp_dir = os.path.join(path, "tools", "mcp")
    if os.path.isdir(mcp_dir):
        tools = raw.setdefault("tools", {})
        existing = tools.setdefault("mcp", [])
        for fname in sorted(os.listdir(mcp_dir)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            with open(os.path.join(mcp_dir, fname), "r", encoding="utf-8") as fh:
                entry = yaml.safe_load(fh) or {}
            # Validate each entry early for a precise error message.
            existing.append(SpecMCPTool.model_validate(entry).model_dump())

    return AgentSpecConfig.model_validate(raw)


def _resolve_instructions(base: str, value: Any) -> str:
    if not value:
        agents_md = os.path.join(base, "AGENTS.md")
        if os.path.isfile(agents_md):
            with open(agents_md, "r", encoding="utf-8") as fh:
                return fh.read()
        return ""
    if isinstance(value, str):
        candidate = os.path.join(base, value)
        # Treat as a file ref only if it actually resolves to a file in the dir.
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8") as fh:
                return fh.read()
        return value  # inline text
    raise ValueError("instructions must be a string (inline text or a file path)")
