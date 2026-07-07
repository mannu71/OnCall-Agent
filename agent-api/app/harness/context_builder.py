"""Query/context augmentation for the harness.

Two behaviour-preserving primitives extracted from ``ReactStrategy.execute``:

  * :func:`build_recall_query` — prepend a knowledge-base recall block (similar
    past issues / patterns / executable skills) to the user query so the agent
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

from app.core.redact import redact


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


async def build_recall_query(
    *,
    user_query: str,
    cloudwatch_config: Optional[Dict[str, Any]],
    logger_instance: Any,
    execution_id: Optional[str],
    code_analyzer_config: Optional[Dict[str, Any]] = None,
    memory_enabled: bool = False,
    allowed_skills: Optional[list] = None,
    has_history: bool = False,
) -> Tuple[str, int, list]:
    """Return ``(augmented_query, recall_hits, selected_skills)`` with blocks prepended.

    Prepends, in priority order under a per-turn token budget: (a') a follow-up
    directive (when ``has_history``), (a) always-injected pinned facts, (b)
    bank-scoped learned semantic memory (when enabled), (c) the KB recall block
    (issues/patterns/skills), and (d) RAG-auto-selected markdown skills.
    ``selected_skills`` is the list of auto-selected skill names (for the UI
    badge). Every leg is best-effort — a failure never blocks the run.

    ``allowed_skills``: when non-empty (an agent has skills explicitly picked on
    its node), both the DB-skill recall and the markdown auto-select are
    restricted to that set by name. ``None``/empty = no scoping — the global
    skill library applies (backward-compatible default).

    ``has_history``: True when the chat turn carries prior conversation history
    (``execute_agent``'s ``conversation_history`` replay is non-empty). Prepends
    a short directive to the USER TURN (never the cached system prompt, so the
    CACHE CONTRACT holds) telling the model to answer from the conversation
    above rather than restarting the investigation — mirrors the corresponding
    pre-scan skip in the CloudWatch node handlers (``is_chat_turn`` — any chat
    turn now lets the agent route CloudWatch tool calls itself instead of a
    keyword-gated deterministic re-scan).
    """
    # Query-aware gate: a purely conversational turn (e.g. "Hi", "thanks") needs
    # no institutional memory — skip the pinned/semantic/KB/skill recalls (several
    # DB + vector lookups) entirely. Mirrors the CloudWatch tool-binding skip in
    # ``tool_assembler``. Guarded on ``not cloudwatch_config`` so a CW-configured
    # node's behaviour is never altered.
    from app.core.intent import is_conversational
    if not cloudwatch_config and is_conversational(user_query):
        return user_query, 0, []

    _allowed_set = set(allowed_skills) if allowed_skills else None
    from app.config import settings
    recall_hits = 0
    repos = repos_from_code_analyzer(code_analyzer_config)
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
            if getattr(settings, "pinned_facts_enabled", True):
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
            if memory_enabled or settings.semantic_memory_enabled:
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

    # ── (c) KB recall (known issues / similar patterns) ──────────────────────
    async def _recall_kb() -> Tuple[Optional[str], int]:
        try:
            from app.services.knowledge_base import knowledge_base as _kb
            from app.harness.helpers import build_recall_context

            # The two base lookups are independent — fan them out concurrently.
            _issues, _patterns = await asyncio.gather(
                _kb.search_known_issues(user_query, limit=3, threshold=0.65),
                _kb.search_similar_patterns(user_query, limit=3, threshold=0.65),
            )
            # CloudWatch log-group patterns depend on _patterns for dedupe, so they
            # run after the fan-out and merge in the same order as before.
            if cloudwatch_config:
                _lg_query = " ".join(cloudwatch_config.get("log_groups") or [])
                if _lg_query:
                    _cw_pat = await _kb.search_similar_patterns(
                        _lg_query, limit=2, threshold=0.55,
                    )
                    seen = {p.get("id") for p in _patterns}
                    for _p in _cw_pat:
                        if _p.get("id") not in seen:
                            _patterns.append(_p)
                            seen.add(_p.get("id"))
            hits = len(_issues) + len(_patterns)
            recall_block = build_recall_context(_issues, _patterns)
            return (recall_block or None), hits
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

    # ── (c) RAG-auto-selected markdown skills ────────────────────────────────
    selected_skills: list[str] = []
    try:
        if getattr(settings, "skill_rag_selection_enabled", True):
            from app.core.skills import get_default_skill_manager
            _mgr = get_default_skill_manager()
            _hits = _mgr.select_for_query(
                user_query, k=getattr(settings, "skill_rag_k", 2), allowed=_allowed_set,
            )
            if _hits:
                selected_skills = [s.name for s in _hits]
                lines = ["## Suggested skills (auto-selected for this query)"]
                for s in _hits:
                    lines.append(f"- **{s.name}** — {s.description} (follow this runbook's steps)")
                blocks.append("\n".join(lines))
                recall_hits += len(_hits)
    except Exception as _skill_err:  # noqa: BLE001 — skill recall is best-effort
        logger_instance.warning(
            "ReactStrategy: skill auto-selection failed (non-fatal): %s",
            redact(str(_skill_err)),
            extra={"execution_id": execution_id},
        )

    augmented_query = _assemble_within_budget(
        blocks, user_query,
        budget_tokens=getattr(settings, "memory_turn_token_budget", 800),
    )
    return augmented_query, recall_hits, selected_skills


def _assemble_within_budget(blocks: list, user_query: str, *, budget_tokens: int) -> str:
    """Prepend memory *blocks* (priority order) to the query within a token budget.

    Greedily include whole blocks until the budget (chars/4 heuristic) is reached;
    a partially-fitting block is truncated rather than dropped so the highest
    item still contributes (bounded per-turn memory injection).
    """
    augmented = user_query
    if not blocks:
        return augmented
    used = 0
    kept: list[str] = []
    for block in blocks:
        cost = len(block) // 4
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
        augmented_query = f"{block}{augmented_query}"
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

    anomaly_correlation = context.get("anomaly_code_correlation")
    if anomaly_correlation:
        block = cap_context_block("Anomaly-Code Correlation", anomaly_correlation)
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
