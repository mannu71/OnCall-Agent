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
_PREVIEW_CHAR_CAP = 3200  # ~800 tokens of drill preview text

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
    limit: int = 20,
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
    limit: int = 20,
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
