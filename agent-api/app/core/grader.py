"""Production LLM-as-judge grader — Loop 2 verification.

grade_answer() scores the agent's final_answer for faithfulness to the
tool-output evidence gathered during the run.  Fail-soft: returns None on
any error so it never breaks the supervisor loop.

Model is DB-resolved via app.crawler.call_llm (same path as every other
production LLM call — never hardcoded).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GRADER_SYSTEM = (
    "You are a strict evaluation judge. You score whether a MODEL OUTPUT is "
    "faithful to the EVIDENCE it was given. Faithful means: every concrete "
    "claim (errors, identifiers, counts, severity, root cause) is supported by "
    "the evidence, nothing material is fabricated, and required points are "
    "covered. Reply with ONLY a JSON object: "
    '{"score": <float 0..1>, "verdict": "faithful|partial|unfaithful", '
    '"reasoning": "<one sentence>"}. '
    "score 1.0 = fully faithful and complete; 0.5 = partially; 0.0 = fabricated "
    "or contradicts the evidence. No prose outside the JSON."
)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    score_m = re.search(r'"score"\s*:\s*([01](?:\.\d+)?)', text or "")
    if score_m:
        verdict_m = re.search(r'"verdict"\s*:\s*"([a-z]+)"', text or "")
        return {
            "score": float(score_m.group(1)),
            "verdict": verdict_m.group(1) if verdict_m else "unknown",
            "reasoning": "(recovered from truncated JSON)",
        }
    return None


def build_evidence(messages: List[Dict[str, Any]], max_chars: int = 8000) -> str:
    """Compact tool-output messages into an evidence string for the grader."""
    parts = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            content = str(msg.get("content", ""))
            if content:
                parts.append(content)
    return "\n---\n".join(parts)[:max_chars]


async def grade_answer(
    *,
    final_answer: str,
    evidence: str,
    requirements: str = "",
) -> Optional[Dict[str, Any]]:
    """Score *final_answer* against *evidence*.

    Returns ``{score, verdict, reasoning}`` or ``None`` when the grader
    cannot run (no evidence, empty answer, model error).  Callers must
    treat ``None`` as "no grader signal" and fall back to heuristics.
    """
    if not (final_answer or "").strip():
        return None
    if not (evidence or "").strip():
        return None

    try:
        from app.crawler.call_llm import call_llm  # DB-resolved model

        human = (
            "EVIDENCE the agent was given:\n```\n"
            + (evidence or "")[:8000]
            + "\n```\n\n"
            "REQUIREMENTS for a faithful answer:\n"
            + (requirements or "(general faithfulness and completeness)")
            + "\n\n"
            "AGENT OUTPUT to score:\n```\n"
            + (final_answer or "")[:6000]
            + "\n```\n\n"
            "Return the JSON verdict now."
        )
        full_prompt = _GRADER_SYSTEM + "\n\n" + human
        text, _in, _out, _cached = await call_llm(
            full_prompt, tier="search", max_tokens=400
        )
        parsed = _extract_json(text) or {}
        score = parsed.get("score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.5
        score = max(0.0, min(1.0, score))
        return {
            "score": score,
            "verdict": parsed.get("verdict", "unknown"),
            "reasoning": parsed.get("reasoning", "(no reasoning returned)"),
        }
    except Exception as exc:  # noqa: BLE001 — never break the loop
        logger.warning("grader: grade_answer failed (%s) — skipping", exc)
        return None
