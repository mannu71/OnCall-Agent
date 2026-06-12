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

from typing import Any, Dict, Optional, Tuple

from app.core.redact import redact


async def build_recall_query(
    *,
    user_query: str,
    cloudwatch_config: Optional[Dict[str, Any]],
    logger_instance: Any,
    execution_id: Optional[str],
) -> Tuple[str, int]:
    """Return ``(augmented_query, recall_hits)`` with a KB recall block prepended."""
    recall_hits = 0
    augmented_query = user_query
    try:
        from app.services.knowledge_base import knowledge_base as _kb
        from app.workflow.strategies.react.helpers import build_recall_context

        _issues = await _kb.search_known_issues(user_query, limit=3, threshold=0.65)
        _patterns = await _kb.search_similar_patterns(user_query, limit=3, threshold=0.65)
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
        _skills = await _kb.recall_skills_for_agent(user_query, limit=3)
        recall_hits = len(_issues) + len(_patterns) + len(_skills)
        recall_block = build_recall_context(_issues, _patterns, _skills)
        if recall_block:
            augmented_query = f"{recall_block}\n\n---\n\n{user_query}"
    except Exception as _recall_err:  # noqa: BLE001 — recall is best-effort
        logger_instance.warning(
            "ReactStrategy: KB recall failed (non-fatal): %s",
            redact(str(_recall_err)),
            extra={"execution_id": execution_id},
        )
    return augmented_query, recall_hits


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
    from app.workflow.strategies.react.helpers import cap_context_block

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
    from app.workflow.strategies.react.helpers import looks_like_midthought

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
