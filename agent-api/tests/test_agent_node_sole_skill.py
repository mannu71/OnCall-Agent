"""The Agent node's "no instructions, one picked skill" fallback.

A scheduled/manual run carries no chat message, so the handler's ``user_query``
chain resolves empty and the node was skipped outright — the agent never
started, so nothing was ever in a position to reach for a skill (selection is
query-driven: ``select_for_query`` has nothing to rank an empty query against).
A node with exactly ONE skill on its picker is unambiguous, so the handler now
synthesises ``/<skill-name>`` and lets ReactStrategy's existing slash expansion
run it. Two or more picked skills stay ambiguous and still skip.

Hermetic: authors its own cookbook in a tmp dir and injects it as the
process-wide skill manager, so nothing depends on what is on disk under
``data/knowledge/skills``.
"""
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import skills as skills_pkg
from app.core.skills.manager import SkillManager
from app.workflow.executor.handlers.agent import _sole_picked_skill, execute


def _write_skill(dirpath: Path, name: str) -> None:
    (dirpath / name).mkdir(parents=True, exist_ok=True)
    (dirpath / name / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: Runbook for {name}.
            when_to_use: when asked for {name}
            ---

            ## Protocol

            Body for {name}. $ARGUMENTS
            """
        ),
        encoding="utf-8",
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch) -> SkillManager:
    """Hermetic manager holding one real skill, injected as the default.

    Nothing ships with the app, so ``skills_dir`` is the only source — this
    tmp dir IS the entire library.
    """
    _write_skill(tmp_path, "recharge-daily-report")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", lambda: m)
    return m


def _node(**config) -> dict:
    return {"id": "agent-1", "type": "agent", "data": {}, "params": config}


def _executor() -> SimpleNamespace:
    """Minimal stand-in for the WorkflowExecutor the handler reads off."""
    return SimpleNamespace(
        active_executions={}, mcp_managers={}, _publish_event=lambda *a, **k: None,
    )


# ── _sole_picked_skill: the picker read ──────────────────────────────────────

def test_single_picked_skill_in_params_dialect():
    assert _sole_picked_skill(_node(skills=["recharge-daily-report"])) == (
        "recharge-daily-report"
    )


def test_single_picked_skill_in_legacy_data_dialect():
    node = {"id": "a", "type": "agent", "data": {"skills": ["only-one"]}}
    assert _sole_picked_skill(node) == "only-one"


def test_csv_string_picker_value_is_coerced():
    """The UI can persist a multi-select as a comma-separated string."""
    assert _sole_picked_skill(_node(skills="one-skill")) == "one-skill"
    assert _sole_picked_skill(_node(skills="a,b")) == ""


def test_two_picked_skills_stay_ambiguous():
    assert _sole_picked_skill(_node(skills=["a", "b"])) == ""


def test_no_picked_skills():
    assert _sole_picked_skill(_node()) == ""
    assert _sole_picked_skill(_node(skills=[])) == ""


# ── the handler's skip paths ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_instructions_and_no_skills_still_skips(manager):
    """Regression guard: the original behaviour is untouched when nothing is picked."""
    result = await execute(_executor(), _node(), {"execution_id": "e1", "inputs": {}})
    assert result["status"] == "skipped"
    assert result["output"] == "Agent node has no user_query or instructions configured."


@pytest.mark.asyncio
async def test_unresolvable_picked_skill_skips_and_names_it(manager):
    """A picked skill the manager cannot resolve must not become a literal query.

    Sending the raw '/typo-skill' text through as the prompt would produce a
    confident, unrelated answer — the skip plus the skill name is the honest
    outcome, and points at the usual cause (an un-rescanned SKILL.md).
    """
    result = await execute(
        _executor(), _node(skills=["typo-skill"]), {"execution_id": "e1", "inputs": {}}
    )
    assert result["status"] == "skipped"
    assert "typo-skill" in result["output"]


@pytest.mark.asyncio
async def test_sole_skill_becomes_the_slash_command(manager, monkeypatch):
    """The happy path: the run proceeds with '/<skill>' as its query."""
    seen = {}

    class _FakeStrategy:
        async def execute(self, workflow, strategy_context):
            seen["user_query"] = strategy_context["user_query"]
            return {"final_answer": "done"}

    import app.workflow.strategies.react as react_mod
    monkeypatch.setattr(react_mod, "ReactStrategy", _FakeStrategy)

    executor = _executor()
    result = await execute(
        executor,
        _node(skills=["recharge-daily-report"]),
        {"execution_id": "e1", "inputs": {}},
    )

    assert seen["user_query"] == "/recharge-daily-report"
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_explicit_instructions_win_over_the_picker(manager, monkeypatch):
    """The fallback must never override a configured instruction."""
    seen = {}

    class _FakeStrategy:
        async def execute(self, workflow, strategy_context):
            seen["user_query"] = strategy_context["user_query"]
            return {"final_answer": "done"}

    import app.workflow.strategies.react as react_mod
    monkeypatch.setattr(react_mod, "ReactStrategy", _FakeStrategy)

    executor = _executor()
    await execute(
        executor,
        _node(skills=["recharge-daily-report"], instructions="Investigate the API errors."),
        {"execution_id": "e1", "inputs": {}},
    )

    assert seen["user_query"] == "Investigate the API errors."
