"""Orchestrate both suites, aggregate per-metric accuracy, and emit the report.

Headline "objective accuracy" is built only from deterministic checks
(crawler find/body/trace; CloudWatch schema + ID-grounding). Severity and
judge-faithfulness are reported as secondary/semantic signals, not folded into
the headline.

Run:  python -m evals.accuracy.runner --report
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict, List

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy.run_crawler import run_crawler_suite
from evals.accuracy.run_cloudwatch import run_cloudwatch_suite


def _mean(rows: List[Dict[str, Any]]) -> float:
    return round(sum(r["score"] for r in rows) / len(rows), 4) if rows else 0.0


def aggregate(crawler: List[Dict[str, Any]], cloud: List[Dict[str, Any]]) -> Dict[str, Any]:
    cw_obj = [r for r in cloud if r.get("objective")]
    cw_sev = [r for r in cloud if r["metric"] == "severity"]
    cw_judge = [r for r in cloud if r["metric"] == "judge_faithfulness"]

    by_op = {}
    for op in ("find", "body", "trace"):
        rows = [r for r in crawler if r["op"] == op]
        by_op[op] = {"mean": _mean(rows), "n": len(rows),
                     "passed": sum(1 for r in rows if r["score"] >= 0.999)}

    cw_by_metric = {}
    for metric in ("schema", "id_grounding", "severity", "judge_faithfulness"):
        rows = [r for r in cloud if r["metric"] == metric]
        cw_by_metric[metric] = {"mean": _mean(rows), "n": len(rows),
                                "passed": sum(1 for r in rows if r["score"] >= 0.999)}

    return {
        "crawler": {
            "objective_accuracy": _mean(crawler),
            "n": len(crawler),
            "passed": sum(1 for r in crawler if r["score"] >= 0.999),
            "by_op": by_op,
            "rows": crawler,
        },
        "cloudwatch": {
            "objective_accuracy": _mean(cw_obj),
            "objective_n": len(cw_obj),
            "objective_passed": sum(1 for r in cw_obj if r["score"] >= 0.999),
            "severity_accuracy": _mean(cw_sev),
            "judge_faithfulness": _mean(cw_judge),
            "by_metric": cw_by_metric,
            "rows": cloud,
        },
    }


async def run_all() -> Dict[str, Any]:
    crawler = await run_crawler_suite()
    cloud = await run_cloudwatch_suite()
    return aggregate(crawler, cloud)


def _print_summary(agg: Dict[str, Any]) -> None:
    c = agg["crawler"]
    w = agg["cloudwatch"]
    print("\n==================== ACCURACY SUMMARY ====================")
    print(f"CodeCrawler  objective accuracy : {c['objective_accuracy']*100:6.2f}%  "
          f"({c['passed']}/{c['n']} cases)")
    for op, s in c["by_op"].items():
        print(f"    - {op:<6}: {s['mean']*100:6.2f}%  ({s['passed']}/{s['n']})")
    print(f"CloudWatch   objective accuracy : {w['objective_accuracy']*100:6.2f}%  "
          f"({w['objective_passed']}/{w['objective_n']} checks: schema + id-grounding)")
    print(f"    - severity (semantic)  : {w['severity_accuracy']*100:6.2f}%")
    print(f"    - judge faithfulness   : {w['judge_faithfulness']*100:6.2f}%")
    print("==========================================================\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="write markdown report")
    args = ap.parse_args()
    agg = asyncio.run(run_all())
    _print_summary(agg)
    if args.report:
        from evals.accuracy.report import write_report
        path = write_report(agg)
        print(f"report written: {path}")


if __name__ == "__main__":
    main()
