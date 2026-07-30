"""CloudWatch analysis, log-watch heartbeat, and query cost controls.

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


class CloudWatchSettings(BaseSettings):
    """CloudWatch analysis, log-watch heartbeat, and query cost controls."""

    # Heartbeat monitor
    heartbeat_seen_ttl_hours: int = 24

    heartbeat_poll_interval: int = 60

    heartbeat_cooldown: int = 300

    heartbeat_max_concurrent: int = 3

    heartbeat_aws_region: str = "us-east-1"

    heartbeat_aws_profile: Optional[str] = None

    heartbeat_db_fallback: bool = True

    log_watch_sse_dedup_max: int = 10000

    cloudwatch_auto_drilldown: bool = True

    cloudwatch_metrics_fusion: bool = True

    # When True, the deterministic cloudwatchAnalyzer node auto-escalates to a
    # contained agent investigation when its triage signals warrant it (high/
    # critical severity, low confidence, or a high-severity alert). Set False to
    # keep the analyzer purely deterministic regardless of node analysis_depth.
    cloudwatch_auto_escalate: bool = True

    # Staged investigation pipeline (run_investigation_pipeline): how many top
    # findings to drill into during Stage 2, and whether the heaviest stage
    # (cross-group correlation) may run. Tunable without a redeploy.
    cloudwatch_pipeline_drilldown_top_n: int = 5

    cloudwatch_pipeline_enable_correlation: bool = True

    # RCA data-fidelity caps (this is an internal root-cause tool — keep enough
    # raw detail that correlation/profile/trace IDs and stack traces survive).
    # Tunable per environment without a redeploy.
    cloudwatch_synthesis_max_chars: int = 24_000   # LLM synthesis payload size

    cloudwatch_example_msg_chars: int = 800        # per-pattern example message

    cloudwatch_drill_sample_chars: int = 1500      # per drill-down sample sent to LLM

    # Pipeline LLM synthesis control. The synthesis is a single Bedrock call that
    # produces the narrative; when an account-level Bedrock guardrail blocks it,
    # it wastes ~24s + tokens every run. Set False to disable entirely; otherwise
    # a circuit breaker skips it after N consecutive guardrail refusals.
    cloudwatch_pipeline_llm_synthesis: bool = True

    cloudwatch_synthesis_guardrail_cooldown: int = 3   # consecutive refusals before skip

    # Reliability (Phase 1). Cap concurrent CloudWatch Logs Insights queries so a
    # wide fan-out (top_n drill-downs × log groups) can't trip StartQuery
    # concurrency limits. Reduced-scope retry gives a failed analyzer one more
    # chance over a halved window (transient errors only) before the run is
    # marked partial. Per-analyzer cache TTLs let cheap-to-stale data (alarms,
    # log-group discovery) cache longer than fast-moving error patterns.
    cloudwatch_insights_max_concurrency: int = 3

    cloudwatch_reduced_scope_retry: bool = True

    cloudwatch_cache_ttl_alarms: int = 300

    cloudwatch_cache_ttl_logs: int = 60

    # Accuracy (Phase 2). Deterministic drill-down scoring ranks candidates by
    # severity × volume/spike × evidence-grade instead of a flat z-score/count
    # sort. Alarm history surfaces flapping alarms (DescribeAlarmHistory).
    # Metrics discovery (ListMetrics) lets the agent find metrics for a log group
    # without hand-written MetricDataQuery dicts. Insights query fix-up auto-adds
    # a missing `| limit` clause instead of rejecting the query outright.
    cloudwatch_drilldown_scoring: bool = True

    cloudwatch_alarm_history: bool = True

    cloudwatch_metrics_discovery: bool = True

    cloudwatch_insights_query_fixup: bool = True

    # Pattern ranking (Phase 6). Unique patterns are selected by volume alone
    # today: analyze_log_patterns sorts count-desc and keeps 15, then the
    # summariser head-slices 12 — so a one-off FATAL never reaches the model,
    # and patterns carry no severity at all (leaving the severity term in
    # score_drill_target inert for them). With this on, patterns are labelled
    # with a derived severity, the upstream candidate pool widens to
    # cloudwatch_pattern_candidates, and the surviving 12 are chosen by
    # severity × rarity-aware volume × evidence grade. Off = byte-identical
    # legacy behaviour.
    cloudwatch_pattern_ranking: bool = False

    cloudwatch_pattern_candidates: int = 40

    # Severity-aware budget allocation. _budget_text is a positional cut: when a
    # summary overruns its token budget the tail is discarded regardless of what
    # is in it, so a rare root-cause exception that ranking correctly promoted
    # can still be deleted at serialisation time (measured live 2026-07-27:
    # RejectedExecutionException and TransportRequestHandler both ranked into the
    # payload, then fell off the 1500-token cut). With this on, the pattern list
    # is fitted to the budget by severity share first, so overflow drops the
    # least diagnostic entries instead of the last ones. Requires
    # cloudwatch_pattern_ranking (severity labels come from it).
    cloudwatch_pattern_budget_allocation: bool = False

    cloudwatch_budget_share_error: float = 0.70

    cloudwatch_budget_share_warn: float = 0.20

    # Cost & token controls (Phase 3). Every run aggregates bytes scanned by
    # Insights and estimates USD (insights_cost_per_gb). A soft per-run scan
    # budget degrades remaining drill-downs to a sampled window once exceeded
    # (recorded as budget_limited) rather than failing. A generous in-process
    # token-bucket rate limiter on StartQuery only bites under heavy fan-out.
    cloudwatch_max_gb_scanned_per_run: float = 5.0

    cloudwatch_insights_cost_per_gb: float = 0.005

    cloudwatch_ratelimit_startquery_rps: float = 5.0

    # Feature breadth (Phase 4). Metrics are a first-class analysis type (uses
    # ListMetrics discovery to build queries when none are hand-written). Time
    # ranges beyond 24h are split into sequential buckets (newest-first, early
    # stop once enough events are gathered). The hard cap defaults to 24h; raise
    # cloudwatch_max_time_range_minutes (up to 10080 = 7d) to enable longer
    # ranges — bucketing then keeps each Insights query within bucket_minutes.
    cloudwatch_max_time_range_minutes: int = 1440

    cloudwatch_bucket_minutes: int = 1440

    # Multi-region fan-out (Phase 5). When a node lists more than one region the
    # investigation runs per-region triage (bounded by this cap), namespaces all
    # findings as "region:log_group", and merges into one evidence bundle for a
    # single synthesis. One bad region degrades to partial coverage, not a failed
    # run. Cross-account (assume-role) is intentionally out of scope here.
    cloudwatch_max_regions_per_run: int = 3
