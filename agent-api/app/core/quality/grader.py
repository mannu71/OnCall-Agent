"""Production LLM-as-judge grader — Loop 2 verification.

grade_answer() scores the agent's final_answer for faithfulness to the
tool-output evidence gathered during the run.  Fail-soft: returns None on
any error so it never breaks the supervisor loop.

Model is DB-resolved via app.core.llm.call_llm (same path as every other
production LLM call — never hardcoded).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.core.llm.json_extract import extract_json_object

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
    obj = extract_json_object(text)
    if obj is not None:
        return obj
    score_m = re.search(r'"score"\s*:\s*([01](?:\.\d+)?)', text or "")
    if score_m:
        verdict_m = re.search(r'"verdict"\s*:\s*"([a-z]+)"', text or "")
        return {
            "score": float(score_m.group(1)),
            "verdict": verdict_m.group(1) if verdict_m else "unknown",
            "reasoning": "(recovered from truncated JSON)",
        }
    return None


_SEP = "\n---\n"
_EVIDENCE_ELIDED = "[... earlier tool output omitted ...]"


def build_evidence(messages: List[Dict[str, Any]], max_chars: int = 8000) -> str:
    """Compact tool-output messages into an evidence string for the grader.

    Keeps the MOST RECENT outputs. This used to join oldest-first and slice
    ``[:max_chars]``, which handed the judge the opening broad searches and cut
    off everything after — but an investigation narrows down, so the findings
    the answer actually rests on are the last few calls. The judge then scored a
    well-grounded answer as unfaithful because the evidence for it was the part
    that got dropped. That is expensive as well as wrong: the supervisor blends
    this into its composite, and a flipped verdict costs a whole extra run.

    A single output larger than the budget is itself tail-truncated, for the
    same reason.
    """
    parts: List[str] = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            content = str(msg.get("content", ""))
            if content:
                parts.append(content)
    if not parts:
        return ""

    kept: List[str] = []
    remaining = max_chars
    for content in reversed(parts):
        if remaining <= 0:
            break
        cost = len(content) + (len(_SEP) if kept else 0)
        if cost <= remaining:
            kept.append(content)
            remaining -= cost
            continue
        # Partial fit: keep this output's tail so the newest lines survive.
        room = remaining - (len(_SEP) if kept else 0)
        if room > len(_EVIDENCE_ELIDED):
            kept.append(_EVIDENCE_ELIDED + content[-(room - len(_EVIDENCE_ELIDED)):])
        remaining = 0

    dropped = len(parts) - len(kept)
    kept.reverse()
    if dropped > 0:
        kept.insert(0, f"[... {dropped} earlier tool output(s) omitted ...]")
    return _SEP.join(kept)


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
        from app.core.llm.call_llm import call_llm  # DB-resolved model

        human = (
            "EVIDENCE the agent was given:\n```\n"
            # Tail, not head — same reasoning as build_evidence, and a head
            # slice here would have re-trimmed exactly the newest evidence that
            # build_evidence just worked to preserve.
            + (evidence or "")[-8000:]
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
        # An unparseable verdict is NO SIGNAL, not a mediocre one. This used to
        # fall back to score=0.5, which is not neutral: the supervisor blends the
        # grader 80/20 into its composite (quality/supervisor.py), so a fabricated
        # 0.5 silently dragged a good answer down or propped a bad one up — and a
        # flipped verdict costs a whole extra agent run on RETRY. Callers already
        # treat None as "no grader signal" and fall back to heuristics.
        parsed = _extract_json(text)
        if parsed is None:
            logger.warning(
                "grader: could not parse a verdict from the judge response "
                "(%d chars) — returning no signal", len(text or ""),
            )
            return None
        try:
            score = float(parsed.get("score"))
        except (TypeError, ValueError):
            logger.warning(
                "grader: judge returned a non-numeric score (%r) — returning no signal",
                parsed.get("score"),
            )
            return None
        score = max(0.0, min(1.0, score))
        return {
            "score": score,
            "verdict": parsed.get("verdict", "unknown"),
            "reasoning": parsed.get("reasoning", "(no reasoning returned)"),
        }
    except Exception as exc:  # noqa: BLE001 — never break the loop
        logger.warning("grader: grade_answer failed (%s) — skipping", exc)
        return None
