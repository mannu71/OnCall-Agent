"""PII boundary, quality/action supervisors, self-improvement, report loops.

One slice of :class:`app.config.Settings`. Mixins carry no behaviour of
their own — they exist so 200+ fields are readable in domain-sized files.
Settings inherits every one of them, so the flat ``settings.<field>``
surface every call site already uses is unchanged.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from app.config._shared import DEFAULT_DELEGATION_BLOCKED_TOOLS  # noqa: F401


class GovernanceSettings(BaseSettings):
    """PII boundary, quality/action supervisors, self-improvement, report loops."""

    guardrail_hard_stop: bool = False

    # ── PII pseudonymization (privacy boundary before Bedrock) ───────────────
    # Detect PII in everything bound for the model and swap in stable, reversible
    # placeholders ([EMAIL_1] …); the final answer is re-hydrated for the user.
    # ON by default — this is a compliance control for a KYC product. Set the
    # flag False to disable, or trim the entity set, without a redeploy.
    pii_pseudonymization_enabled: bool = Field(
        default=True, validation_alias="PII_PSEUDONYMIZATION_ENABLED"
    )

    pii_entity_types: List[str] = Field(
        default_factory=lambda: ["EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IP", "ACCOUNT_ID"],
        validation_alias="PII_ENTITY_TYPES",
    )

    # ── Self-improvement (hill-climbing) loop ────────────────────────────────
    # OFF by default. When enabled, an on-demand analyzer samples recent execution
    # traces and proposes prompt/tool/skill refinements as DRAFTS for operator
    # approval — it never auto-applies anything.
    self_improvement_enabled: bool = Field(
        default=False, validation_alias="SELF_IMPROVEMENT_ENABLED"
    )

    self_improvement_sample: int = Field(
        default=30, validation_alias="SELF_IMPROVEMENT_SAMPLE"
    )

    # Loop 4 — hill-climbing auto-apply (OFF by default).
    # When True the scheduler job promotes safe proposals (skill drafts,
    # reliability notes) to active after passing the eval guardrail.
    # 'prompt' / 'policy' proposals are always left as human-reviewed drafts.
    hillclimb_apply_enabled: bool = Field(
        default=False, validation_alias="HILLCLIMB_APPLY_ENABLED"
    )

    # ── Failure->governance conversion (app.core.improvement.analyzer) ──────
    # OFF by default. When True: (1) analyze_recent() upserts its batch's
    # error fingerprints into failure_ledger (migration 030); (2) a fingerprint
    # recurring >= governance_convert_threshold times becomes an 'eval'-kind
    # draft proposal (convert_recurring_failures) that apply.py can
    # auto-apply — bookkeeping only (marks the ledger row 'converted'), never
    # fabricates eval assertions.
    governance_conversion_enabled: bool = Field(
        default=False, validation_alias="GOVERNANCE_CONVERSION_ENABLED"
    )

    governance_convert_threshold: int = Field(
        default=3, validation_alias="GOVERNANCE_CONVERT_THRESHOLD"
    )

    # Supervisor
    supervisor_max_retries: int = 1

    supervisor_pass_threshold: float = 0.60

    supervisor_hitl_threshold: float = 0.50

    supervisor_hitl_enabled: bool = True

    supervisor_llm_scoring: bool = False

    # Ceiling on a run's cumulative tokens; crossing it stops the supervisor from
    # spending another retry. Raised from 100_000 on 2026-07-23: that figure was
    # set when the loop could only see the parent agent's counters, which on a
    # delegating run is ~10% of the real total (measured: execution 240 recorded
    # 119K against ~1.05M actually spent). Now that subagent usage is folded in
    # (app.harness.usage_ledger), 100_000 would be exceeded by a single ordinary
    # turn and no run would ever earn a retry. This is a retry brake, not an
    # in-flight cap — a turn already under way still runs to completion, so it
    # bounds the run at roughly this figure plus one turn.
    supervisor_token_budget: int = 1_200_000

    # Defensive wall-clock ceiling for the supervisor retry loop (seconds).
    # Independent of max_retries/token_budget — guarantees the loop terminates.
    supervisor_wall_clock_seconds: float = 900.0

    # ── Action Supervisor (pre-execution write gate) ──────────────────────────
    # Distinct from the post-run quality Supervisor above: this reviews each
    # intercepted write-class action BEFORE it runs. Default off; ships in
    # shadow mode first (review + record verdict, but the human/timeout still
    # decides) so the reviewer can be calibrated against real decisions.
    action_supervisor_enabled: bool = False

    action_supervisor_shadow_mode: bool = True

    action_supervisor_timeout_seconds: float = 20.0

    # Risk tiers (CSV fnmatch patterns). Low-risk → the Supervisor LLM decides;
    # high-risk → escalates to a human (Supervisor verdict shown as advisory).
    # Anything gated-but-untier'd is treated as high (fail toward the human).
    action_supervisor_low_risk_patterns: str = (
        "fs_write*,fs_append,fs_upsert,fs_prune,save_playbook,patch_playbook,"
        "pin_fact,write_todos"
    )

    action_supervisor_high_risk_patterns: str = (
        "edit_file,create_file,apply_patch,run_command,run_verify,"
        "delegate_investigation,wiki_publish,skill_write"
    )

    # Gate the wiki-publish output-node handler through the approval primitive.
    wiki_publish_approval_enabled: bool = False

    # ── Cadenced report-only loops ────────────────────────────────────────────
    # Optional webhook a scheduled (cron-fired) run POSTs a compact result
    # report to (app.core.observability.notify.post_run_report). Empty = off (no report sent);
    # any delivery failure is swallowed. The report-only "L1" loop pattern: the
    # cron fires an investigation that REPORTS rather than acts.
    loop_report_webhook_url: str = Field(
        default="", validation_alias="LOOP_REPORT_WEBHOOK_URL"
    )

    # Carry a scheduled loop's own progress ledger across fires: each cron fire
    # of workflow <name> resumes the metamemory ledger keyed "workflow:<name>"
    # (reuses the Phase-2 session-persistence machinery). Off by default; also
    # requires the metamemory + postgres-scratch prerequisites to have any
    # effect. Toggle in Settings → Feature flags.
    loop_state_continuity_enabled: bool = Field(
        default=False, validation_alias="LOOP_STATE_CONTINUITY_ENABLED"
    )
