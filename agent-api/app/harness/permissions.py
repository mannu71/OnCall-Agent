"""Harness permissions entry point.

The allow/ask/deny policy, HITL approval futures, and output-cap wrappers live in
``app.workflow.strategies.react.tool_permissions`` (imported by many call sites).
Rather than relocate that module — which would churn ~35 importers for no
behavioural gain — the harness re-exports its public surface here so new code can
depend on ``app.harness.permissions`` as the stable entry point. Phase 2's later
step (driving the policy from ``AgentSpec.permission_policy``) builds on this.
"""
from __future__ import annotations

from app.workflow.strategies.react.tool_permissions import (  # noqa: F401
    evaluate,
    wrap_tools_with_output_cap,
    wrap_tools_with_permissions,
)

__all__ = ["evaluate", "wrap_tools_with_output_cap", "wrap_tools_with_permissions"]
