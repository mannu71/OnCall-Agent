"""Shared deterministic scoring for CloudWatch patterns and drill-down targets.

Lives in its own module so the drill-down ranker
(:mod:`app.workflow.tools.cloudwatch_drilldown`) and the pattern summariser
(:mod:`app.workflow.tools.cloudwatch_summarizers`) score the same way without
importing each other.

Everything here is pure and deterministic — same input always yields the same
score. Callers depend on that for stable, reproducible ranking.
"""
from __future__ import annotations

import math

from app.workflow.tools.cloudwatch_sanitizer import severity_rank

# Severity → weight. ``none`` is the explicit "nothing notable" label; a
# *missing* severity falls back to SEV_DEFAULT (see severity_weight).
SEV_WEIGHT = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0, "none": 0.5}
SEV_DEFAULT = 1.5

# Evidence-grade → weight. A weak evidence base damps every candidate equally,
# so a marginal lead from thin data can't outrank a solid one.
GRADE_WEIGHT = {"high": 1.0, "medium": 0.85, "low": 0.7, "none": 0.6}
GRADE_DEFAULT = 0.8


def grade_weight(grade: object) -> float:
    """Weight for an evidence-grade label, or GRADE_DEFAULT when unrecognised."""
    return GRADE_WEIGHT.get(str(grade or "").lower(), GRADE_DEFAULT)

# severity_rank() returns 0 (worst) .. 3 (routine); map onto the label vocabulary.
_RANK_TO_LABEL = {0: "critical", 1: "high", 2: "medium", 3: "low"}

# Rarity numerator. rarity_factor = 1 + K/count, so a singleton is boosted x3
# and the bonus has decayed to <5% by ~count 50.
_RARITY_K = 2.0


def severity_label(*texts: str) -> str:
    """Derive a severity label from pattern text (strongest signal wins).

    Patterns coming out of ``analyze_log_patterns`` carry no severity field at
    all, which left the severity term in :func:`score_drill_target` inert for
    them. Deriving one from the normalized pattern plus its example message
    gives the ranker something real to weigh.
    """
    best = 3
    for t in texts:
        if t:
            best = min(best, severity_rank(t))
    return _RANK_TO_LABEL[best]


def severity_weight(severity: object) -> float:
    """Weight for a severity label, or SEV_DEFAULT when absent/unrecognised."""
    return SEV_WEIGHT.get(str(severity or "").lower(), SEV_DEFAULT)


def rarity_factor(occurrence_count: float) -> float:
    """Boost for rare patterns, decaying as occurrences accumulate.

    A ``count == 1`` pattern is the strongest novelty signal there is — a
    one-off ``OutOfMemoryError`` matters more than the 5,000th health-check
    line — but a pure log-scaled volume term ranks it *last*. This restores it
    without discarding the volume signal: the combined curve is U-shaped, so
    singletons and high-volume bursts both outrank the mid-range.
    """
    count = max(float(occurrence_count or 0.0), 1.0)
    return 1.0 + _RARITY_K / count


def volume_factor(occurrence_count: float) -> float:
    """Log-scaled occurrence term — diminishing returns on sheer volume."""
    return 1.0 + math.log10(max(float(occurrence_count or 0.0), 1.0))


def pattern_spike(occurrence_count: float, *, rarity_aware: bool) -> float:
    """Spike term for a pattern candidate.

    ``rarity_aware=False`` reproduces the legacy volume-only behaviour exactly.
    """
    vol = volume_factor(occurrence_count)
    return vol * rarity_factor(occurrence_count) if rarity_aware else vol


def pattern_score(
    severity: object,
    occurrence_count: float,
    grade_weight: float = 1.0,
    *,
    rarity_aware: bool = True,
) -> float:
    """severity × spike × evidence-grade for a unique-pattern candidate."""
    return round(
        severity_weight(severity)
        * pattern_spike(occurrence_count, rarity_aware=rarity_aware)
        * grade_weight,
        6,
    )
