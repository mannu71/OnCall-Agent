"""Action Supervisor — pre-execution review of write-class actions.

Distinct from the post-run *quality* supervisor
(:mod:`app.harness.supervisor_loop` / :mod:`app.core.quality.supervisor`), which scores a
finished answer and can retry. The Action Supervisor here reviews each
intercepted write-class action (file edits, code-gen, run_command, playbook/KB
writes, MCP mutations, wiki publish) *before* it executes, via the shared
ask-gate primitive (:func:`app.harness.tool_permissions.request_action_approval`).

Tiered authority: low-risk actions may be auto-decided by the Supervisor LLM;
high-risk actions escalate to a human, with the Supervisor's verdict attached to
the approval card as advice. See :mod:`app.core.supervision.action_supervisor`.
"""
from app.core.supervision.action_supervisor import (
    Verdict,
    classify_risk_tier,
    is_enabled,
    is_shadow_mode,
    review,
)

__all__ = [
    "Verdict",
    "classify_risk_tier",
    "is_enabled",
    "is_shadow_mode",
    "review",
]
