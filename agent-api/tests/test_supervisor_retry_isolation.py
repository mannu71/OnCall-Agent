"""A supervisor RETRY must not resume the attempt it just rejected.

``make_checkpointer`` ALWAYS returns a checkpointer, so re-running a LangGraph
thread RESUMES its persisted state and ``add_messages`` APPENDS. Reusing
``execution_id`` as the thread id across attempts therefore fed the rejected
answer back as context for the retry, and compounded input tokens, the saved
trajectory and the accumulated token totals.

Regression: attempt 2's first model call saw 6 messages instead of 2, and the
run returned 8 result messages instead of 4.
"""
from __future__ import annotations

import pytest

from app.workflow.strategies.react import executor as react_executor
from app.workflow.strategies.react.executor import attempt_thread_id


# ── the pure derivation ──────────────────────────────────────────────────────

def test_first_attempt_keeps_the_bare_execution_id():
    """Unchanged traces/checkpoint rows for the common single-attempt run."""
    assert attempt_thread_id("exec-1", 1) == "exec-1"


def test_each_retry_gets_its_own_thread():
    ids = [attempt_thread_id("exec-1", n) for n in (1, 2, 3, 4)]
    assert len(set(ids)) == len(ids), f"thread ids collide across attempts: {ids}"
    assert ids[1:] == ["exec-1:retry1", "exec-1:retry2", "exec-1:retry3"]


@pytest.mark.parametrize("missing", [None, ""])
def test_unthreaded_run_stays_unthreaded(missing):
    """No execution id -> no thread to key on, exactly as before."""
    assert attempt_thread_id(missing, 1) == missing
    assert attempt_thread_id(missing, 3) == missing


# ── the wiring: run_plan -> run_supervised -> _run_agent ─────────────────────

class _Verdict:
    def __init__(self, action, score=0.1):
        self.action = action
        self.score = score
        self.reason = "test"
        self.retry_guidance = "GUIDANCE: cite evidence."
        self.score_breakdown = None


class _Cfg:
    max_retries = 1
    token_budget = 0
    llm_scoring_enabled = False


class _RetryOnceSupervisor:
    """Forces exactly one RETRY, then passes."""

    def __init__(self, *_a, **_kw):
        self._cfg = _Cfg()
        self._calls = 0

    async def evaluate(self, **_kw):
        from app.core.quality.supervisor import SupervisorAction

        self._calls += 1
        return _Verdict(
            SupervisorAction.RETRY if self._calls == 1 else SupervisorAction.PASS
        )


@pytest.mark.asyncio
async def test_retry_runs_on_a_fresh_thread(monkeypatch):
    from app.harness.spec import AgentSpec

    seen_thread_ids = []
    seen_queries = []

    async def _fake_run_agent_once(spec, llm, tools, query, **kwargs):
        seen_thread_ids.append(kwargs.get("thread_id"))
        seen_queries.append(query)
        return {
            "final_answer": "weak answer",
            "messages": [],
            "tool_calls": [],
            "input_tokens": 10,
            "output_tokens": 5,
        }

    async def _no_alt_creds(_cfg):
        return []

    monkeypatch.setattr(react_executor, "run_agent_once", _fake_run_agent_once)
    monkeypatch.setattr(react_executor, "gather_alt_credentials", _no_alt_creds)
    monkeypatch.setattr(
        react_executor, "resolve_llm_fallback_chain",
        lambda cfg, alt_credentials=None: [cfg],
    )
    monkeypatch.setattr(
        react_executor, "InvestigationSupervisor", _RetryOnceSupervisor,
    )

    plan = react_executor.RunPlan(
        user_query="Why is it failing?",
        augmented_query="Why is it failing?",
        agent_config={"instructions": "x"},
        llm_config={"model": "m"},
        llm=object(),
        tools=[],
        spec=AgentSpec(agent_config={"instructions": "x"}),
        checkpointer=None,
        cw_synthesis="",
        recall_hits=0,
        code_analyzer_config={},
    )

    import logging

    await react_executor.run_plan(
        plan,
        context={},
        execution_id="exec-RETRY",
        logger_instance=logging.getLogger("test"),
        stream_callback=None,
        execution_port=None,
    )

    assert len(seen_thread_ids) == 2, (
        f"expected one retry, got {len(seen_thread_ids)} attempt(s)"
    )
    assert seen_thread_ids[0] == "exec-RETRY"
    assert seen_thread_ids[1] != seen_thread_ids[0], (
        "retry reused the rejected attempt's thread -> its transcript is resumed"
    )
    # The corrective guidance is what the retry is for.
    assert "GUIDANCE" in seen_queries[1]
