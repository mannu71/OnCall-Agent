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

logger = logging.getLogger(__name__)

AUTO_DRILLDOWN_ENABLED = settings.cloudwatch_auto_drilldown

_AUTO_DRILL_GRADES = frozenset({"low", "none"})
_PLACEHOLDER_RE = re.compile(r"<[A-Z_]+(?::[^>]+)?>")
_PREVIEW_CHAR_CAP = 6000  # internal RCA tool — keep raw drill preview (IDs/traces)

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


def cap_drill_preview(text: str) -> str:
    if len(text) <= _PREVIEW_CHAR_CAP:
        return text
    return text[: _PREVIEW_CHAR_CAP - 1] + "…"


def select_drill_targets(
    patterns_summary: Optional[Dict[str, Any]],
    anomalies_summary: Optional[Dict[str, Any]],
    *,
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """Rank the strongest findings for the staged pipeline to drill into.

    Anomalies (ranked by z_score) take priority over recurring patterns (ranked
    by occurrence_count), since a volume spike is a stronger lead than a steady
    error. Returns up to *top_n* normalized target descriptors, each carrying the
    fields the Insights query builders need.
    """
    targets: List[Dict[str, Any]] = []

    anomalies = (anomalies_summary or {}).get("anomalies") or []
    for a in sorted(anomalies, key=lambda x: (x.get("z_score") or 0), reverse=True):
        if not isinstance(a, dict):
            continue
        targets.append({
            "kind": "anomaly",
            "log_group": a.get("log_group"),
            "z_score": a.get("z_score"),
            "severity": a.get("severity"),
            "label": f"anomaly z={a.get('z_score')} in {a.get('log_group') or 'log group'}",
        })

    patterns = (patterns_summary or {}).get("unique_patterns") or []
    for p in sorted(patterns, key=lambda x: (x.get("occurrence_count") or 0), reverse=True):
        if not isinstance(p, dict):
            continue
        targets.append({
            "kind": "pattern",
            "normalized_pattern": p.get("normalized_pattern", ""),
            "example_message": p.get("example_message", ""),
            "occurrence_count": p.get("occurrence_count"),
            "label": f"pattern x{p.get('occurrence_count')}: "
                     f"{(p.get('normalized_pattern') or '')[:60]}",
        })

    return targets[: max(0, top_n)]


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


def compact_kb_patterns(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact KB log_pattern hits for tool output."""
    out: List[Dict[str, Any]] = []
    for m in matches[:2]:
        out.append({
            "name": m.get("name"),
            "severity": m.get("severity"),
            "similarity": round(float(m.get("similarity") or 0), 2),
            "prior_notes": (m.get("description") or "")[:180],
        })
    return out


async def recall_kb_for_pattern(
    normalized_pattern: str,
    example_message: str,
    *,
    limit: int = 2,
    threshold: float = 0.58,
) -> List[Dict[str, Any]]:
    """Recall similar log_patterns from the knowledge base."""
    query = f"{example_message[:220]} {normalized_pattern[:120]}".strip()
    if len(query) < 8:
        return []
    try:
        from app.services.knowledge_base import knowledge_base

        hits = await knowledge_base.search_similar_patterns(
            query, limit=limit, threshold=threshold,
        )
        return compact_kb_patterns(hits)
    except Exception as exc:
        logger.debug("recall_kb_for_pattern failed (non-fatal): %s", exc)
        return []


async def recall_kb_for_log_groups(log_groups: List[str]) -> List[Dict[str, Any]]:
    """Recall patterns associated with configured log group names."""
    query = " ".join(g for g in log_groups if g)[:300]
    if not query:
        return []
    try:
        from app.services.knowledge_base import knowledge_base

        hits = await knowledge_base.search_similar_patterns(
            query, limit=2, threshold=0.55,
        )
        return compact_kb_patterns(hits)
    except Exception as exc:
        logger.debug("recall_kb_for_log_groups failed (non-fatal): %s", exc)
        return []
