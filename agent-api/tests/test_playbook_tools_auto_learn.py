"""The KB writers (``save_playbook`` / ``patch_playbook``) gate on Auto-learn.

They used to bind on every agent, unconditionally, while the Agent node's
Auto-learn toggle only gated the post-run learning pass in the finalizer. So a
workflow with auto-learn OFF still got a system-prompt bullet telling the agent
to call ``save_playbook`` at the end of a run — and since ``save_playbook`` is
the first entry in ``DEFAULT_ASK_PATTERNS``, an unattended scheduled run stalled
for the full 300s approval timeout before finishing.

``pin_fact`` stays bound either way: it belongs to the pinned-facts memory tier
and has its own ``pinned_facts_enabled`` gate.
"""
import pytest

from app.harness.agent_builder import compose_system_prompt
from app.harness.react_agent import _auto_learn_enabled
from app.harness.tool_setup import build_playbook_tools


def _names(tools):
    return {t.name for t in tools}


# ── tool binding ─────────────────────────────────────────────────────────────

def test_writers_bound_when_auto_learn_on():
    assert _names(build_playbook_tools(auto_learn=True)) == {
        "save_playbook", "patch_playbook", "pin_fact",
    }


def test_writers_absent_when_auto_learn_off():
    assert _names(build_playbook_tools(auto_learn=False)) == {"pin_fact"}


def test_default_stays_permissive_for_the_registry_scan():
    """registry_loader enumerates the catalog with no argument — keep all three."""
    assert "save_playbook" in _names(build_playbook_tools())


# ── toggle parsing (shared with spec_factory) ────────────────────────────────

@pytest.mark.parametrize("config, expected", [
    ({}, False),
    ({"autoLearn": True}, True),
    ({"auto_learn": True}, True),
    ({"params": {"autoLearn": True}}, True),
    ({"autoLearn": False}, False),
    # The LangflowEditor toggle persists String(!!on) — 'false' must not be truthy.
    ({"autoLearn": "false"}, False),
    ({"autoLearn": "true"}, True),
])
def test_auto_learn_toggle_parse(config, expected):
    assert _auto_learn_enabled(config) is expected


def test_auto_learn_read_never_raises():
    assert _auto_learn_enabled(None) is False


# ── prompt bullet follows the same gate ──────────────────────────────────────

def test_prompt_names_save_playbook_only_when_enabled():
    on = compose_system_prompt(
        tools=[], agent_config={"instructions": ""}, auto_learn=True)
    off = compose_system_prompt(
        tools=[], agent_config={"instructions": ""}, auto_learn=False)
    assert "save_playbook" in on
    assert "save_playbook" not in off


def test_prompt_default_is_off():
    """An unset toggle must not advertise a tool that will not be bound."""
    prompt = compose_system_prompt(tools=[], agent_config={"instructions": ""})
    assert "save_playbook" not in prompt
