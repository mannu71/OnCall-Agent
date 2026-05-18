"""Eval-suite-local pytest configuration.

Hooks the terminal summary so a developer running ``pytest -m eval`` sees
the dual-metric report inline at the end of the run, without having to
open ``last_run_report.json`` manually.
"""
from __future__ import annotations

from typing import Any


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """Print a compact accuracy + tokens-per-correct-answer report."""
    try:
        from tests.eval.code_analyzer_eval import aggregate, get_results
    except ImportError:
        return

    results = get_results()
    if not results:
        return

    agg = aggregate(results)
    tr = terminalreporter

    tr.write_sep("=", "Code Analyzer Eval - Phase 0 baseline")
    tr.write_line(
        f"Overall: {agg.overall.correct}/{agg.overall.total} correct "
        f"({agg.overall.accuracy:.1%}) | "
        f"{agg.overall.tokens} tokens | "
        f"{agg.answers_per_kilo_token:.2f} answers/kT"
    )
    if agg.errors:
        tr.write_line(f"Infra errors (recorded as wrong): {agg.errors}")

    if agg.by_category:
        tr.write_sep("-", "By category")
        for cat, s in sorted(agg.by_category.items()):
            tpc = f"{s.tokens_per_correct:,.0f}" if s.correct else "n/a"
            tr.write_line(f"  {cat:<20} {s.correct}/{s.total} ({s.accuracy:.1%}) | {tpc} tokens/correct")

    if agg.by_language:
        tr.write_sep("-", "By language")
        for lang, s in sorted(agg.by_language.items()):
            tpc = f"{s.tokens_per_correct:,.0f}" if s.correct else "n/a"
            tr.write_line(f"  {lang:<20} {s.correct}/{s.total} ({s.accuracy:.1%}) | {tpc} tokens/correct")

    tr.write_sep("=", "End eval report")
