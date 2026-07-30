"""Agent run budgets, LangGraph runtime knobs, tool exposure, delegation.

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


class AgentSettings(BaseSettings):
    """Agent run budgets, LangGraph runtime knobs, tool exposure, delegation."""

    # ── Deep-agent scratch store backend (todos + VFS) ───────────────────────
    # "memory" (default): process-local dict, same behavior as before this
    # setting existed. "postgres": execution_scratch_store table (migration
    # 029) — needed for horizontal scaling, where a run's tool calls can land
    # on a different replica than the one that wrote earlier scratch state.
    scratch_store_backend: str = Field(
        default="memory", validation_alias="SCRATCH_STORE_BACKEND",
    )

    # ReAct loop bound. LangGraph counts a "step" as one node transition; each
    # ReAct iteration is ~2 steps (agent + tool node), so 25 ≈ 12 iterations.
    # Raised from 12 because code investigations legitimately need more hops
    # (find → paginate get_body → trace → synthesize); at 12 the agent was hit
    # the limit mid-investigation and returned a "Let me search…" preamble.
    # Override via AGENT_RECURSION_LIMIT.
    agent_recursion_limit: int = Field(default=25, validation_alias="AGENT_RECURSION_LIMIT")

    # Non-streaming agent invocation wall-clock cap (agent_runner.invoke_agent).
    # Used by the workflow-executor's non-streaming fallback and by subagent
    # delegation (subagent_factory._run_child calls execute_agent with no
    # stream_callback). Previously hardcoded to 300s with no override, which
    # could silently cut a run short even when a caller's OWN wait_for wrapped
    # it with a higher timeout (e.g. DELEGATION_CHILD_TIMEOUT_SECONDS > 300).
    agent_invoke_timeout_seconds: float = Field(
        default=300.0, validation_alias="AGENT_INVOKE_TIMEOUT_SECONDS"
    )

    # Default per-turn output-token cap for agent/workflow LLM calls. 4096 was
    # too small: the model could exhaust its budget mid-reasoning (right before
    # emitting a tool_use), get cut off with stopReason="max_tokens", and have
    # that truncated half-thought returned as the final answer. 8192 leaves room
    # for reasoning + a tool call in one turn. Override via AGENT_MAX_OUTPUT_TOKENS.
    agent_max_output_tokens: int = Field(default=8192, validation_alias="AGENT_MAX_OUTPUT_TOKENS")

    # ── Engine-level run budgets ──────────────────────────────────────────────
    # A wall-clock deadline threaded INTO the loop itself, distinct from the
    # outer supervisor_wall_clock_seconds (which is only checked BETWEEN agent
    # turns, so a single slow turn or tool run can overshoot it). At ~90% of the
    # deadline the agent gets one graceful "synthesize now" nudge (mirrors the
    # max-turns forced-synthesis rung); at 100% it stops with a partial answer
    # rather than being killed mid-thought — see app.harness.run_budget, which
    # enforces both rungs from the pre-model hook. Defaults to 840s so it fires just
    # before the 900s supervisor ceiling, converting that hard cliff into a
    # graceful partial. Set to 0 to disable. Override via env.
    agent_run_deadline_seconds: float = Field(
        default=840.0, validation_alias="AGENT_RUN_DEADLINE_SECONDS"
    )

    # Per-AGENT total-token ceiling (input+output across all turns of one agent;
    # cache reads and writes are excluded). 0 = disabled. When >0 the agent gets
    # a graceful synthesis nudge at ~90% then stops with stop_reason=token_budget
    # at 100%. Override via env.
    #
    # Enabled 2026-07-23 at a deliberately GENEROUS ceiling. This is the only
    # IN-FLIGHT cost cap in the system: the supervisor's budget only checks
    # between turns, so before this a single agent could run unbounded. It binds
    # per agent because build_run_budget is called from agent_runner.execute_agent,
    # which both the parent and every delegated child flow through — so it caps
    # runaway subagents, which is where the spend actually was (execution 240 had
    # one child burn 302,527 input tokens over 14 tool calls while the parent
    # spent 115,415). 200_000 sits comfortably above a healthy deep investigation
    # and only trips genuine runaways; the 90% nudge means a tripped run still
    # synthesises an answer rather than being cut off mid-thought.
    agent_run_token_budget: int = Field(
        default=200_000, validation_alias="AGENT_RUN_TOKEN_BUDGET"
    )

    # Per-tool-call wall-clock cap (seconds), enforced by app.harness.tool_timeout.
    # 0 = no explicit cap; a tool is then still bounded by whatever remains of
    # agent_run_deadline_seconds above (so a hung tool cannot outlive its run).
    # 240 recommended — matches DELEGATION_CHILD_TIMEOUT_SECONDS. A timed-out
    # tool returns an honest error string to the model and the turn continues;
    # it never aborts the run. The effective cap is always the smaller of this
    # and the remaining run deadline. Override via env.
    agent_tool_call_timeout_seconds: float = Field(
        default=0.0, validation_alias="AGENT_TOOL_CALL_TIMEOUT_SECONDS"
    )

    # Learn a per-model correction factor for the chars/4 token heuristic from
    # actual Bedrock usage (app.core.llm.token_calibration), so compaction thresholds
    # track real token counts. Off by default — an exact no-op (factor 1.0) until
    # enabled and an observation is recorded. Override via env.
    token_estimate_calibration_enabled: bool = Field(
        default=False, validation_alias="TOKEN_ESTIMATE_CALIBRATION_ENABLED"
    )

    mcp_tool_output_max_chars: int = 8000

    # Universal ceiling for ANY single tool result that lacks its own cap
    # (db/edit/playbook/repo StructuredTools). Applied by the policy engine to
    # every tool with a .coroutine (app.core.policy.apply_to_tools ->
    # wrap_tools_with_output_cap). Set to 0 to disable. Override via env.
    #
    # 16 KB (~4K tokens), NOT the former 50 KB (~12.5K tokens). A tool result is
    # the expensive kind of context: it is written to the Bedrock cache at full
    # price once and then RE-READ on every remaining turn of the run, and it
    # pushes the window toward compaction (which costs its own summarization
    # call). Measured 2026-07-21 across 26 sessions: 4.44M input tokens vs 3.44M
    # cache reads — the cached prefix is already cheap, so result volume is where
    # the spend actually is. 16 KB is still 2x the per-family MCP cap above, and
    # the CloudWatch family has run accurately for months on a 1-2.5K TOKEN
    # budget (cloudwatch_sanitizer.py), so this is not a tight ceiling.
    # Truncation appends "narrow the query and call again" advice, which is what
    # makes a lower cap safe: the agent can always re-query for the rest.
    tool_output_max_chars: int = Field(default=16000, validation_alias="TOOL_OUTPUT_MAX_CHARS")

    # When AgentSpec.filesystem is on, a StructuredTool result longer than this
    # is offloaded to the session VFS (app.harness.tool_offload) and replaced
    # with a short handle+preview instead of bloating every subsequent turn's
    # context. Applied BEFORE tool_output_max_chars so an offloaded handle is
    # never itself truncated. Matches the VFS backend's own _OFFLOAD_THRESHOLD
    # default so behavior is identical whether offload happens via this live
    # wrapper or the lower-level vfs_offload_if_large() helper. Set to 0 to
    # disable. Override via env.
    tool_result_offload_chars: int = Field(default=6000, validation_alias="TOOL_RESULT_OFFLOAD_CHARS")

    # Tool exposure: "legacy" keeps today's apply_tool_disclosure behavior
    # (all-or-nothing defer at a count/token threshold) byte-for-byte.
    # "window" switches to app.harness.tool_exposure.ToolExposureManager,
    # which caps the number of non-core tools bound directly on any one
    # assembly to tool_exposure_max — MCP tool-density research reports
    # bound-tool selection accuracy drops below 90% once the active tool
    # count exceeds roughly this range. Core/family tools (CloudWatch,
    # crawler, DB, playbook, delegate, edit, planning, filesystem) are
    # exempt from the cap in both modes; tools outside the window stay
    # reachable via the same search_tools/call_tool bridge. Override via env.
    tool_exposure_mode: str = Field(default="legacy", validation_alias="TOOL_EXPOSURE_MODE")

    tool_exposure_max: int = Field(default=12, validation_alias="TOOL_EXPOSURE_MAX")

    # ── LangGraph runtime perf knobs ─────────────────────────────────────────
    # Checkpoint durability for a LangGraph agent run. "exit" (default) writes
    # once, when the run finishes or pauses — it drops the per-superstep Postgres
    # serialization + I/O that the common run, which never resumes, pays for and
    # never reads. "async" writes each superstep in the background; "sync" blocks
    # each superstep on the write (strongest durability, slowest).
    #
    # Safe as a default because HITL is the only path that resumes, and
    # run_agent_once clamps it: an agent_config with hitl_enabled forces "async"
    # so interrupt-time checkpoints are always written. Set AGENT_DURABILITY=async
    # to restore per-superstep writes everywhere (e.g. if you need a crashed
    # non-HITL run to resume mid-flight rather than restart).
    agent_durability: str = Field(default="exit", validation_alias="AGENT_DURABILITY")

    # Cap on concurrent tasks within a LangGraph superstep (parallel tool calls,
    # Send() fan-out). 0 = unset (today's unbounded behavior); >0 sets the run
    # config's ``max_concurrency`` to bound fan-out against Bedrock rate limits.
    agent_max_concurrency: int = Field(default=0, validation_alias="AGENT_MAX_CONCURRENCY")

    # Swap the shared checkpointer to AsyncShallowPostgresSaver (latest checkpoint
    # per thread only). DELIBERATELY still off, despite being a write-reduction
    # win on paper: langgraph-checkpoint deprecated AsyncShallowPostgresSaver in
    # 2.0.20 and its own deprecation notice names ``durability="exit"`` — which
    # ``agent_durability`` above now defaults to — as the replacement. The two
    # overlap, so turning this on buys little beyond an upgrade liability.
    #
    # Kept as an escape hatch for a deployment that wants bounded checkpoint
    # tables while pinning AGENT_DURABILITY=async. Falls back to the full saver,
    # with a warning, when the class is unavailable.
    checkpoint_shallow: bool = Field(default=False, validation_alias="CHECKPOINT_SHALLOW")

    # Streaming path for a LangGraph agent run. Baked ON (default) to use the
    # lighter astream(stream_mode=["messages","updates"]) loop
    # (execute_agent_stream_v2), which also returns complete final state (incl.
    # ToolMessages) and drops the duplicate full-agent re-run fallback. Set to
    # False via env to fall back to the legacy astream_events(v2) event loop.
    agent_stream_mode_enabled: bool = Field(default=True, validation_alias="AGENT_STREAM_MODE_ENABLED")

    # Cache the compiled child agent across delegate calls instead of rebuilding
    # it (LLM resolution + create_react_agent) on every delegation. Scoped to
    # children with an EXPLICIT per-def model (a fixed DB llm_config) so the
    # baked LLM is stable across runs — children that inherit the parent LLM are
    # never cached (the parent's fallback chain can swap model/region/creds
    # mid-run). LangGraph-engine children only. Baked ON (default) for the
    # repeated/parallel-delegation speedup; set False via env to disable.
    subagent_compiled_cache_enabled: bool = Field(default=True, validation_alias="SUBAGENT_COMPILED_CACHE_ENABLED")

    # Explicit global operator override for EVERY delegated subagent's model —
    # the deliberate "force all subagents onto one model" hammer. A DB
    # llm_config name.
    # Outranks a subagent def's own ``model`` and the inherited parent LLM alike.
    # Deliberately off (None) by default: with no override set, an unpinned child
    # inherits the parent's model, so the workflow's Language Model node wins —
    # NOT a silently-assigned "subagent" gateway role.
    subagent_model_override: Optional[str] = Field(
        default=None, validation_alias="SUBAGENT_MODEL_OVERRIDE"
    )

    # Step-level trajectory events (app.harness.step_recorder, migration 030's
    # trajectory_events table). Off by default: a no-op recorder is used, zero
    # extra DB writes. When on, the agent records one event per
    # model turn / tool call, buffered in memory and flushed as a single batch
    # insert at the end of the run — never a per-step DB round-trip. LangGraph
    # engine runs are not yet instrumented (native-engine-only for now).
    step_events_enabled: bool = Field(default=False, validation_alias="STEP_EVENTS_ENABLED")

    # Persist metamemory files across chat turns (migration 029's
    # execution_scratch_store, keyed "session:<chat_session_id>") instead of the
    # per-run-only default. Default ON but self-gating: a no-op unless
    # scratch_store_backend == "postgres" (the durable table the cross-turn
    # persistence relies on), so memory-backend deployments are unaffected.
    vfs_session_persistence_enabled: bool = Field(
        default=True, validation_alias="VFS_SESSION_PERSISTENCE_ENABLED",
    )

    # Inline ceiling for a tool result stored in ``executions.trajectory`` when
    # it could NOT be offloaded (no chat session, no durable store, or a write
    # failure). This is the historical hardcoded [:2000] made explicit.
    #
    # It is a FALLBACK, not the primary path: a chat run stores the full text
    # via app.harness.tool_result_store and leaves a fetchable pointer, because
    # cutting here destroys the evidence a follow-up turn replays from — the
    # read-side budget (chat_tool_history_max_tokens) then has nothing left to
    # budget. Measured 2026-07-23: one 2,000-char cut erased an entire
    # delegated investigation, and the next turn spent 191,457 tokens redoing it.
    trajectory_tool_result_max_chars: int = Field(
        default=2000, validation_alias="TRAJECTORY_TOOL_RESULT_MAX_CHARS"
    )

    # NER-based PERSON/ADDRESS detection is NOT implemented — app.core.privacy is
    # regex-only. The old ``pii_person_detection`` flag was removed rather than
    # left at False: nothing read it, so setting PII_PERSON_DETECTION=true bought
    # silence instead of detection, which is the wrong failure mode for a
    # compliance control. Re-add it in the same commit that implements the NER
    # path, not before.

    # ── Plan → execute → verify agent loop ───────────────────────────────────
    # When True, multi-step agents are instructed to draft a markdown task list,
    # work it item-by-item, and close with a Verification section. Gated to
    # multi-step mode so trivial single-tool runs are not bloated.
    agent_planning_enabled: bool = Field(
        default=True, validation_alias="AGENT_PLANNING_ENABLED"
    )

    # Verified completion: when True, update_todo refuses to mark a plan item
    # completed without cited evidence (a tool ref / file:line / evidence ID),
    # and the finalizer flags a plan left with incomplete items as UNVERIFIED
    # so a half-finished run can't self-report success. Off by default — only
    # meaningful when the planning tools are in play (AgentSpec.planning).
    todo_evidence_required: bool = Field(
        default=False, validation_alias="TODO_EVIDENCE_REQUIRED"
    )

    # ── Delegation (multi-agent) ─────────────────────────────────────────────
    # Bounds for the orchestrator→specialist delegation layer.  All per-node /
    # per-profile overrides layer on top via spec_factory.resolve_profile_fields.
    delegation_max_depth: int = Field(
        default=1, validation_alias="DELEGATION_MAX_DEPTH"
    )

    delegation_max_concurrent: int = Field(
        default=3, validation_alias="DELEGATION_MAX_CONCURRENT"
    )

    # 240s (was 180s): a code/DB-investigation subagent legitimately chains
    # several codegraph + SQL calls; 180s cut it short once even one search hit
    # the old 120s per-call cap. Kept BELOW agent_invoke_timeout_seconds (300s,
    # the inner non-streaming cap) so this wait_for fires first and the child's
    # timeout-recovery envelope (partial result, "tools available but slow") is
    # returned cleanly instead of the inner invoke being killed mid-flight.
    delegation_child_timeout_seconds: float = Field(
        default=240.0, validation_alias="DELEGATION_CHILD_TIMEOUT_SECONDS"
    )

    delegation_output_max_chars: int = Field(
        default=8000, validation_alias="DELEGATION_OUTPUT_MAX_CHARS"
    )

    # CSV of fnmatch patterns always stripped from child tool sets.
    # Children are investigate/read-only by default; orchestrator owns mutations.
    # fs_write*/fs_append/fs_upsert/fs_prune together cover every VFS mutation
    # (metamemory files included) — children keep fs_read/fs_ls/fs_grep only.
    delegation_blocked_tools: str = Field(
        default=DEFAULT_DELEGATION_BLOCKED_TOOLS,
        validation_alias="DELEGATION_BLOCKED_TOOLS",
    )

    # ── Metabolic token economy (app.harness.budget_ledger) ─────────────────
    # OFF by default — a no-op ledger is used, zero behavior change. Applied at
    # the granularity delegate_batch actually supports (between children, not
    # mid-run): each specialist branch gets an initial energy grant, spends it
    # as its children run, earns more on success, drifts its role state (phi)
    # explore<->exploit, and triggers lifecycle turnover (half its remaining
    # energy returns to the communal pool; a fresh scout profile replaces it)
    # once energy is exhausted or it stalls repeatedly.
    token_economy_enabled: bool = Field(
        default=False, validation_alias="TOKEN_ECONOMY_ENABLED"
    )

    token_economy_run_budget: int = Field(
        default=200_000, validation_alias="TOKEN_ECONOMY_RUN_BUDGET",
    )

    token_economy_base_grant: int = Field(
        default=40_000, validation_alias="TOKEN_ECONOMY_BASE_GRANT",
    )

    token_economy_energy_min: int = Field(
        default=5_000, validation_alias="TOKEN_ECONOMY_ENERGY_MIN",
    )

    token_economy_stall_limit: int = Field(
        default=3, validation_alias="TOKEN_ECONOMY_STALL_LIMIT",
    )

    # ── Configurable agent persona / context document ───────────────────────
    # Optional operator-set persona (tone/role/standards) and a context doc,
    # prepended to the system prompt. Empty by default → prompt unchanged.
    agent_persona: str = Field(default="", validation_alias="AGENT_PERSONA")

    agent_context_doc: str = Field(default="", validation_alias="AGENT_CONTEXT_DOC")
