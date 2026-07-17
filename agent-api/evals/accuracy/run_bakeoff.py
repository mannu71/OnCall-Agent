"""Engine bake-off — LangGraph ``create_react_agent`` vs the native ``TurnLoop``.

Answers "which agent engine should we keep" with *comparable* numbers: the same
trajectory case set runs once per engine per repetition, graded by the same
deterministic graders, so accuracy is directly comparable. We also report the
performance and reliability metrics the result contract already carries —
latency p50/p95, tokens/case, tool-calls/case, recovery-ladder activations, and
failures — then apply the decision rule and write a markdown artifact.

Decision rule: native must MATCH the langgraph objective accuracy (within the
tie threshold) AND not materially regress latency/tokens to be adopted; ties
break toward langgraph (keeping native carries extra unbuilt cost — durable
checkpointing + HITL). The report's Verdict section applies this mechanically.

Run inside the agent-api container (needs Bedrock creds for the agent loop):
    python -m evals.accuracy.run_bakeoff                       # print summary
    python -m evals.accuracy.run_bakeoff --report              # + write report
    python -m evals.accuracy.run_bakeoff --report --runs 3     # 3 reps/engine
    python -m evals.accuracy.run_bakeoff --engines langgraph,native
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import os
from typing import Any, Dict, List, Optional, Tuple

from evals.accuracy import _bootstrap  # noqa: F401  (boto3 SSL patch on import)
from evals.accuracy.run_trajectory import run_trajectory_suite

HERE = os.path.dirname(__file__)
REPORTS_DIR = os.path.join(HERE, "reports")

# Score-delta band inside which two engines are called equal (matches run_ab).
_TIE = 0.0009
# Latency / token regression the native engine may not exceed to be "no worse"
# on performance (relative). Advisory — surfaced in the verdict, operator-final.
_PERF_REGRESSION_FRAC = 0.15


# ── small stats helpers (no new deps) ─────────────────────────────────────────
def _mean(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def _pctl(vals: List[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0,1]); 0.0 for empty input."""
    if not vals:
        return 0.0
    xs = sorted(vals)
    if len(xs) == 1:
        return round(xs[0], 3)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return round(xs[lo] + (xs[hi] - xs[lo]) * frac, 3)


def _obj_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if r.get("objective")]


def _case_key(r: Dict[str, Any]) -> Tuple[str, str]:
    return (r.get("id", ""), r.get("metric", ""))


# ── per-engine rollup ─────────────────────────────────────────────────────────
def _engine_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate one engine's rows (possibly across several runs)."""
    obj = _obj_rows(rows)
    # One latency/token/tool sample per (case, run) — dedupe across the 4 metric
    # rows that share the same run so per-case perf isn't quadruple-counted.
    seen: set = set()
    lat: List[float] = []
    toks: List[float] = []
    tool_calls: List[float] = []
    recovery = 0
    per_run_units = 0
    for r in rows:
        # One perf sample per (case, engine, run) — the 4 metric rows of a run
        # share the same latency/tokens/tool_calls, so collapse them.
        unit = (r.get("id"), r.get("engine"), r.get("_run_idx"))
        if unit in seen:
            continue
        seen.add(unit)
        per_run_units += 1
        lat.append(float(r.get("latency_s", 0) or 0))
        toks.append(float(r.get("total_tokens", 0) or 0))
        tool_calls.append(float(r.get("tool_calls_n", 0) or 0))
        if (r.get("stop_reason") not in (None, "completed")
                or r.get("did_forced_synthesis") or r.get("truncated")):
            recovery += 1
    failures = [r for r in obj if r.get("score", 0) < 0.999]
    return {
        "objective_accuracy": _mean([r["score"] for r in obj]),
        "objective_n": len(obj),
        "objective_passed": sum(1 for r in obj if r.get("score", 0) >= 0.999),
        "latency_mean": _mean(lat),
        "latency_p50": _pctl(lat, 0.50),
        "latency_p95": _pctl(lat, 0.95),
        "tokens_mean": round(sum(toks) / len(toks), 1) if toks else 0.0,
        "tokens_total": int(sum(toks)),
        "tool_calls_mean": round(sum(tool_calls) / len(tool_calls), 2) if tool_calls else 0.0,
        "recovery_activations": recovery,
        "run_units": per_run_units,
        "failures": len(failures),
        "failure_cases": sorted({r.get("id", "") for r in failures}),
        "retried": sum(1 for r in rows if r.get("retried")),
        "exception_cases": sorted({r.get("id", "") for r in rows if r.get("stop_reason") == "exception"}),
        "rows": rows,
    }


def _headtohead(base: List[Dict[str, Any]], cand: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-case (id,metric) mean-score comparison: candidate vs baseline."""
    def _mean_by_case(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], float]:
        acc: Dict[Tuple[str, str], List[float]] = {}
        for r in _obj_rows(rows):
            acc.setdefault(_case_key(r), []).append(float(r.get("score", 0)))
        return {k: sum(v) / len(v) for k, v in acc.items()}

    b = _mean_by_case(base)
    c = _mean_by_case(cand)
    wins = ties = losses = 0
    deltas: List[Dict[str, Any]] = []
    for key in sorted(set(b) | set(c)):
        bd = b.get(key, 0.0)
        cd = c.get(key, 0.0)
        d = round(cd - bd, 4)
        if d > _TIE:
            wins += 1
        elif d < -_TIE:
            losses += 1
        else:
            ties += 1
        if abs(d) > _TIE:
            diag = next((r.get("diagnostic", "") for r in cand
                         if _case_key(r) == key and r.get("score", 1) < 0.999), "")
            deltas.append({"id": key[0], "metric": key[1], "baseline": round(bd, 3),
                           "candidate": round(cd, 3), "delta": d, "diag": diag})
    return {"candidate_wins": wins, "ties": ties, "candidate_losses": losses,
            "deltas": sorted(deltas, key=lambda x: x["delta"])}


def _verdict(agg: Dict[str, Any], baseline: str, candidate: str) -> Dict[str, Any]:
    """Mechanically apply the decision rule → recommendation + reasons."""
    b = agg["engines"][baseline]
    c = agg["engines"].get(candidate)
    if c is None:
        return {"recommendation": baseline, "reasons": [f"only {baseline} ran"]}
    reasons: List[str] = []

    acc_delta = round(c["objective_accuracy"] - b["objective_accuracy"], 4)
    accuracy_ok = acc_delta >= -_TIE  # candidate matches-or-beats baseline
    reasons.append(
        f"accuracy: {candidate} {c['objective_accuracy']:.4f} vs {baseline} "
        f"{b['objective_accuracy']:.4f} (Δ{acc_delta:+.4f}) → "
        f"{'meets bar' if accuracy_ok else 'REGRESSES'}")

    def _regress(cv: float, bv: float) -> float:
        return (cv - bv) / bv if bv else 0.0

    lat_reg = _regress(c["latency_p50"], b["latency_p50"])
    tok_reg = _regress(c["tokens_mean"], b["tokens_mean"])
    perf_ok = lat_reg <= _PERF_REGRESSION_FRAC and tok_reg <= _PERF_REGRESSION_FRAC
    reasons.append(
        f"latency p50: {candidate} {c['latency_p50']:.3f}s vs {baseline} "
        f"{b['latency_p50']:.3f}s ({lat_reg*100:+.1f}%)")
    reasons.append(
        f"tokens/case: {candidate} {c['tokens_mean']:.0f} vs {baseline} "
        f"{b['tokens_mean']:.0f} ({tok_reg*100:+.1f}%)")

    if c["exception_cases"]:
        reasons.append(f"{candidate} raised exceptions on {len(c['exception_cases'])} case(s)")

    adopt = accuracy_ok and perf_ok and not c["exception_cases"]
    # Tie on accuracy AND perf breaks toward the baseline (langgraph) per the rule.
    rec = candidate if adopt and acc_delta > _TIE else baseline
    if adopt and abs(acc_delta) <= _TIE:
        reasons.append(f"accuracy tie → break toward baseline ({baseline})")
    reasons.append(
        f"→ candidate {'ADOPTABLE' if adopt else 'NOT adoptable'}; "
        f"recommend keeping **{rec}**")
    return {"recommendation": rec, "adoptable": adopt, "accuracy_ok": accuracy_ok,
            "perf_ok": perf_ok, "accuracy_delta": acc_delta, "reasons": reasons}


def aggregate(rows_by_engine: Dict[str, List[Dict[str, Any]]], *,
              baseline: str = "langgraph") -> Dict[str, Any]:
    engines = {name: _engine_stats(rows) for name, rows in rows_by_engine.items()}
    candidate = next((e for e in rows_by_engine if e != baseline), None)
    agg: Dict[str, Any] = {"engines": engines, "baseline": baseline, "candidate": candidate}
    if candidate and baseline in rows_by_engine:
        agg["headtohead"] = _headtohead(rows_by_engine[baseline], rows_by_engine[candidate])
        agg["verdict"] = _verdict(agg, baseline, candidate)
    return agg


# ── report emission ───────────────────────────────────────────────────────────
def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _print_summary(agg: Dict[str, Any]) -> None:
    names = list(agg["engines"])
    print("\n=============== Engine bake-off: " + " vs ".join(names) + " ===============")
    hdr = f"{'metric':<22}" + "".join(f"{n:>16}" for n in names)
    print(hdr)
    def _row(label: str, fmt) -> None:
        print(f"{label:<22}" + "".join(f"{fmt(agg['engines'][n]):>16}" for n in names))
    _row("objective acc", lambda e: _pct(e["objective_accuracy"]))
    _row("obj passed/n", lambda e: f"{e['objective_passed']}/{e['objective_n']}")
    _row("latency p50 (s)", lambda e: f"{e['latency_p50']:.3f}")
    _row("latency p95 (s)", lambda e: f"{e['latency_p95']:.3f}")
    _row("tokens/case", lambda e: f"{e['tokens_mean']:.0f}")
    _row("tool calls/case", lambda e: f"{e['tool_calls_mean']:.2f}")
    _row("recovery activ.", lambda e: str(e["recovery_activations"]))
    _row("failures", lambda e: str(e["failures"]))
    _row("exceptions", lambda e: str(len(e["exception_cases"])))
    if "headtohead" in agg:
        h = agg["headtohead"]
        print(f"\nhead-to-head ({agg['candidate']} vs {agg['baseline']}): "
              f"wins {h['candidate_wins']}, ties {h['ties']}, losses {h['candidate_losses']}")
    if "verdict" in agg:
        print("\nVERDICT:")
        for r in agg["verdict"]["reasons"]:
            print(f"  - {r}")
    print("=" * 62 + "\n")


def render(agg: Dict[str, Any]) -> str:
    names = list(agg["engines"])
    today = _dt.date.today().isoformat()
    L: List[str] = []
    add = L.append
    add("# Engine bake-off — " + " vs ".join(names) + "\n")
    add(f"_Generated: {today}_\n")
    add("Both engines run the identical trajectory case set and are scored by the same "
        "deterministic graders, so accuracy is directly comparable. Latency, tokens, "
        "tool-calls, and recovery activations quantify performance/reliability.\n")
    add("## Headline\n")
    add("| Metric | " + " | ".join(names) + " |")
    add("|---|" + "---|" * len(names))
    def _mrow(label: str, fmt) -> None:
        add(f"| {label} | " + " | ".join(fmt(agg["engines"][n]) for n in names) + " |")
    _mrow("Objective accuracy", lambda e: f"**{_pct(e['objective_accuracy'])}**")
    _mrow("Objective passed / n", lambda e: f"{e['objective_passed']}/{e['objective_n']}")
    _mrow("Latency p50 (s)", lambda e: f"{e['latency_p50']:.3f}")
    _mrow("Latency p95 (s)", lambda e: f"{e['latency_p95']:.3f}")
    _mrow("Latency mean (s)", lambda e: f"{e['latency_mean']:.3f}")
    _mrow("Tokens / case", lambda e: f"{e['tokens_mean']:.0f}")
    _mrow("Tool calls / case", lambda e: f"{e['tool_calls_mean']:.2f}")
    _mrow("Recovery activations", lambda e: str(e["recovery_activations"]))
    _mrow("Failures", lambda e: str(e["failures"]))
    _mrow("Exceptions", lambda e: str(len(e["exception_cases"])))
    _mrow("Retried rows", lambda e: str(e["retried"]))
    add("")
    if "headtohead" in agg:
        h = agg["headtohead"]
        add(f"**Head-to-head ({agg['candidate']} vs {agg['baseline']}):** "
            f"wins {h['candidate_wins']}, ties {h['ties']}, "
            f"losses {h['candidate_losses']}.\n")
        if h["deltas"]:
            add("## Per-case differences (candidate − baseline)\n")
            add("| Case | Metric | Baseline | Candidate | Δ | Candidate diagnostic |")
            add("|---|---|---|---|---|---|")
            for d in h["deltas"]:
                diag = str(d["diag"])[:120].replace("|", "\\|")
                add(f"| {d['id']} | {d['metric']} | {d['baseline']:.2f} | "
                    f"{d['candidate']:.2f} | {d['delta']:+.3f} | {diag} |")
            add("")
    if "verdict" in agg:
        add("## Verdict\n")
        add(f"**Recommendation: keep `{agg['verdict']['recommendation']}`.**\n")
        for r in agg["verdict"]["reasons"]:
            add(f"- {r}")
        add("")
    add("## How to reproduce\n")
    add("```\n# inside the agent-api container\n"
        "python -m evals.accuracy.run_bakeoff --report --runs 3\n```\n")
    return "\n".join(L)


def write_report(agg: Dict[str, Any]) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    today = _dt.date.today().isoformat()
    path = os.path.join(REPORTS_DIR, f"bakeoff_report_{today}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(agg))
    return path


async def run_all(engines: Tuple[str, ...] = ("langgraph", "native"),
                  runs: int = 3, baseline: str = "langgraph") -> Dict[str, Any]:
    """Run the trajectory suite ``runs`` times per engine, serially (one Bedrock
    loop at a time keeps latency comparable), tagging each row with its engine +
    run index, then aggregate."""
    rows_by_engine: Dict[str, List[Dict[str, Any]]] = {e: [] for e in engines}
    for engine in engines:
        for run_idx in range(runs):
            rows = await run_trajectory_suite(engine=engine)
            for r in rows:
                r["engine"] = engine        # authoritative tag (suite also sets it)
                r["_run_idx"] = run_idx
            rows_by_engine[engine].extend(rows)
    return aggregate(rows_by_engine, baseline=baseline)


def main() -> None:
    ap = argparse.ArgumentParser(description="LangGraph vs native engine bake-off")
    ap.add_argument("--report", action="store_true", help="write markdown report")
    ap.add_argument("--runs", type=int, default=3, help="repetitions per engine (default 3)")
    ap.add_argument("--engines", type=str, default="langgraph,native",
                    help="comma-separated engines (first is the baseline)")
    args = ap.parse_args()
    engines = tuple(e.strip() for e in args.engines.split(",") if e.strip())
    baseline = engines[0] if engines else "langgraph"
    agg = asyncio.run(run_all(engines=engines, runs=args.runs, baseline=baseline))
    _print_summary(agg)
    if args.report:
        path = write_report(agg)
        print(f"report written: {path}")


if __name__ == "__main__":
    main()
