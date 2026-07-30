"""Auto drill-down, pattern→Insights query generation, and KB recall for CloudWatch.

Token policy: auto drill-down runs at most once per tool call, only when
evidence_grade is low/none or data is partial. Preview is capped separately
so triage + drill fit in ~3000 tokens total.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.config import settings
from app.workflow.tools.cloudwatch_scoring import (
    GRADE_WEIGHT,
    grade_weight as _grade_weight_for,
    pattern_score,
    severity_weight,
)

logger = logging.getLogger(__name__)

AUTO_DRILLDOWN_ENABLED = settings.cloudwatch_auto_drilldown

_AUTO_DRILL_GRADES = frozenset({"low", "none"})
_PLACEHOLDER_RE = re.compile(r"<[A-Z_]+(?::[^>]+)?>")
_PREVIEW_CHAR_CAP = 6000  # internal log-analysis tool — keep raw drill preview (IDs/traces)

# Default benign phrases (override via workflow severity_excludes config).
DEFAULT_SEVERITY_EXCLUDES = (
    "failed validation",
    "health check",
    "elb-healthchecker",
    "kube-probe",
)


def should_auto_drill_down(summary: Dict[str, Any]) -> bool:
    """Return True when triage data is too weak to conclude without samples."""
    if not AUTO_DRILLDOWN_ENABLED:
        return False
    if summary.get("evidence_grade") in _AUTO_DRILL_GRADES:
        return True
    dq = summary.get("data_quality") or {}
    return bool(dq.get("partial"))


def pick_drill_target(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Choose the best pattern or anomaly row to drill into."""
    patterns = summary.get("unique_patterns") or []
    if patterns:
        return {"kind": "pattern", **patterns[0]}
    anomalies = summary.get("anomalies") or []
    if anomalies:
        return {"kind": "anomaly", **anomalies[0]}
    return None


def merge_severity_excludes(configured: Optional[List[str]]) -> List[str]:
    """Merge workflow-configured excludes with safe defaults."""
    out: List[str] = list(DEFAULT_SEVERITY_EXCLUDES)
    for item in configured or []:
        s = (item or "").strip()
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out


def apply_severity_excludes(filter_clause: str, excludes: Optional[List[str]]) -> str:
    """Append AND NOT LIKE clauses to an Insights filter."""
    merged = merge_severity_excludes(excludes)
    if not merged:
        return filter_clause
    not_parts = [
        f'@message not like /(?i){re.escape(ex)}/' for ex in merged
    ]
    return f"({filter_clause}) AND {' AND '.join(not_parts)}"


def extract_filter_literals(normalized_pattern: str, example_message: str) -> List[str]:
    """Extract 1–2 stable substrings for a targeted Insights regex filter."""
    text = example_message or normalized_pattern or ""
    exc = re.search(r"\b([A-Z][a-zA-Z0-9_]*(?:Exception|Error))\b", text)
    if exc:
        return [exc.group(1)]

    skip = frozenset({
        "the", "and", "for", "with", "from", "that", "this", "error",
        "failed", "failure", "exception", "message", "level",
    })
    words = re.findall(r"[A-Za-z_]{4,}", text)
    significant = [w for w in words if w.lower() not in skip]
    if significant:
        return significant[:2]

    cleaned = _PLACEHOLDER_RE.sub(" ", normalized_pattern or "")
    chunk = " ".join(cleaned.split())[:48].strip()
    if len(chunk) >= 4:
        return [chunk]
    return []


def build_insights_query_for_pattern(
    normalized_pattern: str,
    example_message: str,
    *,
    limit: int = 50,
) -> str:
    """Build a bounded Insights query from a normalized error pattern."""
    literals = extract_filter_literals(normalized_pattern, example_message)
    if not literals:
        return (
            "fields @timestamp, @message, @logStream, @log\n"
            "| filter level = \"ERROR\" OR @message like /(?i)(error|exception)/\n"
            "| sort @timestamp desc\n"
            f"| limit {limit}\n"
        )
    clause = " or ".join(
        f"@message like /(?i){re.escape(lit)}/" for lit in literals
    )
    return (
        "fields @timestamp, @message, @logStream, @log\n"
        f"| filter {clause}\n"
        "| sort @timestamp desc\n"
        f"| limit {limit}\n"
    )


def build_insights_query_for_anomaly(
    log_group: Optional[str] = None,
    *,
    limit: int = 50,
) -> str:
    """Generic error sample query when drilling from an anomaly spike."""
    from app.mcp.tools.watch_tools import SEVERITY_PATTERNS

    err_filter = SEVERITY_PATTERNS["error"]
    lines = [
        "fields @timestamp, @message, @logStream, @log",
        f"| filter {err_filter}",
    ]
    if log_group:
        lines.append(f'| filter @log = "{log_group}"')
    lines.extend([
        "| sort @timestamp desc",
        f"| limit {limit}",
    ])
    return "\n".join(lines) + "\n"


def _parse_iso(ts: Optional[str]):
    """Best-effort ISO-8601 → aware datetime; None on failure."""
    if not ts:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def compute_drill_window(target: Dict[str, Any], default_start, default_end, *, margin_minutes: int = 5):
    """Cost-aware drill-down window (Phase 3).

    A pattern target carries first_seen/last_seen — drilling only that span
    (padded by *margin_minutes*) instead of the whole triage window scans far
    less data. The result is always clamped within [default_start, default_end]
    so it can never widen the query. Falls back to the full window when the
    target has no usable timestamps. Pure/deterministic.
    """
    from datetime import timedelta

    first = _parse_iso(target.get("first_seen"))
    last = _parse_iso(target.get("last_seen"))
    if not first and not last:
        return default_start, default_end
    lo = (first or last) - timedelta(minutes=margin_minutes)
    hi = (last or first) + timedelta(minutes=margin_minutes)
    # Clamp inside the triage window — never widen it.
    lo = max(lo, default_start)
    hi = min(hi, default_end)
    if lo >= hi:
        return default_start, default_end
    return lo, hi


def cap_drill_preview(text: str) -> str:
    if len(text) <= _PREVIEW_CHAR_CAP:
        return text
    return text[: _PREVIEW_CHAR_CAP - 1] + "…"


_GRADE_WEIGHT = GRADE_WEIGHT  # re-exported for callers importing it from here


def score_drill_target(target: Dict[str, Any], grade_weight: float = 1.0) -> float:
    """Deterministic priority score for a drill-down candidate (Phase 2).

    Combines three available signals so the strongest lead is drilled first:
    ``severity`` × ``volume/spike`` × ``evidence_grade``. Anomalies use their
    z-score as the spike term (a volume spike is a strong lead); patterns use a
    log-scaled occurrence count (diminishing returns on sheer volume). Pure and
    deterministic — same input always yields the same score.

    With ``cloudwatch_pattern_ranking`` on, the pattern spike term also carries
    a rarity factor so a one-off FATAL isn't ranked below routine chatter.
    """
    sev = severity_weight(target.get("severity"))
    if target.get("kind") == "anomaly":
        z = float(target.get("z_score") or 0.0)
        spike = 1.0 + min(max(z, 0.0), 20.0) / 2.0
        return round(sev * spike * grade_weight, 6)
    return pattern_score(
        target.get("severity"),
        target.get("occurrence_count") or 0.0,
        grade_weight,
        rarity_aware=settings.cloudwatch_pattern_ranking,
    )


def select_drill_targets(
    patterns_summary: Optional[Dict[str, Any]],
    anomalies_summary: Optional[Dict[str, Any]],
    *,
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """Rank the strongest findings for the staged pipeline to drill into.

    With ``cloudwatch_drilldown_scoring`` enabled (default) all candidates are
    ranked together by :func:`score_drill_target` (severity × spike × evidence
    grade), so a high-volume critical pattern can outrank a marginal anomaly.
    With the knob off, the legacy ordering is used (anomalies by z-score first,
    then patterns by occurrence count). Returns up to *top_n* normalized target
    descriptors carrying the fields the Insights query builders need.
    """
    anomalies = (anomalies_summary or {}).get("anomalies") or []
    patterns = (patterns_summary or {}).get("unique_patterns") or []

    anomaly_targets: List[Dict[str, Any]] = []
    for a in anomalies:
        if not isinstance(a, dict):
            continue
        anomaly_targets.append({
            "kind": "anomaly",
            "log_group": a.get("log_group"),
            "z_score": a.get("z_score"),
            "severity": a.get("severity"),
            "label": f"anomaly z={a.get('z_score')} in {a.get('log_group') or 'log group'}",
        })

    pattern_targets: List[Dict[str, Any]] = []
    for p in patterns:
        if not isinstance(p, dict):
            continue
        pattern_targets.append({
            "kind": "pattern",
            "normalized_pattern": p.get("normalized_pattern", ""),
            "example_message": p.get("example_message", ""),
            "occurrence_count": p.get("occurrence_count"),
            "severity": p.get("severity"),
            "first_seen": p.get("first_seen"),
            "last_seen": p.get("last_seen"),
            "label": f"pattern x{p.get('occurrence_count')}: "
                     f"{(p.get('normalized_pattern') or '')[:60]}",
        })

    if not settings.cloudwatch_drilldown_scoring:
        # Legacy ordering: anomalies (z-score desc) then patterns (count desc).
        anomaly_targets.sort(key=lambda x: (x.get("z_score") or 0), reverse=True)
        pattern_targets.sort(key=lambda x: (x.get("occurrence_count") or 0), reverse=True)
        return (anomaly_targets + pattern_targets)[: max(0, top_n)]

    grade_w = _grade_weight_for((patterns_summary or {}).get("evidence_grade"))
    scored = [(score_drill_target(t, grade_w), t) for t in (anomaly_targets + pattern_targets)]
    # Sort by score desc; tie-break on label for stable, deterministic ordering.
    scored.sort(key=lambda st: (-st[0], st[1].get("label", "")))
    for s, t in scored:
        t["drill_score"] = s
    return [t for _s, t in scored[: max(0, top_n)]]


# CloudWatch Logs Insights queries must start with one of these commands.
_INSIGHTS_LEAD_COMMANDS = (
    "fields", "filter", "stats", "parse", "sort", "display", "limit",
)


def lint_insights_query(query: str) -> Optional[str]:
    """Cheaply validate a CloudWatch Logs Insights query before hitting AWS.

    Returns ``None`` when the query looks structurally sound, or a short
    corrective hint string when it is obviously malformed — so callers can give
    the model actionable feedback instead of a raw AWS ``ValidationException``
    (which wastes an investigation iteration).

    This is a lightweight structural lint, not a full parser — it catches the
    common mistakes, not every invalid query.
    """
    q = (query or "").strip()
    if not q:
        return ("Empty Insights query. Start with a `fields` command, e.g. "
                "`fields @timestamp, @message | filter @message like /(?i)error/ "
                "| sort @timestamp desc | limit 20`.")

    first_word = q.lstrip("| ").split(None, 1)[0].lower() if q.lstrip("| ") else ""
    if first_word not in _INSIGHTS_LEAD_COMMANDS:
        return (f"Insights query must begin with one of "
                f"{', '.join(_INSIGHTS_LEAD_COMMANDS)} — got '{first_word or '?'}'. "
                "Example: `fields @timestamp, @message | filter ... | limit 20`.")

    # Each '|' separates a command; every segment must be non-empty.
    segments = [seg.strip() for seg in q.split("|")]
    if any(seg == "" for seg in segments):
        return ("Malformed Insights query: an empty segment around a `|`. "
                "Each `|` must be followed by a command (filter/stats/sort/limit).")

    if q.count("/") % 2 != 0:
        return ("Unbalanced regex delimiter `/` in the Insights query. "
                "Regex literals must be wrapped like /(?i)pattern/.")

    if "limit" not in q.lower():
        return ("Insights query has no `| limit` clause — add one (e.g. "
                "`| limit 20`) to bound the result set and cost.")

    return None


# Hard ceiling on Insights pipe stages — a query with this many segments is
# almost certainly malformed/pathological, and each stage adds scan cost.
_MAX_PIPE_STAGES = 12
_DEFAULT_FIXUP_LIMIT = 100


def fixup_insights_query(query: str, *, default_limit: int = _DEFAULT_FIXUP_LIMIT):
    """Auto-correct minor Insights-query issues instead of rejecting outright.

    Returns ``(fixed_query, error)``:

    * ``error`` is a corrective hint string for *unfixable* structural problems
      (empty, bad lead command, unbalanced regex, empty segment, too many pipe
      stages) — the caller should not run the query.
    * Otherwise ``error`` is ``None`` and ``fixed_query`` is the query with a
      ``| limit`` clause injected when one was missing (the one mistake that is
      safe to fix silently). When nothing needs fixing the original query is
      returned unchanged.

    This keeps an agent's almost-correct query runnable (saving an investigation
    iteration) while still hard-failing genuinely broken ones.
    """
    q = (query or "").strip()
    # Reuse the strict linter for everything except the missing-limit case,
    # which we fix rather than reject.
    hint = lint_insights_query(q)
    if hint and "no `| limit`" not in hint:
        return q, hint

    if len([seg for seg in q.split("|")]) - 1 > _MAX_PIPE_STAGES:
        return q, (f"Insights query has too many pipe stages (>{_MAX_PIPE_STAGES}); "
                   "simplify it into fewer commands.")

    if "limit" not in q.lower():
        q = q.rstrip() + f"\n| limit {default_limit}"
    return q, None


def compact_kb_patterns(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact OKF ``kb``-bank recall rows for tool output."""
    out: List[Dict[str, Any]] = []
    for m in matches[:2]:
        content = (m.get("content") or "").strip()
        name = content.split("\n", 1)[0][:80] if content else None
        out.append({
            "name": name,
            "source": m.get("source"),
            "similarity": round(float(m.get("score") or 0), 2),
            "prior_notes": content[:180],
        })
    return out


async def recall_kb_for_pattern(
    normalized_pattern: str,
    example_message: str,
    *,
    limit: int = 2,
) -> List[Dict[str, Any]]:
    """Recall similar known issues / log patterns from the OKF ``kb`` bank (FTS)."""
    query = f"{example_message[:220]} {normalized_pattern[:120]}".strip()
    if len(query) < 8:
        return []
    try:
        from app.services.semantic_memory import semantic_memory

        hits = await semantic_memory.recall(query, bank="kb", k=limit)
        return compact_kb_patterns(hits)
    except Exception as exc:
        logger.debug("recall_kb_for_pattern failed (non-fatal): %s", exc)
        return []


async def recall_kb_for_log_groups(log_groups: List[str]) -> List[Dict[str, Any]]:
    """Recall knowledge associated with configured log group names (FTS)."""
    query = " ".join(g for g in log_groups if g)[:300]
    if not query:
        return []
    try:
        from app.services.semantic_memory import semantic_memory

        hits = await semantic_memory.recall(query, bank="kb", k=2)
        return compact_kb_patterns(hits)
    except Exception as exc:
        logger.debug("recall_kb_for_log_groups failed (non-fatal): %s", exc)
        return []
