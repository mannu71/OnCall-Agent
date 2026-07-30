"""Unit tests for two-stage skill disclosure.

Stage one is the names-only skill MAP; stage two is ``search_skills`` (intent →
name) then the ``skill`` tool (name → runbook). Covers the SkillManager
additions (frontmatter fields, build_map, the search ranker, $ARGUMENTS), both
model-invoked tools, and the ``/skill-name`` slash expander.

All DB-free and hermetic: the ``manager`` fixture authors its own library into a
tmp ``skills_dir``. Nothing ships with the app (there is no bundled-seed source),
so that dir is the single skill source and these tests never depend on what
happens to be on disk under ``data/skills``.
"""
import textwrap
from pathlib import Path

import pytest

from app.core import skills as skills_pkg
from app.core.skills.manager import Skill, SkillManager
from app.harness.skill_tools import (
    build_skill_search_tool, build_skill_tool, expand_slash_command,
    install_load_guard, reset_load_guard,
)


# The hermetic stand-in cookbook: name → (description, when_to_use).
SEED_SPECS = {
    "log-error-triage": (
        "Triage a spike of errors in a service's application logs.",
        "when errors or exceptions are spiking in the logs",
    ),
    "cloudwatch-alarm-drilldown": (
        "Drill into a firing CloudWatch alarm to find its cause.",
        "when an alarm is firing and needs a root cause",
    ),
    "service-restart-checklist": (
        "Safely restart a service without dropping traffic.",
        "when a service must be recycled or rebooted",
    ),
}
SEEDS = tuple(SEED_SPECS)


def _write_skill(dirpath: Path, name: str, frontmatter: str, body: str = "Body here.") -> None:
    (dirpath / name).mkdir(parents=True, exist_ok=True)
    (dirpath / name / "SKILL.md").write_text(
        f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n\n{body}\n", encoding="utf-8"
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch) -> SkillManager:
    """Manager over a hermetic library authored into the user skills dir.

    Nothing ships with the app (no seeding), so ``skills_dir`` is the single
    source and these fixtures ARE the whole library — no production content can
    leak into assertions. Injected as the process-wide default so the tools
    under test — which resolve through ``get_default_skill_manager`` — see this
    same hermetic set.
    """
    for name, (desc, wtu) in SEED_SPECS.items():
        _write_skill(
            tmp_path, name,
            f"name: {name}\ndescription: {desc}\nwhen-to-use: {wtu}",
            body=f"## Protocol\n\nRunbook body for {name}. $ARGUMENTS",
        )
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", lambda: m)
    return m


# ── frontmatter parsing / backward compat ────────────────────────────────────

def test_skill_loads_without_new_fields(tmp_path: Path):
    """A skill declaring only name/description still loads, defaulting the
    optional disclosure fields."""
    _write_skill(tmp_path, "plain", "name: plain\ndescription: A plain skill.")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    sk = m.get_skill("plain")
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


# ── build_map ────────────────────────────────────────────────────────────────

def test_map_contains_all_seeds(manager: SkillManager):
    skill_map = manager.build_map(char_budget=1500)
    assert skill_map
    for name in SEEDS:
        assert name in skill_map


def test_map_is_names_only(manager: SkillManager):
    """The map's whole purpose: names, no descriptions (search_skills has those)."""
    skill_map = manager.build_map()
    for name in SEEDS:
        desc = manager.get_skill(name).description
        assert desc and desc not in skill_map


def test_map_scoping(manager: SkillManager):
    skill_map = manager.build_map(allowed={"log-error-triage"})
    assert "log-error-triage" in skill_map
    assert "service-restart-checklist" not in skill_map


def test_map_over_budget_degrades_to_count(manager: SkillManager):
    assert manager.build_map(char_budget=5) == f"{len(SEEDS)} skills available"


def test_map_empty_when_no_skills(tmp_path: Path):
    m = SkillManager(skills_dir=tmp_path / "nothing-here")
    m.scan_skills()
    assert m.build_map() == ""


def test_map_excludes_disabled(tmp_path: Path):
    _write_skill(tmp_path, "hidden",
                 "name: hidden\ndescription: d\ndisable-model-invocation: true")
    _write_skill(tmp_path, "shown", "name: shown\ndescription: d")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    skill_map = m.build_map()
    assert "shown" in skill_map
    assert "hidden" not in skill_map


def test_model_invocable_names_filters(tmp_path: Path):
    _write_skill(tmp_path, "hidden",
                 "name: hidden\ndescription: d\ndisable-model-invocation: true")
    _write_skill(tmp_path, "shown", "name: shown\ndescription: d")
    _write_skill(tmp_path, "other", "name: other\ndescription: d")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    assert m.model_invocable_names() == {"shown", "other"}
    assert m.model_invocable_names(allowed={"shown", "hidden"}) == {"shown"}


def test_map_is_alphabetical(manager: SkillManager, tmp_path: Path):
    """With no bundled/user split there is no origin to sort by — the map is
    plain alphabetical so it stays stable between turns."""
    _write_skill(tmp_path, "aaa-first", "name: aaa-first\ndescription: d")
    manager.scan_skills()
    names = [n.strip() for n in manager.build_map().split(",")]
    assert names == sorted(names)
    assert names[0] == "aaa-first"


def test_build_map_touches_no_filesystem(manager: SkillManager, monkeypatch):
    """The map is on the per-turn hot path, so it must be pure-CPU once scanned —
    no Path.resolve/stat per skill (that was the F1 regression)."""
    import pathlib

    def _boom(self, *a, **k):  # any filesystem resolve during build → fail loud
        raise AssertionError("build_map must not touch the filesystem")

    monkeypatch.setattr(pathlib.Path, "resolve", _boom)
    out = manager.build_map()
    for name in SEEDS:
        assert name in out


# ── select_for_query (the search_skills ranker) ───────────────────────────────

def test_select_for_query_ranks_when_to_use_only_match(tmp_path: Path):
    """when_to_use is authored as trigger vocabulary — a query hitting only it
    must still find the skill (its name and description share no words)."""
    _write_skill(tmp_path, "alpha",
                 "name: alpha\ndescription: A procedure.\n"
                 "when-to-use: when the payment gateway rejects transactions",
                 body="Steps.")
    _write_skill(tmp_path, "beta", "name: beta\ndescription: Another procedure.",
                 body="Steps.")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    hits = m.select_for_query("gateway rejects payment", k=5)
    assert [s.name for s in hits] == ["alpha"]


def test_select_for_query_none_vs_empty_allowed(manager: SkillManager):
    """allowed=None is unscoped (search everything); allowed=set() is scoped to
    nothing (empty result) — the two must NOT be conflated, or a scoped agent
    with no invocable skills would see the whole library."""
    unscoped = manager.select_for_query("triage errors in logs", k=5, allowed=None)
    assert any(s.name == "log-error-triage" for s in unscoped)
    empty = manager.select_for_query("triage errors in logs", k=5, allowed=set())
    assert empty == []
    scoped = manager.select_for_query(
        "triage errors in logs", k=5, allowed={"log-error-triage"})
    assert [s.name for s in scoped] == ["log-error-triage"]


def test_body_overlap_capped(tmp_path: Path):
    """A long runbook must not outrank a name/description match on verbosity: the
    body-overlap term is capped, so a short skill whose NAME matches wins over a
    verbose skill that merely mentions the query words in its body."""
    # 'gamma' matches the query only through a long body stuffed with the words.
    stuffed = " ".join(["latency", "timeout", "database", "spike", "error"] * 40)
    _write_skill(tmp_path, "gamma", "name: gamma\ndescription: Unrelated.",
                 body=stuffed)
    # 'latency-timeout' matches by NAME (weight 2.0 each token) — should win.
    _write_skill(tmp_path, "latency-timeout",
                 "name: latency-timeout\ndescription: Handle latency timeouts.",
                 body="Short.")
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    hits = m.select_for_query("latency timeout", k=5)
    assert hits[0].name == "latency-timeout"


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

async def test_skill_tool_happy_path(manager: SkillManager):
    tool = build_skill_tool(allowed_skills=None, invoked_sink=[])
    assert tool.name == "skill"
    out = await tool.ainvoke({"skill": "log-error-triage", "args": ""})
    assert "Loaded skill" in out
    assert "<skill_instructions" in out
    assert "Protocol" in out  # runbook body


async def test_skill_tool_description_points_at_the_map(manager: SkillManager):
    tool = build_skill_tool(allowed_skills=None)
    assert "Skill map" in tool.description
    assert "search_skills" in tool.description


async def test_skill_tool_records_sink_and_strips_slash(manager: SkillManager):
    sink: list = []
    tool = build_skill_tool(allowed_skills=None, invoked_sink=sink)
    out = await tool.ainvoke({"skill": "/log-error-triage"})
    assert "Loaded skill" in out
    assert sink == ["log-error-triage"]


async def test_skill_tool_unknown_name(manager: SkillManager):
    tool = build_skill_tool(allowed_skills=None)
    out = await tool.ainvoke({"skill": "nope"})
    assert out.startswith("[skill error]")
    assert "log-error-triage" in out  # lists available


async def test_skill_tool_scoping(manager: SkillManager):
    tool = build_skill_tool(allowed_skills={"cloudwatch-alarm-drilldown"})
    out = await tool.ainvoke({"skill": "log-error-triage"})
    assert out.startswith("[skill error]")
    assert "not available" in out


async def test_skill_tool_body_cap(manager: SkillManager, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "skill_body_inject_chars", 20, raising=False)
    tool = build_skill_tool(allowed_skills=None)
    out = await tool.ainvoke({"skill": "log-error-triage"})
    # A too-long runbook is windowed, not silently cut on disk: the notice must
    # point at the recoverable next-part call, not "continues on disk".
    assert "part 1/" in out
    assert 'part=2' in out
    assert "on disk" not in out


async def test_skill_tool_part_continuation(manager: SkillManager, monkeypatch):
    """part=N returns later windows of a long runbook; out-of-range is an error."""
    from app.config import settings
    monkeypatch.setattr(settings, "skill_body_inject_chars", 20, raising=False)
    tool = build_skill_tool(allowed_skills=None)
    p1 = await tool.ainvoke({"skill": "log-error-triage", "part": 1})
    p2 = await tool.ainvoke({"skill": "log-error-triage", "part": 2})
    # Distinct windows, and a continuation is not stubbed by the re-invoke guard.
    assert "<skill_instructions" in p2
    assert "already loaded" not in p2
    assert "Continuation" in p2
    # Out-of-range part → clear error, never a raise.
    huge = await tool.ainvoke({"skill": "log-error-triage", "part": 999})
    assert huge.startswith("[skill error]") and "does not exist" in huge


async def test_skill_tool_reinvocation_guard_is_per_instance(manager: SkillManager):
    """With no context guard installed, the guard is per tool INSTANCE — a load
    on one instance must not stub a load on another sharing the same sink. This
    is the decoupling of the guard from the (shared) badge sink."""
    sink: list = []
    a = build_skill_tool(allowed_skills=None, invoked_sink=sink)
    b = build_skill_tool(allowed_skills=None, invoked_sink=sink)
    out_a = await a.ainvoke({"skill": "log-error-triage"})
    assert "<skill_instructions" in out_a
    # b shares the sink (so the badge already lists the skill) but has its own
    # guard — it must still emit the full runbook, not the "already loaded" stub.
    out_b = await b.ainvoke({"skill": "log-error-triage"})
    assert "<skill_instructions" in out_b
    assert "already loaded" not in out_b
    # Re-loading on the SAME instance is stubbed.
    out_a2 = await a.ainvoke({"skill": "log-error-triage"})
    assert "already loaded in this context" in out_a2


async def test_skill_tool_guard_isolated_across_contexts(manager: SkillManager):
    """The SAME tool object (as a subagent shares the parent's) must not leak its
    load state across execution contexts: a load under one installed guard does
    not stub a load under a freshly installed guard."""
    tool = build_skill_tool(allowed_skills=None, invoked_sink=[])

    tok_child = install_load_guard()
    try:
        child_out = await tool.ainvoke({"skill": "log-error-triage"})
        assert "<skill_instructions" in child_out
    finally:
        reset_load_guard(tok_child)

    # A new context (e.g. the parent, after the child returned) — same tool
    # object — must get the full runbook, not the stub.
    tok_parent = install_load_guard()
    try:
        parent_out = await tool.ainvoke({"skill": "log-error-triage"})
        assert "<skill_instructions" in parent_out
        assert "already loaded" not in parent_out
        # But a second load within the SAME parent context is stubbed.
        parent_out2 = await tool.ainvoke({"skill": "log-error-triage"})
        assert "already loaded in this context" in parent_out2
    finally:
        reset_load_guard(tok_parent)


async def test_skill_tool_guard_seeded_by_preloaded(manager: SkillManager):
    """install_load_guard(preloaded) seeds the guard so a /slash-preloaded skill
    (runbook already in the turn) is stubbed if the model also calls it."""
    tool = build_skill_tool(allowed_skills=None, invoked_sink=[])
    tok = install_load_guard(["log-error-triage"])
    try:
        out = await tool.ainvoke({"skill": "log-error-triage"})
        assert "already loaded in this context" in out
    finally:
        reset_load_guard(tok)


async def test_skill_tool_disabled_model_invocation(tmp_path: Path, monkeypatch):
    _write_skill(tmp_path, "useronly",
                 "name: useronly\ndescription: d\ndisable-model-invocation: true")
    mgr = SkillManager(skills_dir=tmp_path)
    mgr.scan_skills()
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", lambda: mgr)
    tool = build_skill_tool(allowed_skills=None)
    out = await tool.ainvoke({"skill": "useronly"})
    assert out.startswith("[skill error]")
    assert "user-command only" in out


# ── search_skills tool ───────────────────────────────────────────────────────

async def test_search_skills_happy_path(manager: SkillManager):
    tool = build_skill_search_tool(allowed_skills=None)
    assert tool.name == "search_skills"
    out = await tool.ainvoke({"query": "errors spiking in the logs"})
    assert "log-error-triage" in out
    # Results carry what the map deliberately omits, plus the next step.
    assert "Triage a spike of errors" in out
    assert "when: when errors or exceptions are spiking in the logs" in out
    assert 'skill(skill="<name>")' in out


async def test_search_skills_description_points_at_the_map(manager: SkillManager):
    tool = build_skill_search_tool(allowed_skills=None)
    assert "Skill map" in tool.description


async def test_search_skills_scoping(manager: SkillManager):
    tool = build_skill_search_tool(allowed_skills={"cloudwatch-alarm-drilldown"})
    out = await tool.ainvoke({"query": "errors spiking in the logs"})
    assert "log-error-triage" not in out


async def test_search_skills_never_returns_disabled(tmp_path: Path, monkeypatch):
    _write_skill(tmp_path, "useronly",
                 "name: useronly\ndescription: Triage errors in logs.\n"
                 "disable-model-invocation: true")
    mgr = SkillManager(skills_dir=tmp_path)
    mgr.scan_skills()
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", lambda: mgr)
    tool = build_skill_search_tool(allowed_skills=None)
    out = await tool.ainvoke({"query": "triage errors in logs"})
    assert "useronly" not in out


async def test_search_skills_no_match_falls_back_to_names(manager: SkillManager):
    tool = build_skill_search_tool(allowed_skills=None)
    out = await tool.ainvoke({"query": "zzz unrelated capability"})
    assert "No skills matched" in out
    assert "log-error-triage" in out  # names the library it could not match
    assert "proceed without a skill" in out


async def test_search_skills_no_match_caps_the_name_list(tmp_path: Path, monkeypatch):
    """A large library must not flood the no-match fallback with every name —
    it caps at 25 and tallies the rest as '(+K more)'."""
    for i in range(40):
        _write_skill(tmp_path, f"skill-{i:02d}", f"name: skill-{i:02d}\ndescription: alpha")
    mgr = SkillManager(skills_dir=tmp_path)
    mgr.scan_skills()
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", lambda: mgr)
    out = await build_skill_search_tool(allowed_skills=None).ainvoke(
        {"query": "zzzznomatch qqqqunrelated"})
    assert "No skills matched" in out
    assert "(+15 more)" in out  # 40 skills − 25 shown
    assert out.count("skill-") == 25  # only the capped set is listed


@pytest.mark.parametrize("limit,expected", [(0, 1), (1, 1), (99, 3)])
async def test_search_skills_clamps_limit(manager: SkillManager, limit: int, expected: int):
    """limit is clamped to 1..10 — a 0 or a huge value must not blow up the result."""
    tool = build_skill_search_tool(allowed_skills=None)
    out = await tool.ainvoke({"query": "errors logs alarm restart service", "limit": limit})
    assert out.startswith(f"{expected} matching skill(s)")


async def test_search_skills_never_raises(manager: SkillManager, monkeypatch):
    """A broken manager surfaces as a string to the model, not an exception that
    kills the agent loop."""
    def _boom():
        raise RuntimeError("skills unavailable")
    monkeypatch.setattr(skills_pkg, "get_default_skill_manager", _boom)
    out = await build_skill_search_tool(allowed_skills=None).ainvoke({"query": "anything"})
    assert out.startswith("[search_skills error]")
    assert "skills unavailable" in out


# ── slash expander ───────────────────────────────────────────────────────────

def test_slash_expand_match(manager: SkillManager):
    exp = expand_slash_command("/log-error-triage payments 500s", None)
    assert exp is not None
    query, name = exp
    assert name == "log-error-triage"
    assert "Protocol" in query
    assert "payments 500s" in query


def test_slash_expand_no_args(manager: SkillManager):
    exp = expand_slash_command("/log-error-triage", None)
    assert exp is not None
    assert exp[1] == "log-error-triage"


def test_slash_expand_multiline_args(manager: SkillManager):
    exp = expand_slash_command("/log-error-triage line1\nline2", None)
    assert exp is not None
    assert "line1" in exp[0] and "line2" in exp[0]


def test_slash_expand_unknown_passthrough(manager: SkillManager):
    assert expand_slash_command("/nope do a thing", None) is None


def test_slash_expand_non_slash_passthrough(manager: SkillManager):
    assert expand_slash_command("why are errors spiking?", None) is None


def test_slash_expand_respects_scoping(manager: SkillManager):
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


def test_delete_removes_from_disk_and_survives_rescan(manager: SkillManager, tmp_path: Path):
    """Every skill is user-authored in a writable dir, so delete removes the
    directory outright — there is no packaged/read-only case to hide around
    (the old bundled-seed 'hide via .disabled_skills' path is gone)."""
    assert manager.get_skill("log-error-triage") is not None
    assert manager.delete_skill("log-error-triage") is True
    assert manager.get_skill("log-error-triage") is None
    assert not (tmp_path / "log-error-triage").exists()
    assert not (tmp_path / ".disabled_skills").exists()  # no marker file anymore
    # Stays gone for a fresh manager over the same dir.
    m2 = SkillManager(skills_dir=tmp_path)
    m2.scan_skills()
    assert m2.get_skill("log-error-triage") is None
    assert "log-error-triage" not in m2.build_map()


def test_recreating_a_deleted_skill_restores_it(manager: SkillManager, tmp_path: Path):
    manager.delete_skill("log-error-triage")
    assert manager.get_skill("log-error-triage") is None
    manager.write_skill(
        "log-error-triage",
        "---\nname: log-error-triage\ndescription: My own triage runbook.\n---\n\nbody")
    assert manager.get_skill("log-error-triage") is not None
    m3 = SkillManager(skills_dir=tmp_path)
    m3.scan_skills()
    assert m3.get_skill("log-error-triage") is not None


def test_delete_missing_skill_returns_false(tmp_path: Path):
    m = SkillManager(skills_dir=tmp_path)
    m.scan_skills()
    assert m.delete_skill("does-not-exist") is False


def test_rename_via_edit_drops_stale_entry(tmp_path: Path):
    """Editing a skill's frontmatter `name` rewrites the SAME directory but loads
    under a new name — the old cache key must not linger as a ghost pointing at
    the rewritten file (it would double-list in the map until a full rescan)."""
    m = SkillManager(skills_dir=tmp_path)
    m.write_skill("orig", "---\nname: orig\ndescription: The original runbook.\n---\n\nBody.")
    assert m.get_skill("orig") is not None
    # Same slug/dir ("orig"), but the frontmatter name changes to "renamed".
    m.write_skill("orig", "---\nname: renamed\ndescription: The renamed runbook.\n---\n\nBody.")
    assert m.get_skill("renamed") is not None
    assert m.get_skill("orig") is None
    names = m.build_map()
    assert "renamed" in names and "orig" not in names


async def test_search_skills_limit_defaults_to_configured_k(manager: SkillManager, monkeypatch):
    """The default result count is operator-configurable (SKILL_SEARCH_K), not
    a magic number baked into the tool."""
    from app.config import settings
    monkeypatch.setattr(settings, "skill_search_k", 1, raising=False)
    tool = build_skill_search_tool(allowed_skills=None)
    out = await tool.ainvoke({"query": "errors logs alarm restart service"})
    assert out.startswith("1 matching skill(s)")


async def test_search_skills_advertised_default_is_clamped(manager: SkillManager, monkeypatch):
    """A configured SKILL_SEARCH_K above the 1..10 handler clamp must not be
    advertised in the schema — the default the model sees is the one it can get."""
    from app.config import settings
    monkeypatch.setattr(settings, "skill_search_k", 20, raising=False)
    tool = build_skill_search_tool(allowed_skills=None)
    assert tool.args_schema.model_fields["limit"].default == 10
    assert "default 10" in tool.args_schema.model_fields["limit"].description
