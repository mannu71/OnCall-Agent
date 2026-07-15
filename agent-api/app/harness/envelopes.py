"""Standardized observation/result envelopes for the Agent Harness.

Two contracts, one rendering rule:

  * :class:`ToolResult` — what a tool hands back to the agent loop. Rendered to
    the ``ToolMessage`` content the model reads: a summary line first, then the
    (capped) payload, with an explicit truncation marker and a recovery hint on
    error. This is the single place a *uniform* output cap lives, so the gap
    where CloudWatch / DB / MCP tool outputs were uncapped (only the code
    analyzer capped, via its own tool ``_cap`` helper) is closed by construction.

  * :class:`NodeOutput` — what a workflow node hands back to the executor. A
    typed envelope over the loose ``{"status": ..., "output": ...}`` dicts the
    BFS executor currently passes between nodes, with ``to_dict`` preserving the
    existing wire shape so adoption is incremental and non-breaking.

These extend (do not duplicate) :mod:`app.workflow.strategies.react.output_schemas`
— that module owns the *structured-output* report (``InvestigationReport``);
this module owns the *transport envelope* around any tool/node result.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from typing import Literal
    ToolStatus = Literal["ok", "warning", "error"]
except Exception:  # pragma: no cover
    ToolStatus = str  # type: ignore

from pydantic import BaseModel, Field

# Default uniform cap for a single tool result. A large result is replayed in
# the message history on every subsequent ReAct iteration, so an unbounded dump
# silently inflates input cost across the whole loop. Mirrors the existing
# per-family caps; tune via the constructor / settings later.
DEFAULT_TOOL_OUTPUT_MAX_CHARS = 12_000

_TRUNCATION_MARKER = "[truncated"


def cap_text(value: str, max_chars: int, source: str = "tool") -> tuple[str, bool]:
    """Cap *value* to *max_chars*, returning ``(text, was_truncated)``.

    Accuracy-safe: nothing is summarized — the model is told exactly how to
    fetch the remainder with a narrower query. Idempotent: an already-capped
    string (carrying the truncation marker) passes through untouched.
    """
    if not isinstance(value, str) or max_chars <= 0 or len(value) <= max_chars:
        return value, False
    if _TRUNCATION_MARKER in value[-200:]:
        return value, True
    total = len(value)
    suffix = (
        f"\n…[truncated: showing first {max_chars} of {total} chars from "
        f"'{source}'. Narrow the query (WHERE/LIMIT, date range, name_like, "
        f"drill_down, or pagination) and call again to retrieve the rest.]"
    )
    return value[:max_chars] + suffix, True


class ToolError(BaseModel):
    """Structured error context attached to a failed :class:`ToolResult`."""

    root_cause: str = Field(description="Short human-readable cause of the failure")
    retryable: bool = Field(
        default=False,
        description="Whether retrying the same call (or a narrower one) might succeed",
    )


class ToolResult(BaseModel):
    """Envelope a tool returns to the agent loop.

    Use :meth:`render` to produce the ``ToolMessage`` string the model reads.
    """

    status: ToolStatus = Field(default="ok")  # type: ignore[valid-type]
    summary: str = Field(description="One-line headline the model can act on without parsing data")
    data: Optional[Any] = Field(default=None, description="Structured payload (JSON-serializable)")
    truncated: bool = Field(default=False, description="True when data was capped on render")
    error: Optional[ToolError] = Field(default=None)

    @classmethod
    def ok(cls, summary: str, data: Any = None) -> "ToolResult":
        return cls(status="ok", summary=summary, data=data)

    @classmethod
    def warn(cls, summary: str, data: Any = None) -> "ToolResult":
        return cls(status="warning", summary=summary, data=data)

    @classmethod
    def fail(cls, summary: str, *, root_cause: str, retryable: bool = False) -> "ToolResult":
        return cls(
            status="error",
            summary=summary,
            error=ToolError(root_cause=root_cause, retryable=retryable),
        )

    def render(self, max_chars: int = DEFAULT_TOOL_OUTPUT_MAX_CHARS, source: str = "tool") -> str:
        """Render to the model-facing string, applying the uniform output cap."""
        import json as _json

        lines: List[str] = [self.summary]
        if self.status == "error" and self.error is not None:
            lines.append(f"[error] {self.error.root_cause}"
                         + (" (retryable)" if self.error.retryable else ""))
        if self.data is not None:
            body = self.data if isinstance(self.data, str) else _json.dumps(self.data, default=str)
            lines.append(body)
        text = "\n".join(lines)
        capped, was_trunc = cap_text(text, max_chars, source)
        # record-only; we don't re-validate (keeps render side-effect-free for callers)
        object.__setattr__(self, "truncated", was_trunc)
        return capped


class NodeOutput(BaseModel):
    """Typed envelope for a workflow node result.

    ``to_dict`` preserves the loose shape the BFS executor already consumes
    (``status`` + ``output`` + token fields) so nodes can adopt this incrementally
    without breaking the executor's failed-node detection or persistence.
    """

    status: ToolStatus = Field(default="ok")  # type: ignore[valid-type]
    output: str = Field(default="", description="Human-readable node output / answer")
    data: Optional[Any] = Field(default=None, description="Structured payload for downstream nodes")
    error: Optional[str] = Field(default=None)
    truncated: bool = Field(default=False)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    def to_dict(self) -> Dict[str, Any]:
        # 'failed' is the sentinel the executor's failed-node detection looks for.
        status = "failed" if self.status == "error" else self.status
        out: Dict[str, Any] = {"status": status, "output": self.output}
        if self.data is not None:
            out["data"] = self.data
        if self.error:
            out["error"] = self.error
        if self.truncated:
            out["truncated"] = True
        out["input_tokens"] = self.input_tokens
        out["output_tokens"] = self.output_tokens
        out["total_tokens"] = self.total_tokens
        return out
