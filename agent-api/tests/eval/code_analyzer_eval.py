"""Phase 0 — dual-metric code analyzer evaluation harness.

For every ground-truth question the harness:
  1. Calls the named tool with the given args (deterministic — no LLM).
  2. Grades the result against ``expected`` validators.
  3. Records token cost (estimated chars/4) of the args + result payload.
  4. Aggregates per-category F1 and the headline metric:
        answers_per_kilo_token = correct_count / (sum(tokens) / 1000)

Run only via:
    pytest -m eval tests/eval/code_analyzer_eval.py

Requirements:
  * The target repo (``item.repo``) must already be indexed (PostgreSQL +
    pgvector). The harness skips if Postgres is unreachable or returns no
    rows for the test repo.

The eval intentionally measures **only the tool-surface contribution** —
how many tokens the tool returns to satisfy a question. End-to-end LLM
loops are out of scope here; they belong to a separate integration eval.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import pytest

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants — kept explicit so future changes are obvious in diffs.
# ─────────────────────────────────────────────────────────────────────────────

_GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"
_REPORT_PATH = Path(__file__).parent / "last_run_report.json"
_CHARS_PER_TOKEN = 4  # matches app/core/prompt_caching.py:_estimate_tokens

VALID_CATEGORIES = {
    "definition",          # "Where is symbol X defined?"
    "references",          # "Where is X used?"
    "callers",             # "Who calls X?"
    "callees",             # "What does X call?" (no current tool — future)
    "semantic",            # "Find code about Y" (fuzzy intent)
    "cross_file",          # X called from a different file than its definition
    "ambiguous",           # multiple symbols share a name
    "refactor_survival",   # symbol moved/renamed but should still resolve
}

VALID_TOOLS = {
    "search_code",
    "get_function",
    "get_callers",
    "get_recent_changes",
    "get_file_context",
}


# ─────────────────────────────────────────────────────────────────────────────
# Data shapes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvalItem:
    """One ground-truth question loaded from JSONL."""
    id: str
    category: str
    language: str
    repo: str
    question: str
    tool: str
    args: Dict[str, Any]
    expected: Dict[str, Any]
    source_file: str = ""

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(
                f"{self.id}: category {self.category!r} not in {sorted(VALID_CATEGORIES)}"
            )
        if self.tool not in VALID_TOOLS:
            raise ValueError(
                f"{self.id}: tool {self.tool!r} not in {sorted(VALID_TOOLS)}"
            )


@dataclass
class EvalResult:
    """Outcome of one item — collected across the run for aggregation."""
    item: EvalItem
    correct: bool
    detail: str
    arg_tokens: int
    result_tokens: int
    latency_ms: float
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.arg_tokens + self.result_tokens


@dataclass
class CategoryStats:
    total: int = 0
    correct: int = 0
    tokens: int = 0

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0

    @property
    def tokens_per_correct(self) -> float:
        return (self.tokens / self.correct) if self.correct else float("inf")


# Module-level results bucket — populated by the parametrized test, read by
# the terminal-summary hook in conftest.py.
_RESULTS: List[EvalResult] = []


def get_results() -> List[EvalResult]:
    """Accessor for the conftest reporter — keeps the bucket internal."""
    return list(_RESULTS)


def _reset_results() -> None:
    _RESULTS.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> List[EvalItem]:
    items: List[EvalItem] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("//"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{lineno} bad JSON: {exc}") from exc
        obj.setdefault("source_file", path.name)
        items.append(EvalItem(**obj))
    return items


def load_all_items() -> List[EvalItem]:
    """Load every *.jsonl file under ground_truth/. Empty list is allowed."""
    if not _GROUND_TRUTH_DIR.exists():
        return []
    items: List[EvalItem] = []
    for jsonl in sorted(_GROUND_TRUTH_DIR.glob("*.jsonl")):
        items.extend(_load_jsonl(jsonl))
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Token estimation — matches the project's existing chars/4 heuristic so
# the eval reports the same units the rest of the system uses.
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(payload: Any) -> int:
    """Estimate token count of ``payload`` after JSON serialization."""
    if payload is None:
        return 0
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    return max(0, len(text) // _CHARS_PER_TOKEN)


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatch
# ─────────────────────────────────────────────────────────────────────────────

ToolFn = Callable[..., Awaitable[Any]]


def _resolve_tool(name: str) -> ToolFn:
    """Map a tool name to its async function. Imported lazily to keep the
    eval importable even when DB modules fail to load."""
    from app.mcp.tools import code_tools as _ct
    fn = getattr(_ct, name, None)
    if fn is None or not callable(fn):
        raise RuntimeError(f"Tool {name!r} not found in app.mcp.tools.code_tools")
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# Grading
# ─────────────────────────────────────────────────────────────────────────────

def _match_value(actual: Any, predicate: Dict[str, Any]) -> bool:
    """Apply a single field predicate. Predicate keys: equals, contains, regex."""
    if "equals" in predicate:
        if actual != predicate["equals"]:
            return False
    if "contains" in predicate:
        needle = str(predicate["contains"])
        if needle not in str(actual or ""):
            return False
    if "regex" in predicate:
        import re as _re
        if not _re.search(predicate["regex"], str(actual or "")):
            return False
    return True


def _row_matches(row: Dict[str, Any], must_match: Dict[str, Dict[str, Any]]) -> bool:
    return all(_match_value(row.get(field), pred) for field, pred in must_match.items())


def grade(item: EvalItem, result: Any) -> tuple[bool, str]:
    """Return (correct, detail). Detail is a short reason string for reports."""
    expected = item.expected
    kind = expected.get("kind", "single_dict")

    # Tool errored out — never correct.
    if isinstance(result, dict) and "error" in result:
        return False, f"tool_error: {result['error']}"

    if kind == "single_dict":
        if not isinstance(result, dict):
            return False, f"expected dict, got {type(result).__name__}"
        must = expected.get("must_match", {})
        ok = _row_matches(result, must)
        return ok, "match" if ok else f"mismatch on {list(must)}"

    if kind == "list_contains":
        if not isinstance(result, list):
            return False, f"expected list, got {type(result).__name__}"
        must = expected.get("must_have_one", {})
        for row in result:
            if isinstance(row, dict) and _row_matches(row, must):
                return True, "found"
        return False, f"no row matched {list(must)} in {len(result)} results"

    if kind == "list_recall":
        if not isinstance(result, list):
            return False, f"expected list, got {type(result).__name__}"
        targets: List[Dict[str, Any]] = expected.get("expected_items", [])
        min_recall = float(expected.get("min_recall", 1.0))
        if not targets:
            return False, "list_recall with empty expected_items"
        hits = sum(
            1 for tgt in targets
            if any(isinstance(r, dict) and _row_matches(r, tgt) for r in result)
        )
        recall = hits / len(targets)
        ok = recall >= min_recall
        return ok, f"recall={recall:.2f} (need {min_recall})"

    return False, f"unknown grading kind: {kind!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation — used by conftest's terminal summary
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Aggregate:
    overall: CategoryStats = field(default_factory=CategoryStats)
    by_category: Dict[str, CategoryStats] = field(default_factory=dict)
    by_language: Dict[str, CategoryStats] = field(default_factory=dict)
    errors: int = 0

    @property
    def answers_per_kilo_token(self) -> float:
        return (self.overall.correct * 1000.0 / self.overall.tokens) if self.overall.tokens else 0.0


def aggregate(results: List[EvalResult]) -> Aggregate:
    agg = Aggregate()
    for r in results:
        agg.overall.total += 1
        agg.overall.tokens += r.total_tokens
        if r.correct:
            agg.overall.correct += 1
        if r.error:
            agg.errors += 1

        cat = agg.by_category.setdefault(r.item.category, CategoryStats())
        cat.total += 1
        cat.tokens += r.total_tokens
        if r.correct:
            cat.correct += 1

        lang = agg.by_language.setdefault(r.item.language, CategoryStats())
        lang.total += 1
        lang.tokens += r.total_tokens
        if r.correct:
            lang.correct += 1
    return agg


def write_report(agg: Aggregate, results: List[EvalResult]) -> Path:
    payload = {
        "headline": {
            "total": agg.overall.total,
            "correct": agg.overall.correct,
            "accuracy": round(agg.overall.accuracy, 4),
            "total_tokens": agg.overall.tokens,
            "tokens_per_correct": round(agg.overall.tokens_per_correct, 1)
                if agg.overall.correct else None,
            "answers_per_kilo_token": round(agg.answers_per_kilo_token, 4),
            "errors": agg.errors,
        },
        "by_category": {
            cat: {
                "total": s.total, "correct": s.correct,
                "accuracy": round(s.accuracy, 4),
                "tokens_per_correct": round(s.tokens_per_correct, 1)
                    if s.correct else None,
            }
            for cat, s in sorted(agg.by_category.items())
        },
        "by_language": {
            lang: {
                "total": s.total, "correct": s.correct,
                "accuracy": round(s.accuracy, 4),
                "tokens_per_correct": round(s.tokens_per_correct, 1)
                    if s.correct else None,
            }
            for lang, s in sorted(agg.by_language.items())
        },
        "items": [
            {
                "id": r.item.id, "category": r.item.category,
                "language": r.item.language, "tool": r.item.tool,
                "correct": r.correct, "detail": r.detail,
                "tokens": r.total_tokens, "latency_ms": round(r.latency_ms, 1),
                "error": r.error,
            }
            for r in results
        ],
    }
    _REPORT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _REPORT_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Pytest integration
# ─────────────────────────────────────────────────────────────────────────────

def _items_for_parametrize() -> List[EvalItem]:
    items = load_all_items()
    if not items:
        return []
    return items


@pytest.fixture(scope="session", autouse=True)
def _reset_results_bucket():
    """Ensure a clean bucket per session; conftest reads after the session."""
    _reset_results()
    yield


@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    _items_for_parametrize() or [pytest.param(None, marks=pytest.mark.skip(reason="no ground-truth questions found"))],
    ids=lambda it: getattr(it, "id", "no-items"),
)
async def test_eval_question(item: Optional[EvalItem]) -> None:
    """One pytest case per ground-truth question. All are recorded; none fail
    individually. The aggregate gate is enforced by ``test_zzz_aggregate_gate``
    so a single failed question does not abort collection of the rest.
    """
    if item is None:
        pytest.skip("no ground-truth questions found under tests/eval/ground_truth/")

    try:
        tool = _resolve_tool(item.tool)
    except RuntimeError as exc:
        pytest.skip(f"tool unavailable: {exc}")

    arg_tokens = estimate_tokens(item.args)

    t0 = time.perf_counter()
    error: Optional[str] = None
    result: Any = None
    try:
        result = await tool(**item.args)
    except Exception as exc:
        # Most likely cause: Postgres unreachable / pgvector missing /
        # Bedrock credentials missing. Record as error, not a test failure,
        # so the aggregate report shows the count.
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("Tool %s raised: %s", item.tool, error)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    if error is not None:
        _RESULTS.append(EvalResult(
            item=item, correct=False, detail="infra_error",
            arg_tokens=arg_tokens, result_tokens=0,
            latency_ms=latency_ms, error=error,
        ))
        pytest.skip(f"infra error (recorded): {error}")

    correct, detail = grade(item, result)
    _RESULTS.append(EvalResult(
        item=item, correct=correct, detail=detail,
        arg_tokens=arg_tokens,
        result_tokens=estimate_tokens(result),
        latency_ms=latency_ms, error=None,
    ))


@pytest.mark.eval
def test_zzz_aggregate_gate() -> None:
    """Final aggregate: writes the JSON report, applies CI thresholds.

    Threshold env vars (skip the gate if unset — useful for first-run baselines):
      ECC_EVAL_MIN_ACCURACY      — float in [0,1], min overall accuracy
      ECC_EVAL_MIN_ANSWERS_PER_KT — float, min answers per kilo-token
    """
    results = get_results()
    if not results:
        pytest.skip("no eval results recorded")

    agg = aggregate(results)
    report_path = write_report(agg, results)
    logger.info("Eval report written to %s", report_path)

    min_acc = os.getenv("ECC_EVAL_MIN_ACCURACY")
    min_apkt = os.getenv("ECC_EVAL_MIN_ANSWERS_PER_KT")

    if min_acc is not None:
        threshold = float(min_acc)
        assert agg.overall.accuracy >= threshold, (
            f"accuracy {agg.overall.accuracy:.3f} below threshold {threshold:.3f}"
        )
    if min_apkt is not None:
        threshold = float(min_apkt)
        assert agg.answers_per_kilo_token >= threshold, (
            f"answers_per_kilo_token {agg.answers_per_kilo_token:.3f} below threshold {threshold:.3f}"
        )
