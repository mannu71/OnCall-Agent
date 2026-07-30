"""Regression cover for the shared token estimator.

``app.core.llm.token_estimate`` now backs every soft budget in the app
(compaction thresholds, chat replay, injected context, pinned facts, tool-schema
sizing). Two properties matter and are easy to break:

1. With calibration off — the shipped default — it must be byte-identical to the
   raw ``len(text) // 4`` heuristic every call site used before, so adopting it
   moved no threshold.
2. With calibration on, the learned factor must actually reach the estimate.
   Before this refactor only history compaction applied it; every other budget
   silently ignored it.
"""
import pytest

from app.config import settings
from app.core.llm import token_calibration
from app.core.llm.token_estimate import (
    count_tokens_exact,
    estimate_messages_tokens,
    estimate_request_tokens,
    estimate_tokens,
    get_encoder,
)

SAMPLES = [
    "",
    "a",
    "abc",
    "x" * 400,
    '{"key": "value"}' * 50,
    "hello world " * 33,
]


@pytest.fixture
def calibration_on():
    """Enable calibration and guarantee a clean slate either side of the test."""
    token_calibration.reset()
    original = getattr(settings, "token_estimate_calibration_enabled", False)
    settings.token_estimate_calibration_enabled = True
    try:
        yield
    finally:
        settings.token_estimate_calibration_enabled = original
        token_calibration.reset()


@pytest.mark.parametrize("text", SAMPLES)
def test_default_config_matches_legacy_chars4(text):
    """Calibration off => exactly the old arithmetic, including 0 for empty."""
    expected = max(1, len(text) // 4) if text else 0
    assert estimate_tokens(text) == expected


def test_compaction_helper_delegates():
    """compaction._estimate_tokens is an alias, not a second implementation."""
    from app.core.context.compaction import _estimate_tokens

    for text in SAMPLES:
        assert _estimate_tokens(text) == estimate_tokens(text)


def test_empty_and_none_inputs_are_zero():
    assert estimate_tokens("") == 0
    assert estimate_messages_tokens(None) == 0
    assert estimate_messages_tokens([]) == 0
    assert estimate_request_tokens(None) == 0


def test_request_estimate_counts_tools_and_system_prompt():
    """Tool schemas are a real cost — 20-30K tokens with many tools bound."""
    messages = [{"role": "user", "content": "hi"}]
    bare = estimate_request_tokens(messages)
    with_system = estimate_request_tokens(messages, system_prompt="s" * 400)
    with_tools = estimate_request_tokens(messages, tools=[{"name": "t" * 400}])

    assert with_system > bare
    assert with_tools > bare


def test_calibration_factor_reaches_the_estimate(calibration_on):
    """The whole point of the refactor: the learned factor is not ignored."""
    token_calibration.record("model-a", estimated_tokens=100, actual_tokens=130)
    assert token_calibration.factor_for("model-a") == pytest.approx(1.3)

    base = 400 // 4
    assert estimate_tokens("x" * 400, model="model-a") == pytest.approx(base * 1.3, abs=1)


def test_calibration_is_per_model(calibration_on):
    """An unseen model must not inherit another model's correction."""
    token_calibration.record("model-a", estimated_tokens=100, actual_tokens=130)

    base = 400 // 4
    assert estimate_tokens("x" * 400, model="model-b") == base


def test_calibration_factor_is_clamped(calibration_on):
    """One noisy sample must not wildly distort every later estimate."""
    token_calibration.record("wild", estimated_tokens=1, actual_tokens=10_000)
    assert token_calibration.factor_for("wild") <= 1.6

    token_calibration.reset()
    token_calibration.record("tiny", estimated_tokens=10_000, actual_tokens=1)
    assert token_calibration.factor_for("tiny") >= 0.7


def test_exact_count_is_not_calibration_scaled(calibration_on):
    """count_tokens_exact is a real tokenization; the chars/4 factor is
    meaningless applied to it and must not be."""
    token_calibration.record("model-a", estimated_tokens=100, actual_tokens=130)

    text = "ERROR Null reference at Foo.Bar(line 42)"
    assert count_tokens_exact(text) == count_tokens_exact(text)

    enc = get_encoder()
    if enc is not None:
        assert count_tokens_exact(text) == len(enc.encode(text))


def test_exact_count_falls_back_without_tiktoken(monkeypatch):
    """Minimal environments without tiktoken must still get a usable number."""
    import app.core.llm.token_estimate as te

    monkeypatch.setattr(te, "_ENCODER", False)
    text = "x" * 400
    assert te.count_tokens_exact(text) == 100
    assert te.count_tokens_exact("") == 0


# ── calibration must not thrash the compaction estimate memo ────────────────
# record() runs once per model turn. It used to clear the whole per-message
# estimate memo unconditionally, which meant every turn re-walked the entire
# transcript — exactly the O(N^2) the memo was added to remove. It may only
# invalidate when the factor it feeds actually moved.

def _warm_memo(n=25):
    from langchain_core.messages import HumanMessage
    from app.core.context import compaction

    compaction.reset_estimate_cache()
    msgs = [HumanMessage(content="x" * 400, id=f"m{i}") for i in range(n)]
    for m in msgs:
        compaction._msg_token_estimate(m)
    return msgs


def test_converged_calibration_keeps_the_memo(calibration_on):
    from app.core.context import compaction

    token_calibration.record("m", 100, 130)   # first observation moves the factor
    msgs = _warm_memo()
    assert len(compaction._ESTIMATE_CACHE) == len(msgs)

    for _ in range(20):
        token_calibration.record("m", 100, 130)   # same ratio → already converged
        assert len(compaction._ESTIMATE_CACHE) == len(msgs)


def test_real_drift_still_invalidates_the_memo(calibration_on):
    from app.core.context import compaction

    token_calibration.record("m", 100, 130)
    _warm_memo()
    assert compaction._ESTIMATE_CACHE

    token_calibration.record("m", 100, 60)    # ratio 0.6 — a genuine shift
    assert not compaction._ESTIMATE_CACHE


def test_switching_model_invalidates_the_memo(calibration_on):
    """current_factor() follows _last_model, so a model switch changes every
    estimate even when both models' factors are identical."""
    from app.core.context import compaction

    token_calibration.record("model-a", 100, 130)
    _warm_memo()
    assert compaction._ESTIMATE_CACHE

    token_calibration.record("model-b", 100, 130)
    assert not compaction._ESTIMATE_CACHE
