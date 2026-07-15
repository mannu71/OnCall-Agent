"""Orchestrate the suites, aggregate per-metric accuracy, and emit the report.

Headline "objective accuracy" is built only from deterministic checks
(Code Crawler/codegraph find/body/trace; CloudWatch schema + ID-grounding).
Severity and judge-faithfulness are reported as secondary/semantic signals, not
folded into the headline.

Run:  python -m evals.accuracy.runner --report
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any, Dict, List

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy.run_codegraph import run_codegraph_suite
from evals.accuracy.run_cloudwatch import run_cloudwatch_suite
from evals.accuracy.run_trajectory import run_trajectory_suite
from evals.accuracy.run_lookup import run_lookup_suite

_TRAJ_METRICS = ("tool_selection", "protocol", "final_answer", "grounding", "post_check")


def _mean(rows: List[Dict[str, Any]]) -> float:
    return round(sum(r["score"] for r in rows) / len(rows), 4) if rows else 0.0


def aggregate(
    codegraph: List[Dict[str, Any]],
    cloud: List[Dict[str, Any]],
    trajectory: List[Dict[str, Any]] | None = None,
    lookup: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    cw_obj = [r for r in cloud if r.get("objective")]
    cw_sev = [r for r in cloud if r["metric"] == "severity"]
    cw_judge = [r for r in cloud if r["metric"] == "judge_faithfulness"]

    by_op = {}
    for op in ("find", "body", "trace"):
        rows = [r for r in codegraph if r["op"] == op]
        by_op[op] = {"mean": _mean(rows), "n": len(rows),
                     "passed": sum(1 for r in rows if r["score"] >= 0.999)}

    cw_by_metric = {}
    for metric in ("schema", "id_grounding", "severity", "judge_faithfulness"):
        rows = [r for r in cloud if r["metric"] == metric]
        cw_by_metric[metric] = {"mean": _mean(rows), "n": len(rows),
                                "passed": sum(1 for r in rows if r["score"] >= 0.999)}

    trajectory = trajectory or []
    traj_obj = [r for r in trajectory if r.get("objective")]
    traj_by_metric = {}
    for metric in _TRAJ_METRICS:
        rows = [r for r in trajectory if r["metric"] == metric]
        if rows:
            traj_by_metric[metric] = {"mean": _mean(rows), "n": len(rows),
                                      "passed": sum(1 for r in rows if r["score"] >= 0.999)}

    lookup = lookup or []

    return {
        "codegraph": {
            "objective_accuracy": _mean(codegraph),
            "n": len(codegraph),
            "passed": sum(1 for r in codegraph if r["score"] >= 0.999),
            "by_op": by_op,
            "rows": codegraph,
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
        "trajectory": {
            "objective_accuracy": _mean(traj_obj),
            "objective_n": len(traj_obj),
            "objective_passed": sum(1 for r in traj_obj if r["score"] >= 0.999),
            "retried": sum(1 for r in trajectory if r.get("retried")),
            "by_metric": traj_by_metric,
            "rows": trajectory,
        },
        "lookup": {
            "objective_accuracy": _mean(lookup),
            "n": len(lookup),
            "passed": sum(1 for r in lookup if r["score"] >= 0.999),
            "rows": lookup,
        },
    }


async def run_all() -> Dict[str, Any]:
    codegraph = await run_codegraph_suite()
    cloud = await run_cloudwatch_suite()
    trajectory = await run_trajectory_suite()
    lookup = await run_lookup_suite()
    return aggregate(codegraph, cloud, trajectory, lookup)


def _print_summary(agg: Dict[str, Any]) -> None:
    c = agg["codegraph"]
    w = agg["cloudwatch"]
    print("\n==================== ACCURACY SUMMARY ====================")
    print(f"CodeCrawler  objective accuracy : {c['objective_accuracy']*100:6.2f}%  "
          f"({c['passed']}/{c['n']} cases)")  # codegraph engine
    for op, s in c["by_op"].items():
        print(f"    - {op:<6}: {s['mean']*100:6.2f}%  ({s['passed']}/{s['n']})")
    print(f"CloudWatch   objective accuracy : {w['objective_accuracy']*100:6.2f}%  "
          f"({w['objective_passed']}/{w['objective_n']} checks: schema + id-grounding)")
    print(f"    - severity (semantic)  : {w['severity_accuracy']*100:6.2f}%")
    print(f"    - judge faithfulness   : {w['judge_faithfulness']*100:6.2f}%")
    t = agg.get("trajectory") or {}
    if t.get("objective_n"):
        print(f"Agent trajectory objective acc. : {t['objective_accuracy']*100:6.2f}%  "
              f"({t['objective_passed']}/{t['objective_n']} checks; {t.get('retried', 0)} case-rows retried)")
        for metric, s in (t.get("by_metric") or {}).items():
            print(f"    - {metric:<14}: {s['mean']*100:6.2f}%  ({s['passed']}/{s['n']})")
    lk = agg.get("lookup") or {}
    if lk.get("n"):
        print(f"Lazy-lookup recall accuracy     : {lk['objective_accuracy']*100:6.2f}%  "
              f"({lk['passed']}/{lk['n']})")
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
