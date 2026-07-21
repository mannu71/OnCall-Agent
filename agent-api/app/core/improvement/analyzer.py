"""Trace analyzer for the self-improvement loop.

Two layers:
  * :func:`compute_signals` — deterministic, LLM-free quality metrics over a batch
    of execution records (escalation rate, empty/refusal answers, avg tool calls,
    recurring error fingerprints). Cheap and testable.
  * :func:`analyze_recent` — loads recent executions, computes signals, and (when
    a model is configured) asks the DB-configured LLM to turn the signals into
    concrete, approvable improvement proposals. Proposals are returned as DRAFTS;
    applying them is a separate, human-driven step.

DB-configured model only (``app.core.llm.call_llm`` resolves it) — never hardcoded.
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


def fingerprint_error(text: str) -> str:
    """Regex-normalize an error string into a stable, low-cardinality key
    (digits collapsed to '#', truncated to 80 chars). Shared by
    compute_signals' batch analysis and the live failure_ledger hook
    (app.harness.failure_hook) so both produce the SAME fingerprint for
    the same underlying error class."""
    return re.sub(r"\d+", "#", str(text or ""))[:80]


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
            fp = fingerprint_error(err)
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

    if getattr(settings, "governance_conversion_enabled", False):
        await _record_batch_fingerprints(signals, records)

    proposals = _heuristic_proposals(signals)

    # 4.3 evolution wiring: finer-grained tool-reliability/stall proposals
    # from trajectory_events, additive to the error-fingerprint proposals
    # above. Requires settings.step_events_enabled — a no-op otherwise.
    if getattr(settings, "step_events_enabled", False):
        try:
            trace_ids = [
                str(r["id"]) for r in records
                if r.get("id") is not None
            ]
            event_signals = await compute_event_signals(trace_ids)
            if event_signals:
                signals["events"] = event_signals
                proposals = proposals + _event_heuristic_proposals(event_signals)
        except Exception as exc:  # noqa: BLE001 — analysis must never raise
            logger.warning("self-improvement: event-signal proposal step skipped (%s)", exc)

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


async def compute_event_signals(trace_ids: List[str]) -> Dict[str, Any]:
    """Tool-failure clusters + stall counts from trajectory_events (4.3).

    Finer-grained than compute_signals' error-string fingerprints — this
    identifies WHICH tool is unreliable, not just that some error recurred.
    Requires settings.step_events_enabled to have been on when the sampled
    runs executed; returns {} (a no-op) if no events are found. Never
    raises — event-signal computation is additive to compute_signals, not a
    replacement, so its absence must never break analyze_recent().
    """
    if not trace_ids:
        return {}
    try:
        from app.infrastructure.persistence import trajectory_event_repository
        tool_calls: Dict[str, int] = {}
        tool_failures: Dict[str, int] = {}
        stall_events = 0
        any_events = False
        for tid in trace_ids:
            events = await trajectory_event_repository.list_for_trace(tid)
            for ev in events:
                any_events = True
                payload = ev.get("payload") or {}
                if ev.get("type") == "tool_call":
                    name = (payload.get("action") or {}).get("name") or "?"
                    tool_calls[name] = tool_calls.get(name, 0) + 1
                    if (payload.get("outcome") or {}).get("status") == "error":
                        tool_failures[name] = tool_failures.get(name, 0) + 1
                elif ev.get("type") == "lifecycle":
                    event_name = (payload.get("action") or {}).get("event")
                    if event_name in ("max_turns_forced_synthesis", "truncation_ladder_exhausted"):
                        stall_events += 1
        if not any_events:
            return {}
        unreliable_tools = [
            {
                "tool": name, "calls": tool_calls[name], "failures": tool_failures.get(name, 0),
                "failure_rate": round(tool_failures.get(name, 0) / tool_calls[name], 3),
            }
            for name in tool_calls
            if tool_calls[name] >= 3 and (tool_failures.get(name, 0) / tool_calls[name]) >= 0.5
        ]
        unreliable_tools.sort(key=lambda t: -t["failure_rate"])
        return {
            "traces_with_events": len(trace_ids),
            "unreliable_tools": unreliable_tools[:5],
            "stall_events": stall_events,
        }
    except Exception as exc:  # noqa: BLE001 — event-signal analysis must never raise
        logger.warning("self-improvement: event-signal computation skipped (%s)", exc)
        return {}


def _event_heuristic_proposals(event_signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Proposals derived from compute_event_signals (4.3) — tool-specific
    reliability findings, distinct from compute_signals' error-fingerprint
    proposals."""
    props: List[Dict[str, Any]] = []
    for t in event_signals.get("unreliable_tools", []):
        props.append({
            "kind": "tools", "target": f"tool:{t['tool']}",
            "suggestion": (
                f"Tool '{t['tool']}' fails {t['failure_rate']:.0%} of calls "
                f"({t['failures']}/{t['calls']}) — investigate why, replace it, or steer "
                "the agent away from it (e.g. an AGENT_POLICY.md rule)."
            ),
            "rationale": (
                f"Observed via trajectory_events across {event_signals.get('traces_with_events')} traces."
            ),
            "status": "draft",
        })
    if event_signals.get("stall_events", 0) >= 3:
        props.append({
            "kind": "reliability", "target": "loop-budget",
            "suggestion": (
                "Multiple runs are hitting forced-synthesis or truncation-ladder "
                "exhaustion — consider raising max_turns, narrowing task scope, or "
                "checking for a tool that's consuming turns without progress."
            ),
            "rationale": f"{event_signals['stall_events']} stall event(s) observed.",
            "status": "draft",
        })
    return props


async def _llm_proposals(profile: Optional[str], signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ask the DB-configured LLM to turn signals into concrete proposals (drafts)."""
    from app.core.llm.call_llm import call_llm

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


async def _record_batch_fingerprints(
    signals: Dict[str, Any], records: List[Dict[str, Any]],
) -> None:
    """Upsert this analyze_recent() batch's error fingerprints into
    failure_ledger (migration 030). Best-effort — never breaks analysis.
    Gated by settings.governance_conversion_enabled (checked by the caller).
    """
    if not signals.get("top_errors"):
        return
    try:
        from app.infrastructure.persistence import failure_ledger_repository
        fp_set = {e["fingerprint"] for e in signals["top_errors"]}
        for rec in records:
            err = rec.get("error")
            if not err or fingerprint_error(err) not in fp_set:
                continue
            exec_id = rec.get("id") or rec.get("execution_id")
            await failure_ledger_repository.record(fingerprint_error(err), exec_id)
    except Exception as exc:  # noqa: BLE001 — governance must never break analysis
        logger.warning("self-improvement: failure_ledger recording skipped (%s)", exc)


async def convert_recurring_failures(*, threshold: Optional[int] = None) -> List[Dict[str, Any]]:
    """Failure->governance conversion (3.2): scan failure_ledger for open
    fingerprints recurring >= threshold times and turn each into a draft
    proposal.

    Every proposal is 'kind': 'eval' — the SAFE, deterministic conversion
    apply.py can auto-apply. "Auto-apply" for this kind is deliberately
    bookkeeping-only (marks the failure_ledger row 'converted' — see
    app.core.improvement.apply._apply_eval_proposal): fabricating an actual
    eval-suite regression case from a bare error fingerprint would need
    domain knowledge (which fixture reproduces it, what the expected
    tool_selection/answer assertions are) that only a human has. The
    proposal's suggestion/rationale is the human-actionable next step;
    'policy' and 'selftest' remain valid alternative controls an operator
    can choose instead, listed in the rationale.
    """
    from app.config import settings
    from app.infrastructure.persistence import failure_ledger_repository

    thr = threshold if threshold is not None else getattr(
        settings, "governance_convert_threshold", 3,
    )
    rows = await failure_ledger_repository.list_open(min_count=thr)
    proposals: List[Dict[str, Any]] = []
    for row in rows:
        fp = row["fingerprint"]
        proposals.append({
            "kind": "eval",
            "target": f"failure_ledger:{fp}",
            "suggestion": (
                f"Recurring failure '{fp}' seen {row['count']} times "
                f"(first {row['first_seen']}, last {row['last_seen']}). Add a "
                "regression case reproducing it under evals/accuracy, a "
                "policy rule (deny/ask pattern), or a harness_selftest.py "
                "check, then mark this fingerprint converted."
            ),
            "rationale": (
                f"Recurred {row['count']} times (threshold {thr}) across "
                f"executions {row.get('sample_execution_ids')}."
            ),
            "status": "draft",
            "control_ref": fp,
        })
    return proposals


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
