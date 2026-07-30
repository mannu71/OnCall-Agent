"""Token-efficient compact summaries for CloudWatch tool outputs.

Keeps agent-facing payloads small while preserving fields the ReAct prompt
expects (normalized_pattern, occurrence_count, z_score, baseline_average).
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.config import settings
from app.workflow.tools.cloudwatch_scoring import (
    grade_weight,
    pattern_score,
    severity_label,
)

# Triage caps — tuned for ~800–1200 tokens per structured tool response.
TOP_UNIQUE_PATTERNS = 12
TOP_ANOMALIES = 15
# Internal log-analysis tool — keep enough of the raw example message that
# correlation / profile / trace IDs survive (tail-truncation preserves the most
# diagnostic end of error / stack-trace text).
EXAMPLE_MSG_CHARS = 600
PATTERN_CHARS = 150
CONTEXT_TOP_PATTERNS = 3  # upstream agent-node injection


def _looks_like_error(text: str) -> bool:
    low = (text or "").lower()
    return any(x in low for x in ("error", "exception", "fatal", "traceback", "failed"))


def _truncate(s: Any, n: int, *, tail: bool = False) -> str:
    """Truncate text; for errors/stack traces keep the *tail* (root cause)."""
    text = "" if s is None else str(s)
    if len(text) <= n:
        return text
    if tail and _looks_like_error(text):
        return "…" + text[-(n - 1):]
    return text[: n - 1] + "…"


def merge_data_quality(existing: Dict[str, Any], partial: bool, ratio: float) -> Dict[str, Any]:
    """Merge partial/sampling flags into a compact data_quality block."""
    dq = dict(existing or {})
    if partial:
        dq["partial"] = True
        prev = dq.get("sampling_ratio", 1.0)
        try:
            dq["sampling_ratio"] = min(float(prev), float(ratio))
        except (TypeError, ValueError):
            dq["sampling_ratio"] = ratio
        dq["hint"] = "partial/sampled; confirm via cloudwatch_search_logs"
    return dq


def compact_pattern_buckets(patterns: Any) -> tuple[Dict[str, Any], bool, float]:
    """Replace heavy Insights bucket arrays with status + bucket counts."""
    summary: Dict[str, Any] = {}
    partial = False
    min_ratio = 1.0
    if not isinstance(patterns, dict):
        return summary, partial, min_ratio

    for ptype, pdata in patterns.items():
        if not isinstance(pdata, dict):
            continue
        entry: Dict[str, Any] = {"status": pdata.get("status", "unknown")}
        if pdata.get("partial"):
            partial = True
            try:
                min_ratio = min(min_ratio, float(pdata.get("sampling_ratio", 0.25)))
            except (TypeError, ValueError):
                min_ratio = min(min_ratio, 0.25)
        if entry["status"] == "Complete":
            entry["buckets"] = len(pdata.get("data") or [])
        elif pdata.get("error"):
            entry["err"] = _truncate(pdata["error"], 60)
        summary[ptype] = entry
    return summary, partial, min_ratio


def _rank_unique_patterns(
    patterns: List[Any], grade_w: float,
) -> List[Dict[str, Any]]:
    """Order pattern candidates by severity × rarity-aware volume × grade.

    The upstream list arrives sorted count-descending, so a plain head slice
    keeps the loudest patterns and drops every rare one — exactly backwards for
    a one-off ``OutOfMemoryError``. Ranking first lets a singleton FATAL
    survive the cut without giving up the high-volume burst.

    Ties break on the normalized pattern text so ordering stays deterministic.
    """
    scored = []
    for p in patterns:
        if not isinstance(p, dict):
            continue
        key = p.get("normalized_pattern", "") or ""
        sev = severity_label(key, p.get("example_message", "") or "")
        score = pattern_score(sev, p.get("occurrence_count") or 0.0, grade_w)
        scored.append((score, key, p, sev))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [{**p, "severity": sev} for _s, _k, p, sev in scored]


def summarise_patterns(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Compact analyze_log_patterns output for the agent."""
    if not isinstance(raw, dict):
        return {"error": "unexpected_response", "raw_type": type(raw).__name__}

    pattern_summary, partial, min_ratio = compact_pattern_buckets(raw.get("patterns"))
    dq = merge_data_quality(raw.get("data_quality") or {}, partial, min_ratio)

    candidates: List[Any] = raw.get("unique_patterns") or []
    ranked = settings.cloudwatch_pattern_ranking
    grade: str | None = None
    if ranked:
        # Grade the *unranked* corpus: evidence_grade answers "how solid is the
        # evidence base", which is a property of the whole candidate set, not of
        # whichever pattern ranking floated to the top. Computing it after the
        # sort would let a promoted singleton read as grade=low and spuriously
        # trip should_auto_drill_down.
        grade = _evidence_grade(dq, [p for p in candidates if isinstance(p, dict)])
        candidates = _rank_unique_patterns(candidates, grade_weight(grade))

    compact: List[Dict[str, Any]] = []
    for p in candidates[:TOP_UNIQUE_PATTERNS]:
        if not isinstance(p, dict):
            continue
        entry = {
            "normalized_pattern": _truncate(p.get("normalized_pattern", ""), PATTERN_CHARS),
            "occurrence_count": p.get("occurrence_count"),
            "affected_streams": p.get("affected_streams"),
            "example_message": _truncate(
                p.get("example_message", ""), EXAMPLE_MSG_CHARS, tail=True,
            ),
            "first_seen": p.get("first_seen"),
            "last_seen": p.get("last_seen"),
        }
        if ranked:
            # Carry severity through so select_drill_targets scores patterns on
            # it too — it reads this summary, and without the field every
            # pattern silently fell back to the default weight.
            entry["severity"] = p.get("severity")
        compact.append(entry)

    total = len(raw.get("unique_patterns") or [])
    out: Dict[str, Any] = {
        "success": raw.get("success", True),
        "pattern_buckets": pattern_summary,
        "unique_patterns": compact,
        "unique_patterns_total": total,
        "log_groups_analyzed": raw.get("log_groups_analyzed"),
        "time_range": raw.get("time_range"),
    }
    if dq:
        out["data_quality"] = dq
    if total > len(compact):
        out["unique_patterns_truncated"] = True
    out["evidence_grade"] = grade if grade is not None else _evidence_grade(dq, compact)
    return out


def _evidence_grade(dq: Dict[str, Any], patterns: List[Dict[str, Any]]) -> str:
    """Compact confidence hint for the agent (one token-ish word)."""
    if dq.get("partial"):
        return "low"
    if not patterns:
        return "none"
    top = patterns[0].get("occurrence_count") or 0
    if top >= 10:
        return "high"
    if top >= 3:
        return "medium"
    return "low"


def compact_context_snippet(raw: Dict[str, Any], analysis_type: str) -> Dict[str, Any]:
    """Token-minimal upstream context for the agent node (~400–600 chars)."""
    if not isinstance(raw, dict):
        return {}
    if analysis_type in ("error-patterns", "activity-summary"):
        summary = summarise_patterns(raw) if raw.get("unique_patterns") is not None else {}
        if not summary:
            return {}
        return {
            "top_patterns": (summary.get("unique_patterns") or [])[:CONTEXT_TOP_PATTERNS],
            "data_quality": summary.get("data_quality"),
            "evidence_grade": summary.get("evidence_grade"),
        }
    if analysis_type == "anomaly-detection":
        summary = summarise_anomalies(raw)
        return {
            "top_anomalies": (summary.get("anomalies") or [])[:3],
            "summary": summary.get("summary"),
            "data_quality": summary.get("data_quality"),
        }
    return {"analysis_type": analysis_type, "success": raw.get("success")}


def summarise_anomalies(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Compact detect_anomalies output — critical/high/medium only."""
    if not isinstance(raw, dict):
        return {"error": "unexpected_response", "raw_type": type(raw).__name__}

    keep = {"critical", "high", "medium"}
    groups = raw.get("log_groups_analyzed") or []
    single_group = groups[0] if len(groups) == 1 else None

    kept: List[Dict[str, Any]] = []
    for a in raw.get("anomalies") or []:
        if not isinstance(a, dict):
            continue
        if a.get("severity", "low") not in keep:
            continue
        kept.append({
            "log_group": a.get("log_group") or single_group,
            "timestamp": a.get("timestamp"),
            "severity": a.get("severity"),
            "z_score": a.get("z_score"),
            "current_count": a.get("current_count"),
            "baseline_average": a.get("baseline_average"),
            "reasons": (a.get("reasons") or [])[:3],
        })
    kept.sort(key=lambda x: (x.get("z_score") or 0), reverse=True)

    summary_in = raw.get("summary") or {}
    summary = {
        "total_anomalies": summary_in.get("total_anomalies"),
        "critical_severity": summary_in.get("critical_severity"),
        "high_severity": summary_in.get("high_severity"),
        "medium_severity": summary_in.get("medium_severity"),
    }

    out: Dict[str, Any] = {
        "success": raw.get("success", True),
        "anomalies": kept[:TOP_ANOMALIES],
        "summary": summary,
        "log_groups_analyzed": groups,
    }
    dq = raw.get("data_quality") or {}
    if dq:
        out["data_quality"] = dq
    if len(kept) > TOP_ANOMALIES:
        out["anomalies_truncated"] = True
    out["evidence_grade"] = "low" if dq.get("partial") else ("high" if kept else "none")
    return out


def summarise_correlation(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Cap correlation timeline for token budget."""
    if not isinstance(raw, dict):
        return {"error": "unexpected_response", "raw_type": type(raw).__name__}

    timeline = raw.get("timeline") or []
    truncated = len(timeline) > 100
    out = {k: v for k, v in raw.items() if k != "timeline"}
    out["timeline"] = timeline[:100]
    out["timeline_truncated"] = truncated
    out["timeline_total"] = len(timeline)
    dq = raw.get("data_quality")
    if dq:
        out["data_quality"] = dq
    return out
