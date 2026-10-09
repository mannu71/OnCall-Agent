"""Mini-model bake-off: score every candidate per role, with latency and memory.

Usage (from ``agent-api/``)::

    python -m evals.mini.run                      # all roles, all candidates
    python -m evals.mini.run --roles pii router   # a subset
    python -m evals.mini.run --out evals/mini/reports

Hermetic: no network, no Bedrock, no database. Writes ``bakeoff.json`` and
``bakeoff.md`` to ``--out`` when given, and prints the markdown summary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from evals.mini import corpora
from evals.mini.candidates import CANDIDATES
from evals.mini.metrics import (
    binary_report,
    classification_report,
    grouping_accuracy,
    misroute_rate,
    peak_rss_mb,
    span_report,
    timed,
)

DATASETS = Path(__file__).parent / "datasets"

#: Ship gates from the implementation plan; evaluated per candidate.
GATES = {
    "router": "investigation misroute <= 2% and p95 <= 50 ms",
    "pii": "no masked-recall regression vs baseline on today's 6 types; keep-violation rate <= 1%",
    "injection": "false-flag rate <= 1%",
    "templates": "grouping accuracy >= baseline",
}


def load_router() -> List[Dict[str, str]]:
    with open(DATASETS / "router.jsonl") as f:
        return [json.loads(line) for line in f if line.strip()]


def eval_router(fn) -> Dict[str, Any]:
    rows = load_router()
    preds, lat = timed(fn, [r["text"] for r in rows])
    gold = [r["label"] for r in rows]
    report = classification_report(gold, preds)
    report["investigate_misroute_rate"] = misroute_rate(gold, preds, "investigate")
    report["latency"] = lat
    report["gate_pass"] = report["investigate_misroute_rate"] <= 0.02 and lat["p95_ms"] <= 50
    return report


def eval_pii(fn) -> Dict[str, Any]:
    items = corpora.pii_corpus()
    preds, lat = timed(fn, [i.text for i in items])
    report = span_report(items, preds)
    report["latency"] = lat
    report["chars_per_second"] = round(
        sum(len(i.text) for i in items) / max(1e-9, lat["p50_ms"] * len(items) / 1000.0)
    )
    return report


def eval_injection(fn) -> Dict[str, Any]:
    items = corpora.injection_corpus()
    preds, lat = timed(fn, [i.text for i in items])
    report = binary_report([i.injection for i in items], preds, [i.source for i in items])
    report["latency"] = lat
    report["gate_pass"] = (report["false_flag_rate"] or 0) <= 0.01
    return report


def eval_templates(fn) -> Dict[str, Any]:
    items = corpora.template_corpus()
    preds, lat = timed(fn, [i.text for i in items])
    report = grouping_accuracy([i.template_id for i in items], preds)
    report["latency"] = lat
    return report


EVALUATORS = {
    "router": eval_router,
    "pii": eval_pii,
    "injection": eval_injection,
    "templates": eval_templates,
}


def _apply_relative_gates(results: Dict[str, Dict[str, Any]]) -> None:
    """Gates that compare a candidate with the baseline."""
    base_pii = results.get("pii", {}).get("baseline")
    for name, rep in results.get("pii", {}).items():
        if base_pii is None:
            break
        today = [t for t in base_pii["masked_recall"] if t in rep["masked_recall"]]
        no_regression = all(rep["masked_recall"][t] >= base_pii["masked_recall"][t] for t in today)
        keep_ok = all(v <= 0.01 for v in rep["keep_violation_rate"].values())
        rep["gate_pass"] = no_regression and keep_ok
    base_tpl = results.get("templates", {}).get("baseline")
    for name, rep in results.get("templates", {}).items():
        if base_tpl is not None:
            rep["gate_pass"] = rep["grouping_accuracy"] >= base_tpl["grouping_accuracy"]


def run(roles: List[str]) -> Dict[str, Any]:
    results: Dict[str, Dict[str, Any]] = {}
    for role in roles:
        results[role] = {name: EVALUATORS[role](fn) for name, fn in CANDIDATES[role].items()}
    _apply_relative_gates(results)
    return {"results": results, "gates": {r: GATES[r] for r in roles}, "peak_rss_mb": peak_rss_mb()}


def to_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Mini-model bake-off", "", f"Peak RSS: {report['peak_rss_mb']} MB", ""]
    for role, cands in report["results"].items():
        lines += [f"## {role}", "", f"Gate: {report['gates'][role]}", ""]
        for name, rep in cands.items():
            lat = rep.get("latency", {})
            lines.append(f"### {name}  (gate {'PASS' if rep.get('gate_pass') else 'FAIL'}, "
                         f"p50 {lat.get('p50_ms')} ms, p95 {lat.get('p95_ms')} ms)")
            lines.append("")
            shown = {k: v for k, v in rep.items() if k not in ("latency", "gate_pass", "confusion")}
            lines.append("```json")
            lines.append(json.dumps(shown, indent=2))
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--roles", nargs="*", default=list(EVALUATORS), choices=list(EVALUATORS))
    parser.add_argument("--out", default=None, help="directory for bakeoff.json / bakeoff.md")
    args = parser.parse_args(argv)
    report = run(args.roles)
    md = to_markdown(report)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        Path(args.out, "bakeoff.json").write_text(json.dumps(report, indent=2))
        Path(args.out, "bakeoff.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
