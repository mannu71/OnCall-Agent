"""Metrics for the mini-model bake-off (pure functions, no model imports)."""
from __future__ import annotations

import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple


def classification_report(gold: Sequence[str], pred: Sequence[str]) -> Dict[str, Any]:
    """Accuracy, per-label precision/recall, and the confusion matrix."""
    labels = sorted(set(gold) | set(pred))
    confusion = {g: Counter() for g in labels}
    for g, p in zip(gold, pred):
        confusion[g][p] += 1
    per_label = {}
    for label in labels:
        tp = confusion[label][label]
        predicted = sum(confusion[g][label] for g in labels)
        actual = sum(confusion[label].values())
        per_label[label] = {
            "precision": round(tp / predicted, 4) if predicted else None,
            "recall": round(tp / actual, 4) if actual else None,
            "support": actual,
        }
    correct = sum(1 for g, p in zip(gold, pred) if g == p)
    return {
        "accuracy": round(correct / len(gold), 4) if gold else None,
        "per_label": per_label,
        "confusion": {g: dict(c) for g, c in confusion.items()},
    }


def misroute_rate(gold: Sequence[str], pred: Sequence[str], critical: str) -> float:
    """Share of *critical*-labelled items predicted as anything else."""
    pairs = [(g, p) for g, p in zip(gold, pred) if g == critical]
    return round(sum(1 for g, p in pairs if p != g) / len(pairs), 4) if pairs else 0.0


def _covered(span: Tuple[int, int], predicted: Iterable[Tuple[int, int]]) -> bool:
    start, end = span
    chars = set(range(start, end))
    for ps, pe in predicted:
        chars -= set(range(ps, pe))
        if not chars:
            return True
    return not chars


def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def span_report(items: Sequence[Any], predicted: Sequence[List[Tuple[str, int, int]]]) -> Dict[str, Any]:
    """PII metrics.

    * ``masked_recall`` per gold type: share of gold entities whose characters
      are fully covered by predicted spans of ANY type (what matters for leaks).
    * ``typed_recall``: share overlapped by a predicted span of the SAME type.
    * ``false_positive_spans``: predicted spans overlapping no gold entity.
    * ``keep_violations`` per kind: operational tokens (UUIDs, trace ids, ...)
      that a predicted span touched, i.e. evidence the RCA would lose.
    """
    masked, typed, totals = Counter(), Counter(), Counter()
    fp, fp_by_type = 0, Counter()
    keep_hit, keep_total = Counter(), Counter()
    for item, preds in zip(items, predicted):
        pred_ranges = [(s, e) for _, s, e in preds]
        for gtype, s, e in item.spans:
            totals[gtype] += 1
            if _covered((s, e), pred_ranges):
                masked[gtype] += 1
            if any(pt == gtype and _overlaps((s, e), (ps, pe)) for pt, ps, pe in preds):
                typed[gtype] += 1
        for ptype, ps, pe in preds:
            if not any(_overlaps((ps, pe), (s, e)) for _, s, e in item.spans):
                fp += 1
                fp_by_type[ptype] += 1
        for kind, s, e in item.keep:
            keep_total[kind] += 1
            if any(_overlaps((s, e), r) for r in pred_ranges):
                keep_hit[kind] += 1
    total = sum(totals.values())
    return {
        "masked_recall": {t: round(masked[t] / totals[t], 4) for t in sorted(totals)},
        "typed_recall": {t: round(typed[t] / totals[t], 4) for t in sorted(totals)},
        "overall_masked_recall": round(sum(masked.values()) / total, 4) if total else None,
        "false_positive_spans": fp,
        "false_positives_by_type": dict(fp_by_type),
        "keep_violation_rate": {k: round(keep_hit[k] / keep_total[k], 4) for k in sorted(keep_total)},
    }


def binary_report(gold: Sequence[bool], pred: Sequence[bool], groups: Sequence[str] = ()) -> Dict[str, Any]:
    """Detection rate, false-flag rate, and the false-flag rate per source."""
    tp = sum(1 for g, p in zip(gold, pred) if g and p)
    fn = sum(1 for g, p in zip(gold, pred) if g and not p)
    fp = sum(1 for g, p in zip(gold, pred) if not g and p)
    tn = sum(1 for g, p in zip(gold, pred) if not g and not p)
    out = {
        "detection_rate": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_flag_rate": round(fp / (fp + tn), 4) if fp + tn else None,
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
    }
    if groups:
        by = defaultdict(lambda: [0, 0])
        for g, p, grp in zip(gold, pred, groups):
            if not g:
                by[grp][1] += 1
                by[grp][0] += int(p)
        out["false_flag_rate_by_source"] = {k: round(v[0] / v[1], 4) for k, v in by.items() if v[1]}
    return out


def grouping_accuracy(gold: Sequence[str], pred: Sequence[str]) -> Dict[str, Any]:
    """Loghub-style grouping accuracy (GA).

    A line counts as correct when the set of lines sharing its predicted group
    is exactly the set sharing its gold template.
    """
    gold_groups, pred_groups = defaultdict(set), defaultdict(set)
    for i, (g, p) in enumerate(zip(gold, pred)):
        gold_groups[g].add(i)
        pred_groups[p].add(i)
    correct = 0
    for members in pred_groups.values():
        first = next(iter(members))
        if members == gold_groups[gold[first]]:
            correct += len(members)
    return {
        "grouping_accuracy": round(correct / len(gold), 4) if gold else None,
        "gold_groups": len(gold_groups),
        "predicted_groups": len(pred_groups),
    }


def timed(fn: Callable[[Any], Any], inputs: Sequence[Any]) -> Tuple[List[Any], Dict[str, float]]:
    """Run *fn* over *inputs*; return outputs plus p50/p95 latency in ms."""
    outs, lat = [], []
    for x in inputs:
        t0 = time.perf_counter()
        outs.append(fn(x))
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    return outs, {
        "p50_ms": round(statistics.median(lat), 4) if lat else None,
        "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 4) if lat else None,
        "n": len(lat),
    }


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MB (Linux reports KB, macOS bytes)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)
