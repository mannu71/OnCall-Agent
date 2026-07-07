"""Runtime-editable feature-flag overlay.

Feature flags are declared in ``app.config.Settings`` and default from environment
variables. This module lets an operator override them at runtime from the Settings
UI without a container restart, following the same persist-then-apply pattern used
for the global timezone (``app.core.app_timezone``):

  * Overrides are stored in the generic ``app_settings`` key/value table under the
    ``flag:`` prefix (migration 011 — no new table needed).
  * On startup (and on every write) the stored overrides are applied in-place to
    the process-wide ``settings`` singleton, so all existing ``settings.<flag>``
    reads transparently see the effective value.

Single-worker assumption: the shipped deployment runs ``UVICORN_WORKERS=1``, so the
singleton is the source of truth. If multiple workers are ever enabled, a write in
one worker won't propagate to the others until they reload — acceptable for these
opt-in toggles, and callable via ``load_overlay()`` on demand.

Infrastructure/security/startup-only flags (API auth, secrets manager, OTEL,
startup indexing recovery, supervisor HITL) are intentionally NOT exposed here —
they must stay environment-controlled.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.infrastructure.persistence import app_settings_repository

logger = logging.getLogger(__name__)

_DB_PREFIX = "flag:"

# Declarative catalog. ``key`` matches a ``Settings`` attribute exactly; ``type``
# drives coercion + the UI control. Grouped for display. Keep this list as the
# single source of truth — the API and UI both render from it.
FLAG_CATALOG: List[Dict[str, Any]] = [
    # ── Tool-output compression ──
    {"key": "compression_enabled", "type": "bool", "group": "Compression",
     "label": "Compress MCP tool output",
     "help": "Send large MCP tool results through the compression sidecar before they enter context."},
    {"key": "compression_all_tools", "type": "bool", "group": "Compression",
     "label": "Compress all tool output",
     "help": "Extend compression to non-MCP tools (CloudWatch, DB, crawler). Biggest win for log-heavy runs."},
    {"key": "compression_min_chars", "type": "int", "group": "Compression",
     "label": "Min chars to compress",
     "help": "Outputs below this size skip compression (they gain little and add latency)."},
    {"key": "compression_timeout_ms", "type": "int", "group": "Compression",
     "label": "Sidecar timeout (ms)",
     "help": "Hard timeout for one compress call; on expiry it falls back to truncation."},
    {"key": "compression_max_retries", "type": "int", "group": "Compression",
     "label": "Sidecar retries",
     "help": "Retries on transient sidecar errors. Keep low — the truncation fallback is always correct."},
    {"key": "compression_model_hint", "type": "str", "group": "Compression",
     "label": "Tokenizer model hint",
     "help": "Model id the sidecar uses to pick a tokenizer. Leave blank for the neutral default."},

    # ── Memory & learning ──
    {"key": "semantic_memory_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Semantic memory recall",
     "help": "Retrieve bank-scoped semantic memories (hybrid FTS+vector) per turn."},
    {"key": "memory_fact_extraction_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Durable-fact extraction",
     "help": "After each turn, extract a few durable facts into semantic memory."},
    {"key": "memory_audit_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Memory audit / consolidation",
     "help": "Let the curator run LLM consolidation of stored memories."},
    {"key": "pinned_facts_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Pinned facts",
     "help": "Inject the operator-pinned facts memory tier into context."},
    {"key": "metamemory_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Metamemory summary",
     "help": "Let the agent maintain a context summary that supersedes LLM compaction."},
    {"key": "context_references_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Context references",
     "help": "Attach node-driven context references to the run."},

    # ── Agent behaviour ──
    {"key": "agent_planning_enabled", "type": "bool", "group": "Agent behaviour",
     "label": "Plan → execute → verify",
     "help": "Add the planning discipline and planning tools to the system prompt."},
    {"key": "skill_rag_selection_enabled", "type": "bool", "group": "Agent behaviour",
     "label": "RAG skill selection",
     "help": "Select relevant skills via retrieval instead of always injecting all."},
    {"key": "pii_pseudonymization_enabled", "type": "bool", "group": "Agent behaviour",
     "label": "PII pseudonymization",
     "help": "Pseudonymize PII in flagship/KYC flows before it reaches the model."},
    {"key": "vfs_session_persistence_enabled", "type": "bool", "group": "Agent behaviour",
     "label": "Persist virtual filesystem",
     "help": "Keep the agent's virtual filesystem across turns in a session."},

    # ── Delegation / subagents ──
    {"key": "delegation_child_timeout_seconds", "type": "int", "group": "Delegation & subagents",
     "label": "Subagent time budget (s)",
     "help": "Max wall-clock a delegated subagent runs before it's cut off. Raise it if subagents time out on slow tools (e.g. codegraph on a large repo)."},
    {"key": "delegation_max_concurrent", "type": "int", "group": "Delegation & subagents",
     "label": "Max parallel subagents",
     "help": "How many subagents run at once for delegate_parallel/batch. Lower it (e.g. 1) to avoid duplicate slow work."},

    # ── Reliability ──
    {"key": "routing_fallback_enabled", "type": "bool", "group": "Reliability",
     "label": "Model routing fallback",
     "help": "On Bedrock errors, fall back across credential/region/model chains."},

    # ── LangGraph runtime (performance) ──
    {"key": "agent_durability", "type": "str", "group": "LangGraph runtime",
     "label": "Checkpoint durability",
     "help": "How often a LangGraph run checkpoints to Postgres. 'async' (default) writes each superstep in the background; 'exit' writes only once at the end — cuts per-turn I/O for runs that never resume; 'sync' blocks each write. HITL runs are auto-clamped to 'async'. Valid: sync | async | exit."},
    {"key": "agent_max_concurrency", "type": "int", "group": "LangGraph runtime",
     "label": "Max superstep concurrency",
     "help": "Cap on concurrent tasks within a superstep (parallel tool calls / Send fan-out). 0 = unbounded (default). Set >0 to bound fan-out against Bedrock rate limits."},
    {"key": "checkpoint_shallow", "type": "bool", "group": "LangGraph runtime",
     "label": "Shallow checkpointer",
     "help": "Keep only the latest checkpoint per thread (drops time-travel history). HITL pause/resume still works. Takes effect on next restart (checkpointer is built at startup)."},
    {"key": "bedrock_max_pool_connections", "type": "int", "group": "LangGraph runtime",
     "label": "Bedrock HTTP pool size",
     "help": "botocore connection-pool size for the shared Bedrock client (default 10). Raise (e.g. 50) for wide parallel tool / delegate_parallel fan-out. Takes effect on next LLM build."},
    {"key": "bedrock_latency_optimized", "type": "bool", "group": "LangGraph runtime",
     "label": "Bedrock latency-optimized",
     "help": "Request Bedrock latency-optimized inference (performance_config). Support is model/region-dependent — leave off unless your model/region supports it. Takes effect on next LLM build."},
    {"key": "agent_stream_mode_enabled", "type": "bool", "group": "LangGraph runtime",
     "label": "Lightweight streaming (stream_mode)",
     "help": "Use astream(stream_mode) instead of astream_events(v2) for token/tool streaming. Also returns complete final state (incl. tool results). Behaviour-preserving; eval-gated."},
    {"key": "subagent_compiled_cache_enabled", "type": "bool", "group": "LangGraph runtime",
     "label": "Cache compiled subagents",
     "help": "Reuse a compiled child agent across delegate calls instead of rebuilding it each time. Only children with an explicit per-def model are cached. Speeds up repeated / parallel delegation to the same specialist."},

    # ── Self-improvement & governance ──
    {"key": "self_improvement_enabled", "type": "bool", "group": "Self-improvement & governance",
     "label": "Self-improvement analyzer",
     "help": "Compute hill-climbing improvement signals from run history."},
    {"key": "hillclimb_apply_enabled", "type": "bool", "group": "Self-improvement & governance",
     "label": "Apply improvement proposals",
     "help": "Allow accepted improvement proposals to be applied automatically."},
    {"key": "governance_conversion_enabled", "type": "bool", "group": "Self-improvement & governance",
     "label": "Recurring-failure governance",
     "help": "Convert recurring tool failures into governance/eval proposals."},
    {"key": "token_economy_enabled", "type": "bool", "group": "Self-improvement & governance",
     "label": "Token economy (delegation budgets)",
     "help": "Grant per-branch token budgets to delegated subagents."},

    # ── Chat history & telemetry ──
    {"key": "chat_tool_history_max_tokens", "type": "int", "group": "Chat history & telemetry",
     "label": "Chat tool-replay budget (tokens)",
     "help": "How many tokens of prior tool activity to replay on a follow-up question."},
    {"key": "chat_tool_history_max_executions", "type": "int", "group": "Chat history & telemetry",
     "label": "Chat tool-replay executions",
     "help": "Max prior tool executions replayed on a follow-up question."},
    {"key": "step_events_enabled", "type": "bool", "group": "Chat history & telemetry",
     "label": "Per-step telemetry events",
     "help": "Record per-turn/per-tool step events (batched at run end)."},

    # ── Code semantic search (ONNX embeddings) ──
    # The feature is always on and always uses snowflake-arctic-embed-s with its
    # model files vendored locally, so the enable toggle, model picker, and
    # download switch are intentionally NOT exposed — only the tuning knobs are.
    {"key": "code_semantic_body_max_chars", "type": "int", "group": "Code semantic search",
     "label": "Source body chars embedded",
     "help": "How much of each function's real source is folded into its embedding (0 = name/signature only). Biggest accuracy lever; higher = slower index."},
    {"key": "code_semantic_test_penalty", "type": "float", "group": "Code semantic search",
     "label": "Test result penalty",
     "help": "Score multiplier for test files when the query isn't about tests (1.0 = no penalty). De-prioritizes tests without hiding them."},
    {"key": "code_semantic_batch_size", "type": "int", "group": "Code semantic search",
     "label": "Embedding batch size",
     "help": "Texts per ONNX forward pass during indexing. Higher = faster but more memory."},
]

_CATALOG_BY_KEY = {spec["key"]: spec for spec in FLAG_CATALOG}

# Pristine environment/default values, snapshotted at import BEFORE any overlay is
# applied, so the UI can offer "reset to default".
_DEFAULTS: Dict[str, Any] = {spec["key"]: getattr(settings, spec["key"]) for spec in FLAG_CATALOG}


def _coerce(spec: Dict[str, Any], raw: Any) -> Any:
    t = spec["type"]
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(raw)
    if t == "float":
        return float(raw)
    return "" if raw is None else str(raw)


def _apply(key: str, value: Any) -> None:
    setattr(settings, key, value)


async def load_overlay() -> Dict[str, Any]:
    """Read stored overrides and apply them to the settings singleton.

    Best-effort: a failure here must never block startup. Returns the applied
    overrides (for logging/inspection).
    """
    applied: Dict[str, Any] = {}
    try:
        stored = await app_settings_repository.all()
    except Exception as exc:  # noqa: BLE001 — settings overlay must not block startup
        logger.warning("feature_flags: could not load overrides (%s)", exc)
        return applied

    for db_key, raw in stored.items():
        if not db_key.startswith(_DB_PREFIX):
            continue
        key = db_key[len(_DB_PREFIX):]
        spec = _CATALOG_BY_KEY.get(key)
        if spec is None:
            continue
        try:
            value = _coerce(spec, raw)
            _apply(key, value)
            applied[key] = value
        except Exception as exc:  # noqa: BLE001 — skip a bad row, keep the rest
            logger.warning("feature_flags: skipping bad override %s=%r (%s)", key, raw, exc)

    if applied:
        logger.info("feature_flags: applied %d runtime override(s): %s",
                    len(applied), ", ".join(f"{k}={v!r}" for k, v in applied.items()))
    return applied


async def set_flags(updates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Persist and apply a batch of flag updates. Returns the new effective list.

    A value of ``None`` clears the override and restores the environment default.
    Unknown keys are rejected (ValueError) so the API returns 400.
    """
    for key in updates:
        if key not in _CATALOG_BY_KEY:
            raise ValueError(f"Unknown feature flag: {key!r}")

    for key, raw in updates.items():
        spec = _CATALOG_BY_KEY[key]
        db_key = f"{_DB_PREFIX}{key}"
        if raw is None:
            await app_settings_repository.set(db_key, "")  # tombstone → treat as default
            _apply(key, _DEFAULTS[key])
            continue
        value = _coerce(spec, raw)
        await app_settings_repository.set(db_key, str(value))
        _apply(key, value)

    return effective()


def effective() -> List[Dict[str, Any]]:
    """Return the catalog annotated with current value + environment default."""
    out: List[Dict[str, Any]] = []
    for spec in FLAG_CATALOG:
        key = spec["key"]
        out.append({
            "key": key,
            "label": spec["label"],
            "group": spec["group"],
            "type": spec["type"],
            "help": spec["help"],
            "value": getattr(settings, key),
            "default": _DEFAULTS[key],
        })
    return out
