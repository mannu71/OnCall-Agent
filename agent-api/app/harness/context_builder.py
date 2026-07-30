"""Query/context augmentation for the harness.

Two behaviour-preserving primitives extracted from ``ReactStrategy.execute``:

  * :func:`build_recall_query` — prepend a knowledge-base recall block (similar
    past issues / patterns) and the skill map to the user query so the agent
    starts with institutional memory. Best-effort: a KB failure never blocks the
    run.
  * :func:`seed_context_blocks` — prepend any pre-computed analysis the executor
    placed in the context (deterministic CloudWatch synthesis, code analysis,
    anomaly↔code correlation) and surface the CloudWatch synthesis separately so
    the strategy can use it as a guaranteed answer floor.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from app.core.privacy.redact import redact


def repos_from_code_analyzer(code_analyzer_config: Optional[Dict[str, Any]]) -> list:
    """Pull repo names out of a code-analyzer config for memory bank scoping."""
    if not code_analyzer_config:
        return []
    repos = code_analyzer_config.get("repos") or []
    names = []
    for r in repos:
        name = r.get("name") if isinstance(r, dict) else (r if isinstance(r, str) else None)
        if name:
            names.append(str(name))
    return names


def build_skill_map_block(skill_map: str) -> str:
    """Wrap a ``SkillManager.build_map`` result as the model-facing ``# Skill map``.

    Stage one of skill disclosure. Split out from :func:`build_recall_query` so
    the trajectory eval injects the identical block rather than a lookalike.
    Empty map → ``""`` (no block).
    """
    if not skill_map:
        return ""
    # Deliberately terse: the protocol (load before other work, never mention a
    # skill without invoking it) already lives in the cached system prompt's
    # "# Skills" section and in both tool descriptions. Repeating it here would
    # be paid on every turn — the exact cost this map exists to remove.
    # NB: emphasis here does NOT substitute for that section winning on order —
    # adding a blocking-requirement clause to this block was measured and did not
    # stop the agent skipping a matching skill; placing "# Skills" AFTER the
    # capability sections in agent_builder is what actually binds it.
    # The how-to line that used to close this block ("call search_skills, then
    # skill(...), /<name> invokes directly") is already in the cached "# Skills"
    # section and in both tool descriptions. Here it was re-bought at full rate on
    # every turn; the map only needs to carry the names.
    return f"# Skill map\nLoadable runbooks, by name: {skill_map}"


async def build_recall_query(
    *,
    user_query: str,
    cloudwatch_config: Optional[Dict[str, Any]],
    logger_instance: Any,
    execution_id: Optional[str],
    code_analyzer_config: Optional[Dict[str, Any]] = None,
    memory_enabled: bool = False,
    memory_types: Optional[frozenset] = None,
    allowed_skills: Optional[list] = None,
    has_history: bool = False,
) -> Tuple[str, int]:
    """Return ``(augmented_query, recall_hits)`` with blocks prepended.

    Prepends, in priority order under a per-turn token budget: (a') a follow-up
    directive (when ``has_history``), (a) always-injected pinned facts, (b)
    bank-scoped learned semantic memory (when enabled), (c) the KB recall block
    (issues/patterns), and (d) the skill map — the names of the skills the agent
    can load (``skill_tool_enabled``), which it resolves via ``search_skills``
    and loads with the ``skill`` tool. Every leg is best-effort.

    ``allowed_skills``: when non-empty (an agent has skills explicitly picked on
    its node), the skill map / search is restricted to that set by name.
    ``None``/empty = no scoping — the global skill library applies
    (backward-compatible default).

    ``has_history``: True when the chat turn carries prior conversation history
    (``execute_agent``'s ``conversation_history`` replay is non-empty). Prepends
    a short directive to the USER TURN (never the cached system prompt, so the
    CACHE CONTRACT holds) telling the model to answer from the conversation
    above rather than restarting the investigation — mirrors the corresponding
    pre-scan skip in the CloudWatch node handlers (``is_chat_turn`` — any chat
    turn now lets the agent route CloudWatch tool calls itself instead of a
    keyword-gated deterministic re-scan).

    ``memory_types``: the active memory tiers (subset of ``semantic``/``pinned``/
    ``kb``) resolved from the wired Memory node. When ``None`` (legacy/non-graph
    callers) the prior behavior is preserved: pinned + KB always inject, semantic
    injects on ``memory_enabled`` or the global flag. When a set is given — the
    graph-driven path — each tier injects ONLY if selected, so an agent with no
    Memory node (empty set) gets no memory injection at all (strict gating).
    """
    # Query-aware gate: a purely conversational turn (e.g. "Hi", "thanks") needs
    # no institutional memory — skip the pinned/semantic/KB/skill recalls (several
    # DB + vector lookups) entirely. Mirrors the CloudWatch tool-binding skip in
    # ``tool_assembler``. Guarded on ``not cloudwatch_config`` so a CW-configured
    # node's behaviour is never altered.
    from app.core.quality.intent import is_conversational
    if not cloudwatch_config and is_conversational(user_query):
        return user_query, 0

    _allowed_set = set(allowed_skills) if allowed_skills else None
    from app.config import settings
    recall_hits = 0
    repos = repos_from_code_analyzer(code_analyzer_config)

    # Resolve which memory tiers are active this turn. ``memory_types is None``
    # is the legacy contract (pinned+KB always, semantic on flag/enabled); a set
    # is the strict graph-driven contract (only the selected tiers inject).
    if memory_types is None:
        _pinned_on = True
        _semantic_on = bool(memory_enabled or settings.semantic_memory_enabled)
        _kb_on = True
    else:
        _types = set(memory_types)
        _pinned_on = "pinned" in _types
        _semantic_on = "semantic" in _types
        _kb_on = "kb" in _types
    # Memory blocks in priority order: pinned (always) > learned semantic > KB.
    # Assembled under a per-turn token budget below.
    blocks: list[str] = []

    if has_history:
        blocks.append(
            "[Follow-up turn — the prior conversation, including any tool results "
            "already gathered, is in the message history above. Answer from it "
            "when possible; only call tools for genuinely NEW information the "
            "user is now asking for.]"
        )

    # ── (a) Pinned facts — always injected, NOT similarity-gated ─────────────
    async def _recall_pinned() -> Tuple[Optional[str], int]:
        try:
            if _pinned_on and getattr(settings, "pinned_facts_enabled", True):
                from app.services.semantic_memory import semantic_memory, format_pinned_block
                _pinned = await semantic_memory.list_pinned(repo=repos or None)
                _pinned_block = format_pinned_block(_pinned)
                if _pinned_block:
                    return _pinned_block, len(_pinned)
        except Exception as _pin_err:  # noqa: BLE001 — best-effort
            logger_instance.warning(
                "ReactStrategy: pinned-facts recall failed (non-fatal): %s",
                redact(str(_pin_err)),
                extra={"execution_id": execution_id},
            )
        return None, 0

    # ── (b) Learned semantic memory ──────────────────────────────────────────
    # Driven by a Memory node connected to the agent (``memory_enabled``). The
    # global ``semantic_memory_enabled`` remains a master enable-all override for
    # non-graph callers; default off so memory is purely node-driven per workflow.
    async def _recall_semantic() -> Tuple[Optional[str], int]:
        try:
            if _semantic_on:
                from app.services.semantic_memory import semantic_memory, format_recall_block
                _mem = await semantic_memory.recall(user_query, repo=repos or None)
                _mem_block = format_recall_block(_mem)
                if _mem_block:
                    return _mem_block, len(_mem)
        except Exception as _mem_err:  # noqa: BLE001 — memory recall is best-effort
            logger_instance.warning(
                "ReactStrategy: semantic memory recall failed (non-fatal): %s",
                redact(str(_mem_err)),
                extra={"execution_id": execution_id},
            )
        return None, 0

    # ── (c) KB recall (OKF knowledge bundle, FTS over the ``kb`` bank) ────────
    # Durable operational knowledge (known issues, log patterns, curated docs)
    # lives in the OKF bundle and is indexed into the ``kb`` memory bank. Recall
    # is Postgres FTS — tags act as paraphrase synonyms. The legacy
    # knowledge_entries/log_patterns tables and their embedding search were
    # removed.
    async def _recall_kb() -> Tuple[Optional[str], int]:
        if not _kb_on:
            return None, 0
        try:
            from app.config import settings as _settings
            if not getattr(_settings, "knowledge_bundle_enabled", True):
                return None, 0
            from app.services.semantic_memory import (
                semantic_memory as _sm, format_recall_block as _fmt,
            )

            _kb_hits = await _sm.recall(user_query, bank="kb")
            # CloudWatch log-group context: recall docs mentioning the watched
            # groups, merged/deduped by id.
            if cloudwatch_config:
                _lg_query = " ".join(cloudwatch_config.get("log_groups") or [])
                if _lg_query:
                    seen = {h.get("id") for h in _kb_hits}
                    for _p in await _sm.recall(_lg_query, bank="kb", k=2):
                        if _p.get("id") not in seen:
                            _kb_hits.append(_p)
                            seen.add(_p.get("id"))
            if not _kb_hits:
                return None, 0
            recall_block = _fmt(_kb_hits).replace(
                "## Learned memory (from past investigations)",
                "## Knowledge base (curated)",
            )
            return (recall_block or None), len(_kb_hits)
        except Exception as _recall_err:  # noqa: BLE001 — recall is best-effort
            logger_instance.warning(
                "ReactStrategy: KB recall failed (non-fatal): %s",
                redact(str(_recall_err)),
                extra={"execution_id": execution_id},
            )
        return None, 0

    # Run the three independent recall legs concurrently; append their blocks in
    # the same fixed priority order (pinned > semantic > KB) the sequential code
    # used, so the assembled prompt is byte-identical. Legs log their own
    # failures; return_exceptions guards against anything they don't catch.
    for _res in await asyncio.gather(
        _recall_pinned(), _recall_semantic(), _recall_kb(),
        return_exceptions=True,
    ):
        if isinstance(_res, BaseException):
            continue
        _block, _hits = _res
        recall_hits += _hits
        if _block:
            blocks.append(_block)

    # ── (d) Skill map ────────────────────────────────────────────────────────
    # Stage one of skill disclosure: the NAMES of the skills this agent can
    # load, and nothing else. Descriptions cost ~2K tokens every turn to answer
    # a question the model can ask on demand instead — so it resolves a name via
    # the ``search_skills`` tool and loads the runbook with ``skill``.
    skill_block: str = ""
    try:
        if bool(getattr(settings, "skill_tool_enabled", True)):
            from app.core.skills import get_default_skill_manager
            _mgr = get_default_skill_manager()
            skill_block = build_skill_map_block(_mgr.build_map(
                allowed=_allowed_set,
                char_budget=int(getattr(settings, "skill_map_char_budget", 1500)),
            ))
    except Exception as _skill_err:  # noqa: BLE001 — the map is best-effort
        logger_instance.warning(
            "ReactStrategy: skill map build failed (non-fatal): %s",
            redact(str(_skill_err)),
            extra={"execution_id": execution_id},
        )

    augmented_query = _assemble_within_budget(
        blocks, user_query,
        budget_tokens=getattr(settings, "memory_turn_token_budget", 800),
    )
    # The map gets its own slot ABOVE the memory blocks — a skill is the
    # actionable procedure for the task, so the agent must see it exists even
    # when the memory budget is full.
    if skill_block:
        augmented_query = f"{skill_block}\n\n---\n\n{augmented_query}"
    return augmented_query, recall_hits


def _assemble_within_budget(blocks: list, user_query: str, *, budget_tokens: int) -> str:
    """Prepend memory *blocks* (priority order) to the query within a token budget.

    Greedily include whole blocks until the budget is reached; a partially-fitting
    block is truncated rather than dropped so the highest item still contributes
    (bounded per-turn memory injection). Sizing uses the shared calibrated
    estimator so this budget agrees with compaction's.
    """
    from app.core.llm.token_estimate import estimate_tokens
    augmented = user_query
    if not blocks:
        return augmented
    used = 0
    kept: list[str] = []
    for block in blocks:
        cost = estimate_tokens(block)
        if not budget_tokens or used + cost <= budget_tokens:
            kept.append(block)
            used += cost
        else:
            remaining = max(0, budget_tokens - used) * 4
            if remaining > 80:  # only worth a partial block if it carries signal
                kept.append(block[:remaining].rstrip() + "…")
            break
    for block in reversed(kept):  # reversed → first block ends up on top
        augmented = f"{block}\n\n---\n\n{augmented}"
    return augmented


#: How to work a pre-computed scan. This used to sit in the cached
#: ``_CLOUDWATCH_SECTION`` — roughly half of it — and was paid on every CloudWatch
#: run even though it only makes sense when a block was actually seeded, which is
#: per-turn state the cached prefix cannot know. It rides with the block instead,
#: so the two are never out of step and a run with no pre-computed scan pays zero.
_PRECOMPUTED_CW_GUIDANCE = (
    "[A deterministic log scan has ALREADY run for this turn. Its results — alarms, "
    "anomalies, error patterns, any drill-down, and a data_quality coverage block — "
    "are in the block below. Treat that as your starting evidence and do NOT re-run "
    "the full scan. Use the live tools only to VERIFY or DRILL DEEPER into specific "
    "findings: cloudwatch_search_logs (drill_down=true) for the raw events behind a "
    "pattern or anomaly, cloudwatch_correlate_logs to trace one request across "
    "groups, cloudwatch_discover_log_groups only if a referenced group is missing.]\n\n"
)


def seed_context_blocks(
    *,
    augmented_query: str,
    context: Dict[str, Any],
) -> Tuple[str, str]:
    """Prepend pre-computed analysis blocks; return ``(augmented_query, cw_synthesis)``.

    ``cw_synthesis`` is the longest CloudWatch synthesis output found, kept
    uncapped so the strategy can fall back to it if the agent's own answer comes
    back empty or truncated.
    """
    from app.harness.helpers import cap_context_block

    cw_context = context.get("cloudwatch_context")
    cw_synthesis: str = ""
    if cw_context:
        block = cap_context_block("Pre-computed CloudWatch Analysis", cw_context)
        augmented_query = f"{_PRECOMPUTED_CW_GUIDANCE}{block}{augmented_query}"
        try:
            if isinstance(cw_context, dict):
                for _entry in cw_context.values():
                    _out = (_entry or {}).get("output") if isinstance(_entry, dict) else None
                    if _out and len(str(_out)) > len(cw_synthesis):
                        cw_synthesis = str(_out)
        except Exception:  # noqa: BLE001 — fallback extraction is best-effort
            cw_synthesis = ""

    code_analyzer_context = context.get("code_analyzer_context")
    if code_analyzer_context:
        block = cap_context_block("Pre-computed Code Analysis", code_analyzer_context)
        augmented_query = f"{block}{augmented_query}"

    vector_memory_context = context.get("vector_memory_context")
    if vector_memory_context:
        block = cap_context_block("Recalled Memory", vector_memory_context)
        augmented_query = f"{block}{augmented_query}"

    return augmented_query, cw_synthesis


def apply_synthesis_floor(
    *,
    result: Dict[str, Any],
    cw_synthesis: str,
    logger_instance: Any,
    execution_id: Optional[str],
) -> Dict[str, Any]:
    """Use the deterministic CloudWatch synthesis as the answer floor.

    If the agent's own answer is empty, a provider refusal/stub, a real
    token-limit truncation, or a SHORT mid-thought fragment, replace it with the
    pre-computed synthesis rather than returning a half-finished investigation. A
    long, substantial narrative is never clobbered. Mutates and returns *result*.
    """
    from app.harness.helpers import looks_like_midthought

    _final_text = (result.get("final_answer") or "").strip()
    # Treat provider refusals ("…cannot answer this question") as non-answers too.
    try:
        from app.workflow.executor.cloudwatch_analysis import is_usable_synthesis
        _agent_unusable = not is_usable_synthesis(_final_text)
    except Exception:  # noqa: BLE001
        _agent_unusable = not _final_text

    _short_fragment = looks_like_midthought(_final_text) and len(_final_text) < 400
    if cw_synthesis and (
        not _final_text
        or result.get("truncated")
        or _agent_unusable
        or _short_fragment
    ):
        logger_instance.info(
            "ReactStrategy: agent answer empty/refusal/truncated/short-fragment "
            "(len=%d) — falling back to pre-computed CloudWatch synthesis "
            "(execution_id=%s)",
            len(_final_text), execution_id,
            extra={"execution_id": execution_id},
        )
        result["final_answer"] = cw_synthesis
        result["cloudwatch_synthesis_fallback"] = True
        result.pop("truncated", None)
    return result
