"""Unit tests for two-stage skill disclosure.

Covers the SkillManager additions (frontmatter fields, build_listing,
$ARGUMENTS), the model-invoked ``skill`` tool, and the ``/skill-name`` slash
expander. All DB-free — seed skills load from the package ``seed/`` dir
regardless of CWD.
"""
import textwrap
from pathlib import Path

import pytest

from app.core.skills.manager import Skill, SkillManager
from app.harness.skill_tools import build_skill_tool, expand_slash_command


SEEDS = ("log-error-triage", "cloudwatch-alarm-drilldown", "service-restart-checklist")


@pytest.fixture
def manager() -> SkillManager:
    m = SkillManager(skills_dir=Path("data/skills"))
    m.scan_skills()
    return m


def _write_skill(dirpath: Path, name: str, frontmatter: str, body: str = "Body here.") -> None:
    (dirpath / name).mkdir(parents=True, exist_ok=True)
    (dirpath / name / "SKILL.md").write_text(
        f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n\n{body}\n", encoding="utf-8"
    )


# ── frontmatter parsing / backward compat ────────────────────────────────────

def test_seed_skills_load_without_new_fields(manager: SkillManager):
    """Seed skills only declare name/description/config — they still load and
    default the new fields."""
    for name in SEEDS:
        sk = manager.get_skill(name)
        assert sk is not None
        assert sk.when_to_use == ""
        assert sk.disable_model_invocation is False
        assert sk.argument_hint == ""


def test_new_frontmatter_fields_parse(tmp_path: Path):
    _write_skill(
        tmp_path, "fancy",
        """
        name: fancy
        description: A fancy skill.
        when-to-use: when things get fancy
        argument-hint: <target>
        disable-model-invocation: true
        """,
    )
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    sk = m.get_skill("fancy")
    assert sk is not None
    assert sk.when_to_use == "when things get fancy"
    assert sk.argument_hint == "<target>"
    assert sk.disable_model_invocation is True


# ── build_listing ────────────────────────────────────────────────────────────

def test_listing_contains_all_seeds(manager: SkillManager):
    listing = manager.build_listing(char_budget=8000, entry_cap=250)
    assert listing
    for name in SEEDS:
        assert name in listing


def test_listing_scoping(manager: SkillManager):
    listing = manager.build_listing(allowed={"log-error-triage"})
    assert "log-error-triage" in listing
    assert "service-restart-checklist" not in listing


def test_listing_entry_cap(manager: SkillManager):
    listing = manager.build_listing(entry_cap=40)
    for line in listing.splitlines():
        if line.strip():
            assert len(line) <= 40


def test_listing_budget_shrinks(manager: SkillManager):
    full = manager.build_listing(char_budget=100_000, entry_cap=250)
    tiny = manager.build_listing(char_budget=40, entry_cap=250)
    # Under budget pressure the listing degrades (descriptions dropped) so it is
    # strictly shorter, while still naming every seed skill.
    assert len(tiny) < len(full)
    for name in SEEDS:
        assert name in tiny


def test_listing_excludes_disabled(tmp_path: Path):
    _write_skill(tmp_path, "hidden",
                 "name: hidden\ndescription: d\ndisable-model-invocation: true")
    _write_skill(tmp_path, "shown", "name: shown\ndescription: d")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    listing = m.build_listing()
    assert "shown" in listing
    assert "hidden" not in listing


# ── $ARGUMENTS substitution ──────────────────────────────────────────────────

def test_arguments_substituted_inline(manager: SkillManager):
    sk = Skill(name="a", description="d", content="Run: $ARGUMENTS.",
               skill_dir=Path("."), config_vars={})
    msg = manager.build_invocation_message(sk, user_instruction="the-thing")
    assert "Run: the-thing." in msg
    assert "## User Request" not in msg


def test_no_arguments_appends_user_request(manager: SkillManager):
    sk = Skill(name="a", description="d", content="Static body.",
               skill_dir=Path("."), config_vars={})
    msg = manager.build_invocation_message(sk, user_instruction="the-thing")
    assert "## User Request" in msg
    assert "the-thing" in msg


# ── skill tool ───────────────────────────────────────────────────────────────

async def test_skill_tool_happy_path():
    tool = build_skill_tool(allowed_skills=None, invoked_sink=[])
    assert tool.name == "skill"
    out = await tool.ainvoke({"skill": "log-error-triage", "args": ""})
    assert "Loaded skill" in out
    assert "<skill_instructions" in out
    assert "Protocol" in out  # runbook body


async def test_skill_tool_records_sink_and_strips_slash():
    sink: list = []
    tool = build_skill_tool(allowed_skills=None, invoked_sink=sink)
    out = await tool.ainvoke({"skill": "/log-error-triage"})
    assert "Loaded skill" in out
    assert sink == ["log-error-triage"]


async def test_skill_tool_unknown_name():
    tool = build_skill_tool(allowed_skills=None)
    out = await tool.ainvoke({"skill": "nope"})
    assert out.startswith("[skill error]")
    assert "log-error-triage" in out  # lists available


async def test_skill_tool_scoping():
    tool = build_skill_tool(allowed_skills={"cloudwatch-alarm-drilldown"})
    out = await tool.ainvoke({"skill": "log-error-triage"})
    assert out.startswith("[skill error]")
    assert "not available" in out


async def test_skill_tool_body_cap(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "skill_body_inject_chars", 200, raising=False)
    tool = build_skill_tool(allowed_skills=None)
    out = await tool.ainvoke({"skill": "log-error-triage"})
    assert "runbook truncated" in out


async def test_skill_tool_disabled_model_invocation(tmp_path: Path, monkeypatch):
    _write_skill(tmp_path, "useronly",
                 "name: useronly\ndescription: d\ndisable-model-invocation: true")
    from app.core import skills as skills_pkg
    mgr = SkillManager(skills_dir=tmp_path)
    mgr.scan_skills()
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", lambda: mgr)
    tool = build_skill_tool(allowed_skills=None)
    out = await tool.ainvoke({"skill": "useronly"})
    assert out.startswith("[skill error]")
    assert "user-command only" in out


# ── slash expander ───────────────────────────────────────────────────────────

def test_slash_expand_match():
    exp = expand_slash_command("/log-error-triage payments 500s", None)
    assert exp is not None
    query, name = exp
    assert name == "log-error-triage"
    assert "Protocol" in query
    assert "payments 500s" in query


def test_slash_expand_no_args():
    exp = expand_slash_command("/log-error-triage", None)
    assert exp is not None
    assert exp[1] == "log-error-triage"


def test_slash_expand_multiline_args():
    exp = expand_slash_command("/log-error-triage line1\nline2", None)
    assert exp is not None
    assert "line1" in exp[0] and "line2" in exp[0]


def test_slash_expand_unknown_passthrough():
    assert expand_slash_command("/nope do a thing", None) is None


def test_slash_expand_non_slash_passthrough():
    assert expand_slash_command("why are errors spiking?", None) is None


def test_slash_expand_respects_scoping():
    assert expand_slash_command("/log-error-triage x", {"other"}) is None


# ── delete: user skills removed from disk, bundled skills hidden persistently ─

def test_delete_user_skill(tmp_path: Path):
    _write_skill(tmp_path, "gone", "name: gone\ndescription: d")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    assert m.get_skill("gone") is not None
    assert m.delete_skill("gone") is True
    assert m.get_skill("gone") is None
    assert not (tmp_path / "gone").exists()


def test_delete_bundled_skill_hides_persistently(tmp_path: Path):
    # tmp_path is an empty user dir → only bundled seeds are present.
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    assert m.get_skill("log-error-triage") is not None
    # Bundled skill "deletes" (hides) and the marker is written.
    assert m.delete_skill("log-error-triage") is True
    assert m.get_skill("log-error-triage") is None
    assert (tmp_path / ".disabled_skills").exists()
    # Survives a fresh scan (a new manager over the same dir).
    m2 = SkillManager(skills_dir=tmp_path)
    m2.scan_skills()
    assert m2.get_skill("log-error-triage") is None
    assert "log-error-triage" not in m2.build_listing()


def test_recreating_hidden_bundled_skill_re_enables(tmp_path: Path):
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    m.delete_skill("log-error-triage")
    assert m.get_skill("log-error-triage") is None
    # Authoring a skill with the same name clears the hidden marker.
    m.write_skill("log-error-triage", "---\nname: log-error-triage\ndescription: mine\n---\n\nbody")
    assert m.get_skill("log-error-triage") is not None
    m3 = SkillManager(skills_dir=tmp_path)
    m3.scan_skills()
    assert m3.get_skill("log-error-triage") is not None


def test_delete_missing_skill_returns_false(tmp_path: Path):
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    assert m.delete_skill("does-not-exist") is False
