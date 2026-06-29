"""A/B harness — Code Crawler vs codegraph over the identical fixture + graders.

Answers "which backend is better" with *comparable* numbers: both engines run the
same AST-derived case set (find/body/trace) and are scored by the same
deterministic graders. We report per-op accuracy, per-case win/tie/loss, mean
latency, and mean result-tokens for each backend, then write a markdown artifact.

Run inside the agent-api container (needs Bedrock creds for the crawler index and
the baked codegraph binary):
    python -m evals.accuracy.run_ab            # print summary
    python -m evals.accuracy.run_ab --report   # also write reports/ab_report_<date>.md
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import os
from typing import Any, Dict, List, Tuple

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy.runner import _mean
from evals.accuracy.run_crawler import run_crawler_suite
from evals.accuracy.run_codegraph import run_codegraph_suite

HERE = os.path.dirname(__file__)
REPORTS_DIR = os.path.join(HERE, "reports")
_OPS = ("find", "body", "trace")


def _by_id(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["id"]: r for r in rows}


def _op_stats(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    stats: Dict[str, Dict[str, float]] = {}
    for op in _OPS:
        op_rows = [r for r in rows if r["op"] == op]
        stats[op] = {
            "mean": _mean(op_rows),
            "n": len(op_rows),
            "latency": round(sum(r.get("latency_s", 0) for r in op_rows) / len(op_rows), 3)
            if op_rows else 0.0,
            "tokens": round(sum(r.get("result_tokens", 0) for r in op_rows) / len(op_rows), 1)
            if op_rows else 0.0,
        }
    return stats


def aggregate(crawler: List[Dict[str, Any]], codegraph: List[Dict[str, Any]]) -> Dict[str, Any]:
    cg_by_id = _by_id(codegraph)
    wins = ties = losses = 0
    deltas: List[Dict[str, Any]] = []
    for cr in crawler:
        cg = cg_by_id.get(cr["id"])
        if not cg:
            continue
        d = round(cg["score"] - cr["score"], 3)
        if d > 0.0009:
            wins += 1
        elif d < -0.0009:
            losses += 1
        else:
            ties += 1
        if abs(d) > 0.0009:
            deltas.append({
                "id": cr["id"], "op": cr["op"],
                "crawler": cr["score"], "codegraph": cg["score"], "delta": d,
                "cg_diag": cg.get("diagnostic", ""),
            })
    return {
        "crawler": {
            "overall": _mean(crawler), "n": len(crawler),
            "latency": round(sum(r.get("latency_s", 0) for r in crawler) / len(crawler), 3)
            if crawler else 0.0,
            "by_op": _op_stats(crawler), "rows": crawler,
        },
        "codegraph": {
            "overall": _mean(codegraph), "n": len(codegraph),
            "latency": round(sum(r.get("latency_s", 0) for r in codegraph) / len(codegraph), 3)
            if codegraph else 0.0,
            "tokens": round(sum(r.get("result_tokens", 0) for r in codegraph) / len(codegraph), 1)
            if codegraph else 0.0,
            "by_op": _op_stats(codegraph), "rows": codegraph,
            "index_error": next((r.get("index_error") for r in codegraph if r.get("index_error")), None),
        },
        "headtohead": {"codegraph_wins": wins, "ties": ties, "codegraph_losses": losses},
        "deltas": sorted(deltas, key=lambda x: x["delta"]),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _print_summary(agg: Dict[str, Any]) -> None:
    cr, cg, h2h = agg["crawler"], agg["codegraph"], agg["headtohead"]
    print("\n=============== A/B: Code Crawler vs codegraph ===============")
    if cg.get("index_error"):
        print(f"!! codegraph index error: {cg['index_error']}")
    print(f"{'metric':<16}{'Code Crawler':>16}{'codegraph':>16}")
    print(f"{'overall acc':<16}{_pct(cr['overall']):>16}{_pct(cg['overall']):>16}")
    for op in _OPS:
        print(f"{'  ' + op + ' acc':<16}"
              f"{_pct(cr['by_op'][op]['mean']):>16}{_pct(cg['by_op'][op]['mean']):>16}")
    print(f"{'mean latency':<16}{cr['latency']:>15.3f}s{cg['latency']:>15.3f}s")
    print(f"{'mean tokens':<16}{'n/a':>16}{cg.get('tokens', 0):>16.1f}")
    print(f"\nhead-to-head: codegraph wins {h2h['codegraph_wins']}, "
          f"ties {h2h['ties']}, losses {h2h['codegraph_losses']} "
          f"(of {cr['n']} cases)")
    if agg["deltas"]:
        print("\nlargest per-case differences (codegraph − crawler):")
        for d in agg["deltas"][:10]:
            print(f"  {d['id']:<26} {d['delta']:+.2f}  cg={d['codegraph']:.2f} "
                  f"cr={d['crawler']:.2f}  {str(d['cg_diag'])[:60]}")
    print("==============================================================\n")


def render(agg: Dict[str, Any]) -> str:
    cr, cg, h2h = agg["crawler"], agg["codegraph"], agg["headtohead"]
    today = _dt.date.today().isoformat()
    L: List[str] = []
    add = L.append
    add("# A/B Accuracy & Performance — Code Crawler vs codegraph\n")
    add(f"_Generated: {today}_\n")
    add("Both backends run the identical AST-derived case set (find/body/trace from "
        "`fixtures/sample_repo`) and are scored by the same deterministic graders, so "
        "these numbers are directly comparable. Accuracy is the headline; latency and "
        "result-tokens quantify performance.\n")
    if cg.get("index_error"):
        add(f"> **codegraph index error:** {cg['index_error']}\n")
    add("## Headline\n")
    add("| Metric | Code Crawler | codegraph |")
    add("|---|---|---|")
    add(f"| Overall accuracy | **{_pct(cr['overall'])}** | **{_pct(cg['overall'])}** |")
    for op in _OPS:
        add(f"| {op} accuracy | {_pct(cr['by_op'][op]['mean'])} | {_pct(cg['by_op'][op]['mean'])} |")
    add(f"| Mean latency (s) | {cr['latency']:.3f} | {cg['latency']:.3f} |")
    add(f"| Mean result tokens | n/a | {cg.get('tokens', 0):.1f} |")
    add("")
    add(f"**Head-to-head:** codegraph wins {h2h['codegraph_wins']}, ties {h2h['ties']}, "
        f"loses {h2h['codegraph_losses']} (of {cr['n']} cases).\n")
    if agg["deltas"]:
        add("## Per-case differences (codegraph − crawler)\n")
        add("| Case | Op | Crawler | codegraph | Δ | codegraph diagnostic |")
        add("|---|---|---|---|---|---|")
        for d in agg["deltas"]:
            diag = str(d["cg_diag"])[:120].replace("|", "\\|")
            add(f"| {d['id']} | {d['op']} | {d['crawler']:.2f} | {d['codegraph']:.2f} "
                f"| {d['delta']:+.2f} | {diag} |")
        add("")
    add("## How to reproduce\n")
    add("```\n# inside the agent-api container\n"
        "python -m evals.accuracy.run_ab --report\n```\n")
    return "\n".join(L)


def write_report(agg: Dict[str, Any]) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    today = _dt.date.today().isoformat()
    path = os.path.join(REPORTS_DIR, f"ab_report_{today}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(agg))
    return path


async def run_all() -> Dict[str, Any]:
    # Crawler first (it sets repos_base_path to the writable fixture copy, which
    # codegraph then indexes from the same location).
    crawler = await run_crawler_suite()
    codegraph = await run_codegraph_suite()
    return aggregate(crawler, codegraph)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="write markdown report")
    args = ap.parse_args()
    agg = asyncio.run(run_all())
    _print_summary(agg)
    if args.report:
        path = write_report(agg)
        print(f"report written: {path}")


if __name__ == "__main__":
    main()
