"""Registry of structured-output schemas for the agent final-synthesis step.

Agents resolve a named schema from this registry so each use case can return a
shape that fits its domain: an RCA agent uses ``investigation``, a support agent
uses ``support_resolution``, and a generic agent uses ``generic``. The default
is ``generic`` — a neutral shape (summary / findings / next_steps / confidence)
that works for any task. Profiles and agent-node configs can pin a different schema.

The ``incident-rca`` builtin profile explicitly pins ``investigation``, so existing
RCA workflows are unaffected by the default change.

All models stay flat, optional-heavy, and provider-agnostic (Bedrock / Anthropic /
OpenAI ``with_structured_output`` over a Pydantic model) and serialize to JSON for
the SSE ``agent_complete`` payload the UI renders as a card.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Type

from pydantic import BaseModel, Field

from app.workflow.strategies.react.output_schemas import Evidence, InvestigationReport

try:
    from typing import Literal
    _Severity = Literal["critical", "high", "medium", "low", "info"]
except Exception:  # pragma: no cover
    _Severity = str  # type: ignore


class Finding(BaseModel):
    """A single generic finding with optional citation."""

    statement: str = Field(description="One concrete finding, fact, or observation")
    evidence: Optional[str] = Field(
        default=None, description="Short supporting quote / source / citation")


class GenericReport(BaseModel):
    """A use-case-agnostic structured answer.

    Suitable for any agent that wants validated JSON without RCA-specific fields.
    """

    summary: str = Field(description="One-paragraph direct answer to the user's question")
    findings: List[Finding] = Field(
        default_factory=list, description="Discrete findings backing the answer")
    next_steps: List[str] = Field(
        default_factory=list, description="Concrete recommended next actions")
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="0.0-1.0 confidence in the answer")


class SupportResolution(BaseModel):
    """Structured result for a customer-support / service-desk agent."""

    summary: str = Field(description="One-paragraph answer or resolution for the customer")
    resolution_status: Optional[str] = Field(
        default=None,
        description="resolved | needs_more_info | escalated | not_resolved")
    customer_intent: Optional[str] = Field(
        default=None, description="What the customer was trying to achieve")
    actions_taken: List[str] = Field(
        default_factory=list, description="Steps performed to address the request")
    follow_ups: List[str] = Field(
        default_factory=list, description="Outstanding actions or recommendations")
    references: List[str] = Field(
        default_factory=list, description="KB articles, ticket ids, or sources cited")
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="0.0-1.0 confidence in the resolution")


class DataAnalysis(BaseModel):
    """Structured result for a data-analysis / reporting agent."""

    summary: str = Field(description="One-paragraph answer to the analytical question")
    findings: List[Finding] = Field(
        default_factory=list, description="Key quantitative or qualitative findings")
    metrics: List[str] = Field(
        default_factory=list, description="Notable metric values, e.g. 'p95 latency = 412ms'")
    data_quality_notes: List[str] = Field(
        default_factory=list,
        description="Caveats: gaps, sampling, partial coverage, assumptions")
    next_steps: List[str] = Field(
        default_factory=list, description="Recommended follow-up analysis or actions")
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="0.0-1.0 confidence in the analysis")


_REGISTRY: Dict[str, Type[BaseModel]] = {
    "investigation": InvestigationReport,
    "generic": GenericReport,
    "support_resolution": SupportResolution,
    "data_analysis": DataAnalysis,
}

_DEFAULT = "generic"


def register_schema(name: str, model: Type[BaseModel]) -> None:
    """Register (or replace) a named output schema."""
    _REGISTRY[name.strip().lower()] = model


def resolve_output_schema(name: Optional[str]) -> Type[BaseModel]:
    """Resolve a schema name to a Pydantic model, defaulting to GenericReport.

    Unknown names fall back to the default so a bad config never breaks a run.
    """
    if not name:
        return _REGISTRY[_DEFAULT]
    return _REGISTRY.get(str(name).strip().lower(), _REGISTRY[_DEFAULT])


def schema_names() -> List[str]:
    return sorted(_REGISTRY.keys())


__all__ = [
    "Finding",
    "GenericReport",
    "SupportResolution",
    "DataAnalysis",
    "register_schema",
    "resolve_output_schema",
    "schema_names",
]
