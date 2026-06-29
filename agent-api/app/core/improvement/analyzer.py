"""Trace analyzer for the self-improvement loop.

Two layers:
  * :func:`compute_signals` — deterministic, LLM-free quality metrics over a batch
    of execution records (escalation rate, empty/refusal answers, avg tool calls,
    recurring error fingerprints). Cheap and testable.
  * :func:`analyze_recent` — loads recent executions, computes signals, and (when
    a model is configured) asks the DB-configured LLM to turn the signals into
    concrete, approvable improvement proposals. Proposals are returned as DRAFTS;
    applying them is a separate, human-driven step.

DB-configured model only (``app.crawler.call_llm`` resolves it) — never hardcoded.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ImprovementReport:
    profile: Optional[str]
    sample_size: int
    signals: Dict[str, Any] = field(default_factory=dict)
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "sample_size": self.sample_size,
            "signals": self.signals,
            "proposals": self.proposals,
            "note": self.note,
        }


def _answer_of(rec: Dict[str, Any]) -> str:
    out = rec.get("output") or rec.get("result") or {}
    if isinstance(out, dict):
        return str(out.get("final_answer") or out.get("answer") or "")
    return str(out or "")


def _is_refusalish(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t) < 12:
        return True
    return any(p in t for p in ("i cannot", "i can't", "unable to", "no answer", "i'm sorry"))


def compute_signals(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic quality metrics over a batch of execution dicts."""
    n = len(records)
    if not n:
        return {"count": 0}
    escalated = 0
    empty_or_refusal = 0
    tool_calls_total = 0
    errors = 0
    error_fingerprints: Dict[str, int] = {}
    for rec in records:
        out = rec.get("output") or rec.get("result") or {}
        out = out if isinstance(out, dict) else {}
        if out.get("supervisor_escalated"):
            escalated += 1
        if _is_refusalish(_answer_of(rec)):
            empty_or_refusal += 1
        tc = out.get("tool_calls") or []
        tool_calls_total += len(tc) if isinstance(tc, list) else 0
        err = rec.get("error")
        if err:
            errors += 1
            fp = re.sub(r"\d+", "#", str(err))[:80]
            error_fingerprints[fp] = error_fingerprints.get(fp, 0) + 1
    top_errors = sorted(error_fingerprints.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "count": n,
        "escalation_rate": round(escalated / n, 3),
        "empty_or_refusal_rate": round(empty_or_refusal / n, 3),
        "avg_tool_calls": round(tool_calls_total / n, 2),
        "error_rate": round(errors / n, 3),
        "top_errors": [{"fingerprint": fp, "count": c} for fp, c in top_errors],
    }


def _heuristic_proposals(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """LLM-free fallback proposals derived straight from the signals."""
    props: List[Dict[str, Any]] = []
    if signals.get("empty_or_refusal_rate", 0) >= 0.2:
        props.append({
            "kind": "prompt", "target": "role/instructions",
            "suggestion": "Strengthen the instruction to always produce a concrete, "
                          "evidence-backed answer and to state plainly when nothing is found "
                          "rather than refusing.",
            "rationale": f"{signals['empty_or_refusal_rate']:.0%} of recent answers were empty/refusal-like.",
            "status": "draft",
        })
    if signals.get("escalation_rate", 0) >= 0.25:
        props.append({
            "kind": "policy", "target": "supervisor/guardrails",
            "suggestion": "Review supervisor thresholds or add a verification step; "
                          "escalations are frequent.",
            "rationale": f"{signals['escalation_rate']:.0%} of recent runs escalated.",
            "status": "draft",
        })
    if signals.get("avg_tool_calls", 0) >= 8:
        props.append({
            "kind": "tools", "target": "tool-selection",
            "suggestion": "Encourage stopping earlier once evidence supports a conclusion, "
                          "or capture a skill/playbook for the common path.",
            "rationale": f"Average {signals['avg_tool_calls']} tool calls/run is high.",
            "status": "draft",
        })
    for e in signals.get("top_errors", []):
        if e.get("count", 0) >= 2:
            props.append({
                "kind": "reliability", "target": "error",
                "suggestion": f"Recurring error needs attention: {e['fingerprint']}",
                "rationale": f"Seen {e['count']} times in the sample.",
                "status": "draft",
            })
    return props


async def analyze_recent(
    profile: Optional[str] = None,
    *,
    limit: Optional[int] = None,
    use_llm: bool = True,
) -> ImprovementReport:
    """Sample recent executions and return improvement proposals (drafts).

    Guarded by ``settings.self_improvement_enabled``; returns an empty report with
    a note when disabled, so the endpoint can surface why nothing came back.
    """
    from app.config import settings

    if not getattr(settings, "self_improvement_enabled", False):
        return ImprovementReport(profile=profile, sample_size=0,
                                 note="self-improvement is disabled (SELF_IMPROVEMENT_ENABLED=false)")

    n = limit or getattr(settings, "self_improvement_sample", 30)
    from app.infrastructure.persistence import execution_repository
    if profile:
        records = await execution_repository.list_by_workflow(profile, limit=n)
    else:
        records = await execution_repository.list_all(limit=n)

    signals = compute_signals(records)
    if not signals.get("count"):
        return ImprovementReport(profile=profile, sample_size=0, signals=signals,
                                 note="no recent executions to analyze")

    proposals = _heuristic_proposals(signals)

    if use_llm:
        try:
            llm_props = await _llm_proposals(profile, signals)
            if llm_props:
                proposals = llm_props + proposals  # LLM first, heuristics as backstop
        except Exception as exc:  # noqa: BLE001 — analysis must never raise
            logger.warning("self-improvement: LLM proposal step skipped (%s)", exc)

    return ImprovementReport(
        profile=profile, sample_size=signals["count"], signals=signals, proposals=proposals,
    )


async def _llm_proposals(profile: Optional[str], signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ask the DB-configured LLM to turn signals into concrete proposals (drafts)."""
    from app.crawler.call_llm import call_llm

    prompt = (
        "You are an agent-quality analyst. Given these aggregate signals from recent runs of an "
        f"agent{f' (profile: {profile})' if profile else ''}, propose AT MOST 5 concrete, "
        "actionable improvements. Each must be a JSON object with keys: kind "
        "(prompt|tools|policy|skill|reliability), target, suggestion, rationale. Respond with a "
        "JSON array ONLY, no prose.\n\n"
        f"Signals:\n{json.dumps(signals, indent=2)}"
    )
    text, _tin, _tout, _cached = await call_llm(prompt, tier="search", max_tokens=1200)
    return _parse_proposals(text)


def _parse_proposals(text: str) -> List[Dict[str, Any]]:
    """Extract a JSON array of proposal objects from an LLM response (tolerant)."""
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for item in arr if isinstance(arr, list) else []:
        if isinstance(item, dict) and item.get("suggestion"):
            out.append({
                "kind": str(item.get("kind", "prompt")),
                "target": str(item.get("target", "")),
                "suggestion": str(item.get("suggestion", "")),
                "rationale": str(item.get("rationale", "")),
                "status": "draft",
            })
    return out
