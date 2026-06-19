"""Declarative agent-spec models.

A filesystem/JSON description of an agent — ``config.yaml`` + ``AGENTS.md`` +
``tools/mcp/*.yaml`` + ``skills/`` — that is version-controllable and portable,
unlike the platform's ReactFlow-JSON-in-Postgres workflows. Scope is import-only:
these models validate a spec and :mod:`app.spec.importer` converts it into the
existing workflow schema so it runs through the current executor unchanged.

Mapping to platform concepts:
  * ``llm.model``    → a Language Model node wired to the agent's ``lm`` port
  * ``tools.mcp``    → ``tool`` nodes (``app.services.mcp_client_manager``)
  * ``policies``     → the declarative policy engine (:mod:`app.core.policy`)
  * ``instructions`` → the agent node's system instructions
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SpecLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    max_completion_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None  # low | medium | high

    @field_validator("reasoning_effort")
    @classmethod
    def _valid_effort(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("low", "medium", "high"):
            raise ValueError("reasoning_effort must be low|medium|high")
        return v


class SpecMCPTool(BaseModel):
    """A declared MCP server. Either stdio (``command``) or remote (``url``)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)


class SpecTools(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: List[str] = Field(default_factory=list)      # callable sub-agent names
    builtins: List[str] = Field(default_factory=list)    # platform built-in tool names
    mcp: List[SpecMCPTool] = Field(default_factory=list)


class AgentSpecConfig(BaseModel):
    """Top-level agent spec (one ``config.yaml``)."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    name: str
    description: str = ""
    instructions: str = ""                               # resolved markdown text
    # Reference a reusable agent profile (see migration 025_agent_profiles.sql);
    # the executor folds its role/output-schema/policies under the node's config.
    profile: Optional[str] = None
    # Optional inline overrides (take precedence over the referenced profile).
    role_prompt: Optional[str] = None
    output_schema: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    llm: Optional[SpecLLM] = None
    tools: SpecTools = Field(default_factory=SpecTools)
    policies: List[Dict[str, Any]] = Field(default_factory=list)  # see app.core.policy
    params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("spec_version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported spec_version {v}; only 1 is supported")
        return v

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must match [a-z0-9][a-z0-9_-]{0,63} (lowercase, ≤64 chars)"
            )
        return v
