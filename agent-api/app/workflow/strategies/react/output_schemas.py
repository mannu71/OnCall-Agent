"""Structured-output schemas for "Data Query Mode".

When a workflow/agent node requests structured output, the ReAct loop runs as
usual (freeform reasoning + tools), and then a single final synthesis call binds
the model to :class:`InvestigationReport` via ``llm.with_structured_output(...)``
so the caller receives a validated, machine-readable object instead of prose.

Kept deliberately flat and provider-agnostic: ``ChatBedrockConverse`` /
``ChatAnthropic`` / ``ChatOpenAI`` all support ``with_structured_output`` over a
Pydantic model (tool-calling under the hood), and the same schema serializes to
JSON for the SSE ``agent_complete`` payload the UI renders as a card.
"""
from __future__ import annotations

from typing import List, Optional

try:  # Pydantic v2 (project standard) — Literal severity for a clean enum in JSON schema
    from typing import Literal
    _Severity = Literal["critical", "high", "medium", "low", "info"]
except Exception:  # pragma: no cover
    _Severity = str  # type: ignore

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """A single piece of code evidence backing a finding (disk-citable)."""

    file: str = Field(description="Repository-relative file path, e.g. src/WebApi/Controllers/ProfilesController.cs")
    line: Optional[int] = Field(default=None, description="1-based line number where the evidence is")
    symbol: Optional[str] = Field(default=None, description="Symbol name (class/function/method) if applicable")
    snippet: Optional[str] = Field(default=None, description="Short supporting quote/signature (<=200 chars)")


class InvestigationReport(BaseModel):
    """Machine-readable result of a code investigation / RCA.

    Every field except ``summary`` is optional so the same schema fits both a
    simple "where is X" lookup and a full root-cause analysis.
    """

    summary: str = Field(description="One-paragraph direct answer to the user's question")
    root_cause: Optional[str] = Field(
        default=None, description="Root cause, when the question is an RCA; otherwise null")
    severity: Optional[_Severity] = Field(  # type: ignore[valid-type]
        default=None, description="Severity if this is an incident/RCA: critical|high|medium|low|info")
    evidence: List[Evidence] = Field(
        default_factory=list, description="Cited file:line evidence supporting the answer")
    affected_components: List[str] = Field(
        default_factory=list, description="Components / modules / services implicated")
    next_steps: List[str] = Field(
        default_factory=list, description="Concrete recommended next actions")
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="0.0-1.0 confidence in the answer")
