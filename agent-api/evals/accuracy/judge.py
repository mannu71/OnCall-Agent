"""LLM-as-judge over Bedrock — scores narrative faithfulness only.

Used for the qualitative columns (CloudWatch synthesis faithfulness, crawler
RCA/search relevance). Deliberately kept OUT of the headline deterministic
accuracy: an LLM judge agrees with a human only ~80-90% of the time, so its
output is reported separately and its own agreement is measured by selftest.

Builds its model through the app's own enriched-config path (resolve_llm_config
-> build_llm), which injects the DB-stored Bedrock model-key credentials,
remaps to the eu cross-region inference profile, and disables TLS verification
exactly like the production synthesis path.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from evals.accuracy import _bootstrap  # noqa: F401  (applies boto3 SSL patch on import)

# Model proven enabled in this account/region (see llm_configs). Override JUDGE_MODEL.
DEFAULT_JUDGE_MODEL = os.getenv("JUDGE_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0")

_JUDGE_SYSTEM = (
    "You are a strict evaluation judge. You score whether a MODEL OUTPUT is "
    "faithful to the EVIDENCE it was given. Faithful means: every concrete "
    "claim (errors, identifiers, counts, severity, coverage) is supported by "
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
    # Fallback for truncated/garbled JSON: recover score+verdict by regex so a
    # long reasoning string that got cut off doesn't zero out a valid verdict.
    score_m = re.search(r'"score"\s*:\s*([01](?:\.\d+)?)', text or "")
    if score_m:
        verdict_m = re.search(r'"verdict"\s*:\s*"([a-z]+)"', text or "")
        return {"score": float(score_m.group(1)),
                "verdict": verdict_m.group(1) if verdict_m else "unknown",
                "reasoning": "(recovered from truncated JSON)"}
    return None


async def _build_judge_llm(model: str):
    from app.workflow.llm_config import resolve_llm_config
    from app.workflow.strategies.react.llm_factory import build_llm
    wf = {"nodes": [{"id": "judge_lm", "type": "language_model",
                     "data": {"provider": "bedrock", "model": model}}], "edges": []}
    cfg = await resolve_llm_config(wf)
    return build_llm(cfg)


def _usage_tokens(resp: Any) -> tuple:
    usage = getattr(resp, "usage_metadata", None) or {}
    return usage.get("input_tokens", 0), usage.get("output_tokens", 0)


async def judge_faithfulness(
    *,
    model_output: str,
    evidence: str,
    requirements: str = "",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Score *model_output* against *evidence*. Returns {score, verdict, reasoning, tokens}."""
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = await _build_judge_llm(model or DEFAULT_JUDGE_MODEL)
    human = (
        "EVIDENCE the model was given:\n```\n" + (evidence or "")[:8000] + "\n```\n\n"
        "REQUIREMENTS for a faithful answer:\n" + (requirements or "(general faithfulness)") + "\n\n"
        "MODEL OUTPUT to score:\n```\n" + (model_output or "")[:6000] + "\n```\n\n"
        "Return the JSON verdict now."
    )
    resp = await llm.ainvoke([
        SystemMessage(content=_JUDGE_SYSTEM),
        HumanMessage(content=human),
    ])
    in_tok, out_tok = _usage_tokens(resp)
    content = str(resp.content) if resp.content else ""
    parsed = _extract_json(content) or {}
    score = parsed.get("score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "verdict": parsed.get("verdict", "unknown"),
        "reasoning": parsed.get("reasoning", "(no reasoning returned)"),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "raw": content,
    }
