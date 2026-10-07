from datetime import datetime, timezone

from oncall_ui import api as api_mod
from oncall_ui.results import (
    clean_llm_text, extract_final_answer, extract_node_field, extract_privacy_redactions, extract_tokens,
)
from oncall_ui.schedules import apply_schedule, from_api, target_nodes
from oncall_ui.sse import iter_sse
from oncall_ui.timeutils import (
    cron_to_local_time, local_time_to_cron, next_occurrence, parse_date, relative_time,
    run_started_compact, scheduled_relative,
)


# ── time / cron ───────────────────────────────────────────────────────────
def test_parse_date_variants():
    assert parse_date("2026-02-17T12:48:21.351271+00:00Z").tzinfo is not None
    assert parse_date("2026-02-17 12:48:21").hour == 12
    assert parse_date("nope") is None and parse_date(None) is None


def test_cron_round_trip_in_offset_zone():
    cron = local_time_to_cron("10:30", "daily", "Asia/Kolkata", on=datetime(2024, 1, 15).date())
    assert cron == "0 5 * * *"
    assert cron_to_local_time(cron, "Asia/Kolkata") == "10:30"
    assert local_time_to_cron("09:00", "weekly", "UTC").endswith("* * 1")
    assert local_time_to_cron("09:00", "monthly", "UTC").endswith("1 * *")
    assert cron_to_local_time("bad", "UTC") == "09:00"


def test_next_occurrence_and_relative_labels():
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    nxt = next_occurrence("11:00", "UTC", now)
    assert nxt.day == 8 and nxt.hour == 11
    assert scheduled_relative(nxt, "UTC", now) == "tomorrow · in 23h 00m"
    assert scheduled_relative(next_occurrence("12:30", "UTC", now), "UTC", now) == "today · in 30m"
    assert relative_time("2026-10-07T11:00:00Z", now) == "1h ago"
    assert run_started_compact("2026-10-07T09:05:00Z", "UTC", now) == "Today · 09:05"
    assert run_started_compact("2026-10-06T09:05:00Z", "UTC", now) == "Yesterday · 09:05"


# ── SSE ───────────────────────────────────────────────────────────────────
def test_iter_sse_unwraps_nested_data_and_skips_heartbeats():
    lines = [
        "event: connected", 'data: {"execution_id": "x"}', "",
        ": heartbeat", "",
        "event: tool_call", 'data: {"event_type": "tool_call", "data": {"tool": "grep", "args": {"q": 1}}}', "",
        "event: llm_token", 'data: {"data": {"token": "Hel"}}', "",
        "data: not json", "",
    ]
    evs = list(iter_sse(lines))
    assert [e.event for e in evs] == ["connected", "tool_call", "llm_token", "message"]
    assert evs[1].data["tool"] == "grep" and evs[1].data["args"] == {"q": 1}
    assert evs[2].data["token"] == "Hel"
    assert evs[3].data == {"message": "not json"}


# ── result extraction ─────────────────────────────────────────────────────
def test_extract_final_answer_prefers_agent_node():
    data = {"workflow_name": "w", "cloudwatch_tool_1": {"output": "long cloudwatch report"},
            "agent_1": {"final_answer": "root cause is X", "privacy_redactions": [{"type": "EMAIL"}],
                        "input_tokens": 10, "output_tokens": 5}}
    assert extract_final_answer(data) == "root cause is X"
    assert extract_privacy_redactions(data) == [{"type": "EMAIL"}]
    assert extract_tokens(data) == {"input": 10, "output": 5, "total": 15}
    assert extract_final_answer({"results": {"cw": {"output": "only report"}}}) == "only report"
    assert extract_tokens({"input_tokens": 3, "output_tokens": 4, "total_tokens": 9})["total"] == 9


def test_extract_node_field_and_clean_text():
    data = {"results": {"a": {"x": 1}, "b": {"structured_output": {"root_cause": "rc"}}}}
    assert extract_node_field(data, "structured_output") == {"root_cause": "rc"}
    assert clean_llm_text("[{'type': 'text', 'text': 'hello\\nworld'}]") == "hello\nworld"
    assert clean_llm_text("plain") == "plain"


# ── schedules ─────────────────────────────────────────────────────────────
def test_apply_schedule_creates_scheduler_node_and_edge():
    wf = {"name": "w", "nodes": [{"id": "ag", "type": "agent", "data": {"label": "Bot"}}], "edges": []}
    assert target_nodes(wf) == [{"id": "ag", "label": "Bot (agent)"}]
    out = apply_schedule(wf, {"title": "Nightly", "target_node": "ag", "recurrence": "daily",
                              "start_time": "02:00", "schedule": "0 2 * * *"})
    sched = next(n for n in out["nodes"] if n["type"] == "scheduler")
    assert sched["data"]["cronExpression"] == "0 2 * * *" and sched["data"]["label"] == "Nightly"
    assert out["edges"][0]["target"] == "ag" and out["schedule"] == "0 2 * * *"
    assert wf["nodes"] == [{"id": "ag", "type": "agent", "data": {"label": "Bot"}}]  # input untouched
    again = apply_schedule(out, {"schedule": "0 3 * * *", "enabled": False})
    assert len([n for n in again["nodes"] if n["type"] == "scheduler"]) == 1
    assert again["enabled"] is False
    view = from_api(again, "UTC")
    assert view["start_time"] == "02:00" and view["enabled"] is False


# ── api error messages ────────────────────────────────────────────────────
def test_error_message_shapes():
    em = api_mod.error_message
    assert em({"detail": "nope"}, "x") == "nope"
    assert em({"error": "E", "message": {"message": "bad", "errors": ["a", "b"]}}, "x") == "a\nb"
    assert em({"message": {"message": "bad"}}, "x") == "bad"
    assert em("text body", "x") == "text body"
    assert em({}, "fallback") == "fallback"


def test_base_url_normalisation(monkeypatch):
    from oncall_ui import config
    monkeypatch.setenv("AGENT_API_URL", "http://agent-api:8000/")
    assert config.api_base_url() == "http://agent-api:8000/api/v1"
    monkeypatch.setenv("AGENT_API_URL", "http://h/api/v1")
    assert config.api_base_url() == "http://h/api/v1"
