"""Unit tests for the TOOL map — the names of the deferred tools, carried in the
``search_tools`` description.

When progressive disclosure defers a large MCP tool set, the model can no longer
see those tools' schemas. The map names them so it still knows the capability
exists and what vocabulary to search with; the schemas stay deferred (they are
what cost tokens). It rides the cached prompt prefix, so it is paid once per run.
"""
import pytest

from app.harness.tool_disclosure import build_disclosure_tools


class _FakeTool:
    """Minimal stand-in for a bound MCP tool (name + description + ainvoke)."""

    def __init__(self, name: str, description: str = "does a thing"):
        self.name = name
        self.description = description
        self.calls: list = []

    async def ainvoke(self, arguments: dict) -> str:
        self.calls.append(arguments)
        return f"{self.name} ran with {arguments}"


def _tools(n: int) -> list:
    return [_FakeTool(f"wit_get_work_item_{i}", f"work item op {i}") for i in range(n)]


def _search_desc(deferred: list) -> str:
    search, _call = build_disclosure_tools(deferred)
    assert search.name == "search_tools"
    return search.description


def test_map_names_every_deferred_tool():
    deferred = _tools(5)
    desc = _search_desc(deferred)
    assert "Tool map (deferred, searchable):" in desc
    for t in deferred:
        assert t.name in desc


def test_map_names_are_sorted():
    deferred = [_FakeTool("zeta_tool"), _FakeTool("alpha_tool"), _FakeTool("mid_tool")]
    desc = _search_desc(deferred)
    assert desc.index("alpha_tool") < desc.index("mid_tool") < desc.index("zeta_tool")


def test_map_truncates_over_budget_with_more_hint(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "tool_map_char_budget", 60, raising=False)
    deferred = _tools(20)
    desc = _search_desc(deferred)
    assert "more — search to find them]" in desc
    # The kept names fit the budget; the rest are accounted for in the +K tail.
    kept = sum(1 for t in deferred if t.name in desc)
    assert 0 < kept < len(deferred)
    assert f"[+{len(deferred) - kept} more" in desc


def test_map_degrades_to_count_when_no_name_fits(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "tool_map_char_budget", 1, raising=False)
    desc = _search_desc(_tools(7))
    assert "7 tools — search to find them." in desc


def test_map_absent_for_unnamed_tools():
    """Nothing to map (no usable names) → the description is left alone."""
    desc = _search_desc([_FakeTool("")])
    assert "Tool map" not in desc


async def test_call_tool_still_dispatches_through_the_tool():
    """The map is a description-only change — call_tool's behaviour is untouched."""
    target = _FakeTool("wit_get_work_item")
    _search, call_tool = build_disclosure_tools([target])
    out = await call_tool.ainvoke({"tool_name": "wit_get_work_item", "arguments": {"id": 877}})
    assert "wit_get_work_item ran with" in out
    assert target.calls == [{"id": 877}]


async def test_call_tool_unknown_name_still_hints():
    _search, call_tool = build_disclosure_tools([_FakeTool("wit_get_work_item")])
    out = await call_tool.ainvoke({"tool_name": "get_work_item", "arguments": {}})
    assert out.startswith("[call_tool error]")
    assert "wit_get_work_item" in out
