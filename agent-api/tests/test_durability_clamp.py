"""``agent_durability`` defaults to "exit" — the HITL clamp is what makes that safe.

"exit" writes a checkpoint only when the run finishes, which is the right trade
for the common run that never resumes (measured: 6 checkpoint rows -> 1 on a
4-superstep graph). A HITL run is the exception: pause/resume replays from the
interrupt-time checkpoint, and under "exit" that checkpoint is never written.

``run_agent_once`` therefore clamps HITL runs back to "async". That clamp was
dead code while the default was already "async"; flipping the default made it
load-bearing, so it is pinned here. If it regresses, HITL approve/reject breaks
in a way no other test would catch.
"""
import pytest

from app.config import settings


@pytest.fixture
def durability(monkeypatch):
    """Set settings.agent_durability for the duration of a test."""
    def _set(value):
        monkeypatch.setattr(settings, "agent_durability", value, raising=False)
    return _set


@pytest.fixture
def captured_durability(monkeypatch):
    """Run run_agent_once with everything stubbed, returning the durability
    that actually reached execute_agent."""
    seen = {}

    async def fake_execute_agent(*args, **kwargs):
        seen["durability"] = kwargs.get("durability")
        return {"final_answer": "ok", "messages": [], "tool_calls": []}

    import app.harness.agent_runner as agent_runner
    import app.harness as harness

    monkeypatch.setattr(agent_runner, "execute_agent", fake_execute_agent)
    monkeypatch.setattr(harness, "build_agent_from_spec", lambda *a, **k: object())

    async def _run(hitl_enabled):
        from app.harness.engine import run_agent_once

        spec = type("Spec", (), {"agent_config": {"hitl_enabled": hitl_enabled}})()
        await run_agent_once(spec, llm=object(), tools=[], user_query="q")
        return seen.get("durability")

    return _run


@pytest.mark.asyncio
async def test_hitl_run_is_clamped_to_async(durability, captured_durability):
    """The safety property: a HITL run never runs under "exit"."""
    durability("exit")
    assert await captured_durability(hitl_enabled=True) == "async"


@pytest.mark.asyncio
async def test_non_hitl_run_keeps_exit(durability, captured_durability):
    """...and everything else still gets the write reduction."""
    durability("exit")
    assert await captured_durability(hitl_enabled=False) == "exit"


@pytest.mark.asyncio
async def test_clamp_does_not_downgrade_sync(durability, captured_durability):
    """"sync" is a stronger guarantee than "async" — clamping it down would be a
    silent durability regression. Only "exit" is unsafe for HITL."""
    durability("sync")
    assert await captured_durability(hitl_enabled=True) == "sync"


@pytest.mark.asyncio
async def test_explicit_async_is_passed_through(durability, captured_durability):
    durability("async")
    assert await captured_durability(hitl_enabled=True) == "async"
    assert await captured_durability(hitl_enabled=False) == "async"


def test_shipped_default_is_exit():
    """Pins the flip itself — the write reduction is only real if it ships on."""
    from app.config import Settings

    assert Settings.model_fields["agent_durability"].default == "exit"
