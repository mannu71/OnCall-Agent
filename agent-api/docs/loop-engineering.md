# Loop Engineering Architecture

Reference: [The Art of Loop Engineering](https://www.langchain.com/blog/the-art-of-loop-engineering) (LangChain, 2024)

## Core idea

Instead of cramming behavior into ever-larger prompts ("prompt engineering"), loop
engineering builds **layered control-flow loops around the model**.  The system prompt
in this codebase carries an explicit *CACHE CONTRACT* (`agent_builder.py`) forbidding
run-specific data: the prompt is deterministic and cached.  Run-specific steering lives
in the loops, not the text.

As measured during the architecture audit: system-prompt text accounts for ~3 % of per-
invocation input tokens.  Loop logic (supervisor verdict, recovery machinery, tool
governance, compaction, disclosure) determines >80 % of agent behavior.

---

## The Four Loops

### Loop 1 — Agent Loop ✅ Mature

The foundational "give the model context + tools, iterate until done" cycle.

| Component | File | What it does |
|---|---|---|
| Supervisor retry loop | `app/harness/supervisor_loop.py` | Bounded `while True`: iteration cap, wall-clock deadline, token budget, supervisor verdict routing (PASS / RETRY / HITL / ESCALATE) |
| ReAct inner loop | `app/workflow/strategies/react/agent_runner.py` | LangGraph ReAct with recursion limit (12 steps ≈ 6 ReAct iterations); post-invocation recovery for truncation, overflow, mid-thought preamble |
| Context compaction | `app/core/compaction/compressor.py` + `app/core/memory/compaction_manager.py` | Pre-invocation LLM summarization; mid-loop deterministic pruning; overflow recovery — all code-driven, not prompt-steered |
| Progressive tool disclosure | `app/harness/tool_disclosure.py` | When >25 deferrable tools or ≥20K tokens: replace with `search_tools` + `call_tool` bridge (BM25 ranking).  Agent discovers tools on demand |

**No action needed — this loop is complete.**

---

### Loop 2 — Verification Loop 🟡 Activated this round

Wraps the agent loop with a quality gate.  Poor output feeds back as targeted retry
guidance; borderline output surfaces to a human (HITL).

| Component | File | What it does |
|---|---|---|
| Heuristic scorer | `app/core/supervisor.py` `_composite_score()` | Weighted score: confidence 40 %, answer richness 25 %, tool health 20 %, certainty 15 % |
| **LLM-judge grader** *(new)* | `app/core/grader.py` | `grade_answer(final_answer, evidence)` — strict JSON `{score, verdict, reasoning}`; model DB-resolved via `call_llm`; fail-soft (never breaks the loop) |
| Supervisor `evaluate()` | `app/core/supervisor.py` | When `llm_scoring_enabled=True`: calls grader, blends score (heuristic 80 % + grader 20 %); enriches retry guidance with `reasoning` |
| Evidence extraction | `app/core/grader.py` `build_evidence()` | Compacts `role=tool` messages from the run into an evidence string for the grader |
| Routing | `app/harness/supervisor_loop.py` | Passes `messages=result["messages"]` to `supervisor.evaluate()` |

**Flags / rollout:**

| Flag | Default | Scope |
|---|---|---|
| `SUPERVISOR_LLM_SCORING` / `settings.supervisor_llm_scoring` | `False` | Global fallback |
| `agent_config["verification_grader"]` or `deep_features["verification_grader"]` | `None` → global | Per-workflow/profile override |

For **new agent profiles**, set `verification_grader: true` in `deep_features` when
calling `PUT /agent-profiles/{name}`.  Existing profiles (null field) fall through to
the global `False` — zero behavior change.

---

### Loop 3 — Event-Driven Loop 🟡 Partial (out of scope this round)

Connects the agent to external triggers so it runs autonomously, not just on demand.

| Component | File | Status |
|---|---|---|
| Cron scheduler | `app/core/scheduler.py` | APScheduler + pg advisory leader lock — cron-triggered workflow runs |
| In-process event bus | `app/core/events.py` | Pub/sub for internal application events |
| Workflow event streaming | `app/core/scheduler.py` `_emit_event()` | SSE fan-out to subscribed clients |

**Gap:** no inbound external trigger ingestion (webhook / Slack / CloudWatch alarm → run).
This is the next natural investment after Loops 2 and 4 are stable.

---

### Loop 4 — Hill-Climbing Loop 🟡 Activated this round

Production traces feed an analyzer that detects quality problems and recommends
improvements.  Safe proposals are auto-applied behind an eval guardrail; risky ones
(prompt/policy changes) remain human-reviewed drafts.

| Component | File | What it does |
|---|---|---|
| Signal computation | `app/core/improvement/analyzer.py` `compute_signals()` | Deterministic: escalation rate, empty/refusal rate, avg tool calls, error fingerprints |
| Proposal generation | `app/core/improvement/analyzer.py` `analyze_recent()` | LLM proposals (DB-resolved) + heuristic backstop; all land as `status: "draft"` |
| **Proposal applier** *(new)* | `app/core/improvement/apply.py` `apply_proposals()` | Applies `skill` → confidence-gated draft via `SkillService`; `reliability` → audit log; `prompt`/`policy` → human-reviewed drafts only |
| **Eval guardrail** *(new)* | `app/core/improvement/guard.py` `run_selftest_guard()` | Runs `evals/harness_selftest.py` (DB-free); blocks apply on regression; falls back to import smoke check when pytest unavailable |
| Scheduler job *(new)* | `app/core/scheduler.py` `_run_hillclimb_wrapper()` | Leader-gated, interval = 2× curator interval; `dry_run` when `HILLCLIMB_APPLY_ENABLED=false` |
| API | `app/api/v1/endpoints/improvement.py` | `GET /improvement/analyze` (unchanged) + `POST /improvement/apply?dry_run=true` (new) |
| Curator (pre-existing) | `app/core/memory/curator.py` | Promotes verified skills and pins frequently-recalled memories — the one existing closed loop |

**Flags / rollout:**

| Flag | Default | Effect |
|---|---|---|
| `SELF_IMPROVEMENT_ENABLED` | `False` | Master gate — must be `True` for anything to run |
| `HILLCLIMB_APPLY_ENABLED` | `False` | When `False` the scheduler runs in dry-run (analyze only, no writes) |

**Enabling for a new deployment:**

```env
SELF_IMPROVEMENT_ENABLED=true
HILLCLIMB_APPLY_ENABLED=true   # only after verifying dry-run output is sensible
```

**Safe proposal kinds** (auto-apply eligible):

- `skill` — creates a `status: draft, confidence: 0.0` skill file.  The curator's
  confidence gate (`skill_confidence_min`) keeps it out of recall until promoted.
- `reliability` — structured log entry only; no state change.

**Human-reviewed only** (never auto-applied):

- `prompt` — touches the CACHE CONTRACT system prompt.
- `policy` — requires operator sign-off via the policy engine.

---

## Rollout policy

| Loop | Existing workflows | New workflows / profiles |
|---|---|---|
| 1. Agent loop | Unchanged | Unchanged |
| 2. Verification grader | OFF (null → global `False`) | Set `deep_features.verification_grader=true` on profile creation |
| 3. Event-driven | Unchanged | Out of scope |
| 4. Hill-climbing | Dry-run only (`HILLCLIMB_APPLY_ENABLED=false`) | Enable per deployment once dry-run output is reviewed |

## Accuracy gate

The merge gate for any change to these loops: **container accuracy eval must hold
1.0000** (`evals/accuracy/runner.py`, run inside the Docker container per
`project_eval_harness` notes).  Run with the grader OFF first (regression baseline)
then with the grader ON for a test workflow.
