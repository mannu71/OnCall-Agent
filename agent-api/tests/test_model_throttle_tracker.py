"""Unit tests for the process-local LLM throttle tracker (deterministic clock)."""
import pytest

from app.core.llm import model_throttle_tracker as throttle
from app.core.llm.model_throttle_tracker import ThrottleTarget


@pytest.fixture
def clock():
    """Install a fake monotonic clock; restore real time + clear state after."""
    state = {"t": 1000.0}
    throttle._set_time_fn(lambda: state["t"])
    throttle.reset()
    yield state
    throttle.reset()
    import time as _time
    throttle._set_time_fn(_time.monotonic)


T = ThrottleTarget(provider="bedrock", region="us-east-1",
                   model_id="anthropic.claude-sonnet-4-6", key_id="default")


def test_unknown_target_is_not_cooled(clock):
    assert throttle.is_cooled(T) is False
    assert throttle.cooldown_remaining(T) == 0.0


def test_mark_throttled_starts_cooldown(clock):
    cd = throttle.mark_throttled(T)
    assert cd > 0.0
    assert throttle.is_cooled(T) is True
    assert throttle.cooldown_remaining(T) > 0.0


def test_cooldown_elapses_and_clears(clock):
    throttle.mark_throttled(T)
    # Advance well past the 30s cap.
    clock["t"] += 100.0
    assert throttle.is_cooled(T) is False
    # Reading after expiry clears the entry (fresh start next throttle).
    assert throttle.cooldown_remaining(T) == 0.0


def test_consecutive_throttles_widen_cooldown(clock):
    # First throttle: decorrelated jitter attempt=1 → within [1, 3].
    cd1 = throttle.mark_throttled(T)
    assert 1.0 <= cd1 <= 3.0
    # Second consecutive throttle uses a higher attempt → can exceed cd1's
    # ceiling; assert it is still bounded by the 30s cap.
    cd2 = throttle.mark_throttled(T)
    assert cd2 <= 30.0


def test_distinct_targets_are_independent(clock):
    other = ThrottleTarget(provider="bedrock", region="us-west-2",
                           model_id="anthropic.claude-sonnet-4-6", key_id="default")
    throttle.mark_throttled(T)
    assert throttle.is_cooled(T) is True
    assert throttle.is_cooled(other) is False


def test_target_key_is_case_insensitive(clock):
    upper = ThrottleTarget(provider="Bedrock", region="US-EAST-1",
                           model_id="Anthropic.Claude-Sonnet-4-6", key_id="default")
    throttle.mark_throttled(T)
    # Same logical target despite different casing.
    assert throttle.is_cooled(upper) is True


def test_clear_specific_target(clock):
    other = ThrottleTarget(provider="bedrock", region="us-west-2",
                           model_id="m", key_id="default")
    throttle.mark_throttled(T)
    throttle.mark_throttled(other)
    throttle._tracker.clear(T)
    assert throttle.is_cooled(T) is False
    assert throttle.is_cooled(other) is True
