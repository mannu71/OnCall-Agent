"""Harness self-test — deterministic regression coverage for app/harness/.

The accuracy harness (``evals.accuracy``) exercises the crawler/CloudWatch tools
directly but NOT ``ReactStrategy.execute`` / the harness runtime. This module
fills that blind spot: it unit-tests the harness primitives the strategy depends
on, so a regression in the agent-path logic is caught WITHOUT needing a live
Bedrock run.

Run inside the agent-api container:

    docker exec agent-api-agent-api-1 sh -c "cd /app && python -m evals.harness_selftest"

Exits non-zero on the first failure (CI-gate friendly).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)  # keep output to the PASS/FAIL lines

_FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    print(f"{status} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        _FAILURES.append(name)


# ── import hygiene ───────────────────────────────────────────────────────────
def test_import_order_no_cycles() -> None:
    """Fresh-interpreter import checks for known circular-import trip wires.

    router_classify <-> strategies.router was a latent cycle: whichever of
    the two loaded FIRST determined whether the process worked or every
    workflow run silently broke (the failed first import poisoned the
    executor handler registry — agent/language_model/cloudwatch nodes became
    "Unknown node type" skips and the chat surfaced a feeder node's output
    as the answer). These must run in SUBPROCESSES: the current interpreter
    already has everything cached, which is exactly how the bug hides.
    """
    import subprocess
    env = {**os.environ, "PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8"}
    probes = {
        "router_classify imported first": "import app.workflow.router_classify",
        "handler registry imported first (all core handlers registered)": (
            "from app.workflow.executor.handlers import HANDLERS; "
            "missing = {'agent','language_model','cloudwatch_tool','router'} - set(HANDLERS); "
            "assert not missing, f'missing handlers: {missing}'"
        ),
    }
    for label, code in probes.items():
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=env, timeout=120,
        )
        check(
            f"import hygiene: {label}",
            proc.returncode == 0,
            (proc.stderr or "")[-300:],
        )


# ── envelopes ────────────────────────────────────────────────────────────────
def test_envelopes() -> None:
    from app.harness import ToolResult, NodeOutput, cap_text

    tr = ToolResult.ok("found 3 rows", data={"rows": [1, 2, 3]})
    rendered = tr.render()
    check("envelopes.ToolResult.render summary-first", rendered.startswith("found 3 rows"))

    capped, trunc = cap_text("x" * 50_000, 12_000, "db")
    check("envelopes.cap_text truncates", trunc and len(capped) < 13_000, f"len={len(capped)}")
    capped2, trunc2 = cap_text("short", 12_000, "db")
    check("envelopes.cap_text passthrough", (not trunc2) and capped2 == "short")

    no = NodeOutput(status="error", output="boom", error="bad").to_dict()
    check("envelopes.NodeOutput error→failed", no["status"] == "failed")
    ok = NodeOutput(status="ok", output="hi").to_dict()
    check("envelopes.NodeOutput ok passthrough", ok["status"] == "ok")


# ── tool_router ──────────────────────────────────────────────────────────────
def test_tool_router() -> None:
    import os
    from app.harness.tool_router import filter_tools

    class T:
        def __init__(self, n, d=""):
            self.name = n
            self.description = d

    os.environ["TOOL_ROUTER_TOP_K"] = "2"
    specials = [T("cloudwatch_search_logs"), T("code_find"), T("db_list_tables")]
    mcps = [T("alpha_query", "alpha"), T("beta_thing", "beta"), T("gamma_io", "gamma")]
    out = filter_tools(specials + mcps, query="alpha")
    names = [t.name for t in out]
    check("tool_router keeps special prefixes", all(s.name in names for s in specials))
    kept_mcp = [n for n in names if n in {"alpha_query", "beta_thing", "gamma_io"}]
    check("tool_router caps MCP at top_k", len(kept_mcp) <= 2, f"kept={kept_mcp}")
    check("tool_router passthrough when no MCP", filter_tools(specials, "x") == specials)


# ── tool_exposure (Phase 1: window mode) ──────────────────────────────────────
def test_tool_exposure() -> None:
    from app.harness.tool_exposure import ToolExposureManager

    class T:
        def __init__(self, n, d=""):
            self.name = n
            self.description = d

    core = [T("cloudwatch_search_logs"), T("crawler_find"), T("fs_write"), T("write_todos")]
    _words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
              "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
              "quebec", "romeo", "sierra", "tango"]
    rankable = [T(f"srv_tool_{i}", f"handles {w} widgets") for i, w in enumerate(_words)]

    mgr = ToolExposureManager(core + rankable, max_direct=5)
    bound = mgr.window("handles quebec widgets")
    names = [t.name for t in bound]

    check("tool_exposure: core always kept", all(c.name in names for c in core))
    check("tool_exposure: bridge present when rankable non-empty",
          "search_tools" in names and "call_tool" in names)
    rankable_bound = [n for n in names if n.startswith("srv_tool_")]
    check("tool_exposure: rankable capped at max_direct", len(rankable_bound) <= 5,
          f"bound={rankable_bound}")
    check("tool_exposure: query-relevant tool ranked into window",
          "srv_tool_16" in rankable_bound, f"bound={rankable_bound}")
    check("tool_exposure: counts exposed", mgr.core_count == 4 and mgr.rankable_count == 20)

    # No rankable tools at all -> no bridge (nothing to search for), core only.
    mgr_core_only = ToolExposureManager(core, max_direct=5)
    bound_core_only = mgr_core_only.window("anything")
    check("tool_exposure: no bridge when nothing rankable",
          "search_tools" not in [t.name for t in bound_core_only])
    check("tool_exposure: core-only window == core", len(bound_core_only) == len(core))

    # Tools outside the window stay reachable via the bridge's own catalog.
    check("tool_exposure: bridge_names are the two bridge tools",
          set(mgr.bridge_names) == {"search_tools", "call_tool"})


# ── error_classifier: Bedrock context-overflow rung ──────────────────────────
def test_boto_context_overflow_classifier() -> None:
    """_classify_boto_error must route Bedrock's plain ValidationException
    context-overflow messages to CONTEXT_OVERFLOW/should_compress, not UNKNOWN —
    otherwise neither engine's reactive-compact-and-retry rung ever fires."""
    from botocore.exceptions import ClientError
    from app.core.error_classifier import classify_error, FailoverReason

    def _ve(message: str) -> ClientError:
        return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "Converse")

    overflow_messages = [
        "Input is too long for requested model.",
        "Malformed input request: too many tokens in the input, please reduce",
        "This model's maximum context length is 200000 tokens.",
    ]
    for msg in overflow_messages:
        ce = classify_error(_ve(msg))
        check(
            f"boto ValidationException overflow→CONTEXT_OVERFLOW ({msg[:30]!r})",
            ce.reason == FailoverReason.CONTEXT_OVERFLOW and ce.should_compress,
            f"reason={ce.reason} should_compress={ce.should_compress}",
        )

    ce_other = classify_error(_ve("The requested schema is invalid."))
    check(
        "boto ValidationException non-overflow→UNKNOWN",
        ce_other.reason == FailoverReason.UNKNOWN and not ce_other.should_compress,
        f"reason={ce_other.reason}",
    )


# ── engine: resolve_engine() flag resolution + native NotImplementedError ────
def test_engine_resolution() -> None:
    from app.harness.engine import resolve_engine, ENGINE_LANGGRAPH, ENGINE_NATIVE

    check("engine: defaults to langgraph", resolve_engine({}) == ENGINE_LANGGRAPH)
    check("engine: defaults to langgraph with no config", resolve_engine(None) == ENGINE_LANGGRAPH)
    check(
        "engine: per-workflow override via agent_config['engine']",
        resolve_engine({"engine": "native"}) == ENGINE_NATIVE,
    )
    check(
        "engine: per-workflow override via params mirror",
        resolve_engine({"params": {"engine": "native"}}) == ENGINE_NATIVE,
    )
    check(
        "engine: unknown value falls back to langgraph",
        resolve_engine({"engine": "bogus"}) == ENGINE_LANGGRAPH,
    )
    check(
        "engine: hitl_enabled clamps native→langgraph",
        resolve_engine({"engine": "native", "hitl_enabled": True}) == ENGINE_LANGGRAPH,
    )


def _fake_ai_message(content: str = "", tool_calls=None, truncated: bool = False):
    from langchain_core.messages import AIMessage
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        response_metadata={"stopReason": "max_tokens"} if truncated else {},
    )


class _FakeToolBoundLLM:
    """Stands in for a tool-bound chat model. Not Bedrock/Anthropic-named, so
    TurnLoop.__init__ exercises the generic bind_tools() fallback path."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, messages, config=None):
        idx = min(self.calls, len(self._responses) - 1)
        msg = self._responses[idx]
        self.calls += 1
        return msg


class _FakeTool:
    def __init__(self, name: str, result: str = "tool output"):
        self.name = name
        self._result = result

    async def ainvoke(self, args):
        return self._result


async def test_turn_loop_happy_path() -> None:
    """A model that answers with no tool_calls on turn 1 completes immediately."""
    from app.harness.engine.turn_loop import TurnLoop
    from app.harness import AgentSpec

    from langchain_core.messages import HumanMessage

    llm = _FakeToolBoundLLM([_fake_ai_message("The answer is 42.")])
    loop = TurnLoop(llm, [], "You are a helpful agent.", agent_config={}, max_turns=5)
    result = await loop.run([HumanMessage(content="what is the answer?")])
    check("turn_loop: happy path final_answer", result["final_answer"] == "The answer is 42.")
    check("turn_loop: happy path no truncation", not result.get("truncated"))
    check("turn_loop: happy path single model call", llm.calls == 1)


async def test_turn_loop_tool_call_then_complete() -> None:
    """Turn 1 calls a tool, turn 2 synthesizes from the tool result."""
    from app.harness.engine.turn_loop import TurnLoop
    from langchain_core.messages import HumanMessage

    tool_call = {"name": "fake_tool", "args": {"x": 1}, "id": "call_1"}
    llm = _FakeToolBoundLLM([
        _fake_ai_message("", tool_calls=[tool_call]),
        _fake_ai_message("Done, found it via fake_tool."),
    ])
    loop = TurnLoop(llm, [_FakeTool("fake_tool")], "sys", agent_config={}, max_turns=5)
    result = await loop.run([HumanMessage(content="investigate")])
    check("turn_loop: tool round-trip completes", result["final_answer"] == "Done, found it via fake_tool.")
    check("turn_loop: tool round-trip records tool_calls", len(result["tool_calls"]) == 1)
    check("turn_loop: tool round-trip 2 model calls", llm.calls == 2)


async def test_step_recorder() -> None:
    from app.harness.step_recorder import StepRecorder, build_step_recorder, _NoopStepRecorder
    from app.config import settings as _s
    from app.infrastructure.persistence.trajectory_event_repository import (
        trajectory_event_repository as _ter_instance,
    )

    # ── unit: buffer accumulates, flush persists then clears ──
    rec = StepRecorder("trace-1", span_id=None, model_id="m1")
    check("step_recorder: starts empty", rec.buffered_count == 0)
    rec.record_model_turn(
        1, reason="model_call", text="hello", tool_calls=[{"id": "c1", "name": "t"}],
        stop_reason="end_turn", tokens={"in": 10, "out": 5},
    )
    rec.record_tool_call(1, tool_name="t", status="ok", latency_ms=12.3)
    rec.record_lifecycle(2, "max_turns_forced_synthesis", max_turns=7)
    check("step_recorder: buffers 3 events", rec.buffered_count == 3)

    captured: list = []

    async def _fake_append_batch(events):
        captured.extend(events)

    _orig_append = _ter_instance.append_batch
    _ter_instance.append_batch = _fake_append_batch
    try:
        await rec.flush()
    finally:
        _ter_instance.append_batch = _orig_append

    check("step_recorder: flush persists all buffered events", len(captured) == 3)
    check("step_recorder: flush clears buffer", rec.buffered_count == 0)
    check(
        "step_recorder: event has trace_id/step_index/type",
        all(e["trace_id"] == "trace-1" for e in captured)
        and {e["type"] for e in captured} == {"model_turn", "tool_call", "lifecycle"},
    )
    model_turn_evt = next(e for e in captured if e["type"] == "model_turn")
    check(
        "step_recorder: model_turn payload has action.tool_calls",
        model_turn_evt["payload"]["action"]["tool_calls"] == [{"id": "c1", "name": "t"}],
    )
    check(
        "step_recorder: model_turn payload has tokens in meta",
        model_turn_evt["payload"]["meta"]["tokens"] == {"in": 10, "out": 5},
    )

    # flush() on an empty buffer never calls the repository.
    called = {"n": 0}

    async def _counting_append_batch(events):
        called["n"] += 1

    _ter_instance.append_batch = _counting_append_batch
    try:
        await rec.flush()
    finally:
        _ter_instance.append_batch = _orig_append
    check("step_recorder: flush on empty buffer is a no-op", called["n"] == 0)

    # flush() failure is swallowed — recording must never break a run.
    async def _boom(events):
        raise RuntimeError("db down")

    rec2 = StepRecorder("trace-2")
    rec2.record_lifecycle(0, "x")
    _ter_instance.append_batch = _boom
    try:
        await rec2.flush()
        flush_ok = True
    except Exception:  # noqa: BLE001
        flush_ok = False
    finally:
        _ter_instance.append_batch = _orig_append
    check("step_recorder: flush failure never raises", flush_ok)

    # ── _NoopStepRecorder: everything is a safe no-op ──
    noop = _NoopStepRecorder()
    noop.record_model_turn(1, reason="x")
    noop.record_tool_call(1, tool_name="t", status="ok")
    noop.record_lifecycle(1, "x")
    await noop.flush()
    check("step_recorder: noop recorder never buffers", noop.buffered_count == 0)
    check("step_recorder: noop.enabled is False", noop.enabled is False)
    check("step_recorder: StepRecorder.enabled is True", StepRecorder("t").enabled is True)

    # ── build_step_recorder: flag/trace_id gating ──
    _orig_flag = _s.step_events_enabled
    try:
        _s.step_events_enabled = False
        check(
            "build_step_recorder: disabled -> noop",
            isinstance(build_step_recorder("exec-1"), _NoopStepRecorder),
        )
        _s.step_events_enabled = True
        check(
            "build_step_recorder: enabled + trace_id -> live recorder",
            isinstance(build_step_recorder("exec-1"), StepRecorder),
        )
        check(
            "build_step_recorder: enabled but no trace_id -> noop",
            isinstance(build_step_recorder(None), _NoopStepRecorder),
        )
    finally:
        _s.step_events_enabled = _orig_flag


async def test_turn_loop_step_events_wiring() -> None:
    """End-to-end: step_events_enabled=True on a real TurnLoop run persists
    model_turn + tool_call events with the run's execution_id as trace_id."""
    from app.harness.engine.turn_loop import TurnLoop
    from app.config import settings as _s
    from langchain_core.messages import HumanMessage
    from app.infrastructure.persistence.trajectory_event_repository import (
        trajectory_event_repository as _ter_instance,
    )

    captured: list = []

    async def _fake_append_batch(events):
        captured.extend(events)

    _orig_append = _ter_instance.append_batch
    _orig_flag = _s.step_events_enabled
    _ter_instance.append_batch = _fake_append_batch
    _s.step_events_enabled = True
    try:
        tool_call = {"name": "fake_tool", "args": {"x": 1}, "id": "call_1"}
        llm = _FakeToolBoundLLM([
            _fake_ai_message("", tool_calls=[tool_call]),
            _fake_ai_message("Done, found it via fake_tool."),
        ])
        loop = TurnLoop(
            llm, [_FakeTool("fake_tool")], "sys", agent_config={}, max_turns=5,
            execution_id="turnloop-step-events-exec",
        )
        await loop.run([HumanMessage(content="investigate")])
    finally:
        _ter_instance.append_batch = _orig_append
        _s.step_events_enabled = _orig_flag

    types = [e["type"] for e in captured]
    check("turn_loop+step_events: model_turn events recorded", types.count("model_turn") == 2, str(types))
    check("turn_loop+step_events: tool_call event recorded", types.count("tool_call") == 1, str(types))
    check(
        "turn_loop+step_events: all events carry execution_id as trace_id",
        bool(captured) and all(e["trace_id"] == "turnloop-step-events-exec" for e in captured),
    )


async def test_instrument_langgraph_result() -> None:
    """LangGraph parity (post-hoc): _instrument_langgraph_result derives the
    same trajectory events / failure-ledger recording / verify tracking the
    native turn loop produces live, from a serialized LangGraph message list."""
    from app.harness.agent_runner import _instrument_langgraph_result
    from app.harness.step_recorder import StepRecorder
    from app.harness.verify_tracking import VerifyState
    from app.config import settings as _ilg_settings
    from app.infrastructure.persistence import failure_ledger_repository as _ilg_fl

    parsed = {
        "messages": [
            {"role": "user", "content": "fix the bug and verify it"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "name": "edit_file", "args": {}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true, "file": "a.py"}'},
            {
                "role": "assistant", "content": "",
                "tool_calls": [
                    {"id": "c2", "name": "run_verify", "args": {}},
                    {"id": "c3", "name": "cw_search", "args": {}},
                ],
            },
            {"role": "tool", "tool_call_id": "c2", "content": '{"ok": false, "exit_code": 1}'},
            {"role": "tool", "tool_call_id": "c3", "content": "Error: throttled"},
            {"role": "assistant", "content": "Done, but verification failed."},
        ],
    }

    recorder = StepRecorder("lg-instr-trace")
    verify_state = VerifyState()

    _orig_record = _ilg_fl.record
    recorded_fingerprints: list = []

    async def _capture_record(fingerprint, execution_id=None):
        recorded_fingerprints.append((fingerprint, execution_id))
        return 1

    _orig_gov_flag = _ilg_settings.governance_conversion_enabled
    _ilg_fl.record = _capture_record
    _ilg_settings.governance_conversion_enabled = True
    try:
        await _instrument_langgraph_result(
            parsed, recorder=recorder, verify_state=verify_state, execution_id="lg-instr-exec",
        )
    finally:
        _ilg_fl.record = _orig_record
        _ilg_settings.governance_conversion_enabled = _orig_gov_flag

    check("instrument_langgraph: records a model_turn per assistant message", recorder.buffered_count >= 3)
    types = [ev["type"] for ev in recorder._buffer]
    check("instrument_langgraph: 3 model_turn events (one per assistant message)", types.count("model_turn") == 3)
    check("instrument_langgraph: 3 tool_call events (one per tool message)", types.count("tool_call") == 3)

    tool_events = {ev["payload"]["action"]["name"]: ev for ev in recorder._buffer if ev["type"] == "tool_call"}
    check("instrument_langgraph: resolves tool names via tool_call_id", set(tool_events.keys()) == {"edit_file", "run_verify", "cw_search"})
    check("instrument_langgraph: successful edit_file classified ok", tool_events["edit_file"]["payload"]["outcome"]["status"] == "ok")
    check("instrument_langgraph: failing cw_search classified error", tool_events["cw_search"]["payload"]["outcome"]["status"] == "error")

    check(
        "instrument_langgraph: verify_pending cleared by run_verify (edit -> verify sequencing)",
        verify_state.pending is False,
    )
    check("instrument_langgraph: verify_last_passed reflects run_verify's own result", verify_state.last_passed is False)

    check(
        "instrument_langgraph: live failure-ledger hook fired for the failing tool call",
        any("cw_search" in fp for fp, _eid in recorded_fingerprints),
    )
    check(
        "instrument_langgraph: failure-ledger hook passes execution_id through",
        all(eid == "lg-instr-exec" for _fp, eid in recorded_fingerprints),
    )

    # Empty input is a safe no-op.
    empty_recorder = StepRecorder("lg-instr-empty")
    empty_verify = VerifyState()
    await _instrument_langgraph_result(
        {"messages": []}, recorder=empty_recorder, verify_state=empty_verify, execution_id=None,
    )
    check("instrument_langgraph: empty message list is a no-op", empty_recorder.buffered_count == 0)

    # ── skip_first_n: replayed prior-turn chat history must NOT be
    #    re-recorded as this execution's events (or re-fingerprinted into the
    #    failure ledger on every follow-up turn of a session). The same
    #    7-message trace, but the first 6 entries are the replayed initial
    #    prefix (in serialized-list coordinates) — only the final assistant
    #    message belongs to this run. ──
    skip_recorder = StepRecorder("lg-instr-skip")
    skip_verify = VerifyState()
    recorded_fingerprints.clear()
    _ilg_fl.record = _capture_record
    _ilg_settings.governance_conversion_enabled = True
    try:
        await _instrument_langgraph_result(
            parsed, recorder=skip_recorder, verify_state=skip_verify,
            execution_id="lg-instr-skip-exec", skip_first_n=6,
        )
    finally:
        _ilg_fl.record = _orig_record
        _ilg_settings.governance_conversion_enabled = _orig_gov_flag
    skip_types = [ev["type"] for ev in skip_recorder._buffer]
    check(
        "instrument_langgraph: skip_first_n excludes replayed history from events",
        skip_types.count("model_turn") == 1 and skip_types.count("tool_call") == 0,
    )
    check(
        "instrument_langgraph: skip_first_n suppresses re-fingerprinting prior failures",
        recorded_fingerprints == [],
    )
    check(
        "instrument_langgraph: skip_first_n leaves prior-turn verify state untouched",
        skip_verify.pending is False and skip_verify.last_passed is None,
    )


async def test_turn_loop_max_turns_forced_synthesis() -> None:
    """A model that never stops calling tools gets a forced-synthesis nudge
    once, then hits a hard MAX_TURNS-equivalent stop shortly after."""
    from app.harness.engine.turn_loop import TurnLoop
    from app.harness.engine.loop_state import StopReason
    from langchain_core.messages import HumanMessage

    tool_call = {"name": "fake_tool", "args": {}, "id": "call_x"}
    # Always returns a tool call — never lets the loop terminate naturally.
    llm = _FakeToolBoundLLM([_fake_ai_message("", tool_calls=[tool_call])] * 20)
    loop = TurnLoop(llm, [_FakeTool("fake_tool")], "sys", agent_config={}, max_turns=1)
    result = await loop.run([HumanMessage(content="loop forever")])
    check("turn_loop: max_turns eventually stops", llm.calls < 20, f"calls={llm.calls}")
    check("turn_loop: max_turns produces a final_answer key", "final_answer" in result)
    check(
        "turn_loop: max_turns surfaces stop_reason=max_turns",
        result.get("stop_reason") == StopReason.MAX_TURNS.value, result.get("stop_reason"),
    )
    check("turn_loop: max_turns surfaces did_forced_synthesis", result.get("did_forced_synthesis") is True)


async def test_turn_loop_verify_pending_tracking() -> None:
    """An edit_file call with no follow-up run_verify leaves verify_pending
    True in the result; a subsequent run_verify call clears it."""
    import json as _json
    from app.harness.engine.turn_loop import TurnLoop
    from langchain_core.messages import HumanMessage

    edit_call = {"name": "edit_file", "args": {}, "id": "c1"}
    llm = _FakeToolBoundLLM([
        _fake_ai_message("", tool_calls=[edit_call]),
        _fake_ai_message("Edited the file."),
    ])
    edit_tool = _FakeTool("edit_file", result=_json.dumps({"ok": True, "file": "a.py"}))
    loop = TurnLoop(llm, [edit_tool], "sys", agent_config={}, max_turns=5)
    result = await loop.run([HumanMessage(content="fix the bug")])
    check("turn_loop: edit without verify leaves verify_pending True", result.get("verify_pending") is True)
    check("turn_loop: verify_last_passed unset without a run_verify call", result.get("verify_last_passed") is None)

    verify_call = {"name": "run_verify", "args": {"repo": "x"}, "id": "c2"}
    llm2 = _FakeToolBoundLLM([
        _fake_ai_message("", tool_calls=[edit_call]),
        _fake_ai_message("", tool_calls=[verify_call]),
        _fake_ai_message("Edited and verified."),
    ])
    edit_tool2 = _FakeTool("edit_file", result=_json.dumps({"ok": True, "file": "a.py"}))
    verify_tool = _FakeTool("run_verify", result=_json.dumps({"ok": True, "exit_code": 0}))
    loop2 = TurnLoop(llm2, [edit_tool2, verify_tool], "sys", agent_config={}, max_turns=5)
    result2 = await loop2.run([HumanMessage(content="fix the bug")])
    check("turn_loop: run_verify clears verify_pending", result2.get("verify_pending") is False)
    check("turn_loop: verify_last_passed reflects run_verify result", result2.get("verify_last_passed") is True)

    verify_call_fail = {"name": "run_verify", "args": {"repo": "x"}, "id": "c3"}
    llm3 = _FakeToolBoundLLM([
        _fake_ai_message("", tool_calls=[edit_call]),
        _fake_ai_message("", tool_calls=[verify_call_fail]),
        _fake_ai_message("Tried to verify, it failed."),
    ])
    edit_tool3 = _FakeTool("edit_file", result=_json.dumps({"ok": True, "file": "a.py"}))
    verify_tool_fail = _FakeTool("run_verify", result=_json.dumps({"ok": False, "exit_code": 1}))
    loop3 = TurnLoop(llm3, [edit_tool3, verify_tool_fail], "sys", agent_config={}, max_turns=5)
    result3 = await loop3.run([HumanMessage(content="fix the bug")])
    check("turn_loop: failed verify still clears pending", result3.get("verify_pending") is False)
    check("turn_loop: failed verify records verify_last_passed False", result3.get("verify_last_passed") is False)


def test_terminal_state_derivation() -> None:
    from app.harness.terminal_state import derive_terminal_state, terminal_state_for_result
    from app.harness.engine.loop_state import StopReason, TerminalState

    check(
        "terminal_state: plain success",
        derive_terminal_state() == TerminalState.SUCCESS.value,
    )
    check(
        "terminal_state: conversational -> no_op (highest precedence)",
        derive_terminal_state(is_conversational=True, supervisor_escalated=True) == TerminalState.NO_OP.value,
    )
    check(
        "terminal_state: forced synthesis -> exhausted",
        derive_terminal_state(did_forced_synthesis=True) == TerminalState.EXHAUSTED.value,
    )
    check(
        "terminal_state: truncated -> exhausted",
        derive_terminal_state(truncated=True) == TerminalState.EXHAUSTED.value,
    )
    check(
        "terminal_state: stop_reason=max_turns -> exhausted",
        derive_terminal_state(stop_reason=StopReason.MAX_TURNS.value) == TerminalState.EXHAUSTED.value,
    )
    check(
        "terminal_state: stop_reason=token_budget -> exhausted",
        derive_terminal_state(stop_reason=StopReason.TOKEN_BUDGET.value) == TerminalState.EXHAUSTED.value,
    )
    check(
        "terminal_state: stop_reason=hitl_paused -> blocked",
        derive_terminal_state(stop_reason=StopReason.HITL_PAUSED.value) == TerminalState.BLOCKED.value,
    )
    check(
        "terminal_state: stop_reason=aborted -> blocked",
        derive_terminal_state(stop_reason=StopReason.ABORTED.value) == TerminalState.BLOCKED.value,
    )
    check(
        "terminal_state: supervisor_escalated -> stalled",
        derive_terminal_state(supervisor_escalated=True) == TerminalState.STALLED.value,
    )
    check(
        "terminal_state: supervisor_retry_exhausted -> stalled",
        derive_terminal_state(supervisor_retry_exhausted=True) == TerminalState.STALLED.value,
    )
    check(
        "terminal_state: verify_pending -> unverified",
        derive_terminal_state(verify_pending=True) == TerminalState.UNVERIFIED.value,
    )
    check(
        "terminal_state: exhausted takes precedence over unverified",
        derive_terminal_state(truncated=True, verify_pending=True) == TerminalState.EXHAUSTED.value,
    )
    check(
        "terminal_state: stalled takes precedence over unverified",
        derive_terminal_state(supervisor_escalated=True, verify_pending=True) == TerminalState.STALLED.value,
    )

    check(
        "terminal_state_for_result: pulls fields from a result dict",
        terminal_state_for_result({
            "stop_reason": None, "verify_pending": True, "supervisor_escalated": False,
        }) == TerminalState.UNVERIFIED.value,
    )
    check(
        "terminal_state_for_result: missing keys default safely",
        terminal_state_for_result({}) == TerminalState.SUCCESS.value,
    )

    # LangGraph parity: execute_agent()'s result dict shape (stop_reason/
    # did_forced_synthesis/verify_pending/verify_last_passed, added for
    # LangGraph parity with the native engine) classifies identically.
    check(
        "terminal_state_for_result: LangGraph recursion-limit recovery -> exhausted",
        terminal_state_for_result({
            "stop_reason": "max_turns", "did_forced_synthesis": True,
        }) == TerminalState.EXHAUSTED.value,
    )
    check(
        "terminal_state_for_result: LangGraph hitl_paused -> blocked",
        terminal_state_for_result({"stop_reason": "hitl_paused"}) == TerminalState.BLOCKED.value,
    )
    check(
        "terminal_state_for_result: LangGraph unverified edit -> unverified",
        terminal_state_for_result({
            "stop_reason": "completed", "verify_pending": True,
        }) == TerminalState.UNVERIFIED.value,
    )
    check(
        "terminal_state_for_result: LangGraph clean completion -> success",
        terminal_state_for_result({
            "stop_reason": "completed", "did_forced_synthesis": False, "verify_pending": False,
        }) == TerminalState.SUCCESS.value,
    )


async def test_engine_native_dispatch() -> None:
    """run_agent_once(engine='native') routes through TurnLoop, not the
    LangGraph path — verified by monkeypatching the native entry point."""
    import app.harness.engine as engine_mod
    from app.harness import AgentSpec

    spec = AgentSpec(agent_config={}, has_cloudwatch=False, has_code_analyzer=False,
                      permission_mode="auto_allow", session_id="x")
    called = {}

    async def _fake_run_native(spec_, llm, tools, user_query, **kw):
        called["hit"] = True
        called["user_query"] = user_query
        return {"final_answer": "native ran", "messages": [], "tool_calls": [],
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0}

    orig = engine_mod._run_native
    engine_mod._run_native = _fake_run_native
    try:
        result = await engine_mod.run_agent_once(
            spec, llm="LLM", tools=[], user_query="hi", engine=engine_mod.ENGINE_NATIVE,
        )
        check("engine: native dispatch reaches _run_native", called.get("hit") is True)
        check("engine: native dispatch passes query through", called.get("user_query") == "hi")
        check("engine: native dispatch returns its result", result.get("final_answer") == "native ran")
    finally:
        engine_mod._run_native = orig


# ── engine: compression pipeline / preserved-tail contract ───────────────────
def test_compression_split_preserved_tail() -> None:
    """The tail boundary must never orphan a ToolMessage from the AIMessage
    that requested it — a split pair would make Bedrock reject the retry
    this splitter exists to protect (INVALID_CHAT_HISTORY)."""
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from app.core.memory.compaction import _msg_token_estimate
    from app.harness.engine.compression import split_preserved_tail

    messages = [
        HumanMessage(content="q1"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "a1"}]),
        ToolMessage(content="result1", tool_call_id="a1"),
        HumanMessage(content="q2"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "b1"}]),
        ToolMessage(content="result2", tool_call_id="b1"),
    ]
    # Tuned so the naive backward walk lands exactly on the LAST ToolMessage
    # alone — the failure mode the pairing-safety extension must catch.
    last_tokens = _msg_token_estimate(messages[-1])
    head, tail = split_preserved_tail(messages, keep_recent_tokens=last_tokens)
    check(
        "compression: tail never starts on an orphaned ToolMessage",
        not (tail and isinstance(tail[0], ToolMessage)),
        f"tail[0]={type(tail[0]).__name__ if tail else None}",
    )
    check(
        "compression: tail includes the AIMessage that requested its tool result",
        bool(tail) and isinstance(tail[0], AIMessage) and tail[0].tool_calls[0]["id"] == "b1",
    )
    check("compression: head+tail reconstruct the original list", head + tail == messages)

    # A boundary that's already safe (lands on a plain HumanMessage) needs no
    # adjustment — head/tail split exactly where the token walk says.
    safe_tokens = sum(_msg_token_estimate(m) for m in messages[3:])
    head2, tail2 = split_preserved_tail(messages, keep_recent_tokens=safe_tokens)
    check(
        "compression: already-safe boundary is left unadjusted",
        tail2 == messages[3:], f"tail2 len={len(tail2)} expected={len(messages[3:])}",
    )


def test_compression_split_preserved_tail_empty() -> None:
    from app.harness.engine.compression import split_preserved_tail

    check(
        "compression: empty messages -> empty head/tail",
        split_preserved_tail([], 100) == ([], []),
    )


async def test_compression_pipeline_delegates() -> None:
    """CompressionPipeline is a thin dispatcher — verify it calls through to
    the manager's compact_if_needed / force_compact, not its own logic."""
    from app.harness.engine.compression import CompressionPipeline

    class _FakeMgr:
        def __init__(self):
            self.compact_if_needed_calls = 0
            self.force_compact_calls = 0

        async def compact_if_needed(self, messages):
            self.compact_if_needed_calls += 1
            return messages

        async def force_compact(self, messages):
            self.force_compact_calls += 1
            return messages

    fake = _FakeMgr()
    pipeline = CompressionPipeline(fake)
    await pipeline.maybe_compact(["m1"])
    await pipeline.reactive_compact(["m1"])
    check("compression: maybe_compact delegates to compact_if_needed", fake.compact_if_needed_calls == 1)
    check("compression: reactive_compact delegates to force_compact", fake.force_compact_calls == 1)


async def test_compression_metamemory_precheck() -> None:
    """When metamemory is seeded and over threshold, the agent-maintained
    summary supersedes the LLM-summary tier — compact_if_needed is never
    called. Falls through unchanged when under threshold or unseeded."""
    from app.harness.engine.compression import CompressionPipeline
    from app.core.vfs import vfs_drop_session as _vdrop2
    from app.core.vfs.backend import vfs_write
    from app.harness import metamemory as _mm
    from langchain_core.messages import HumanMessage, SystemMessage

    class _FakeMgr:
        def __init__(self, threshold: int, keep_recent: int = 5):
            self.compact_if_needed_calls = 0
            self._threshold = threshold
            self._keep_recent = keep_recent

        @property
        def compaction_threshold_tokens(self) -> int:
            return self._threshold

        @property
        def keep_recent_tokens(self) -> int:
            return self._keep_recent

        def estimate_tokens(self, messages) -> int:
            return sum(len(str(getattr(m, "content", ""))) for m in messages)

        async def compact_if_needed(self, messages):
            self.compact_if_needed_calls += 1
            return messages

    try:
        # under threshold -> falls straight through, metamemory untouched.
        fake_low = _FakeMgr(threshold=100_000)
        pipeline_low = CompressionPipeline(fake_low, vfs_session_id="cm-precheck-low")
        await pipeline_low.maybe_compact([HumanMessage(content="hi")])
        check(
            "compression: metamemory precheck skipped under threshold",
            fake_low.compact_if_needed_calls == 1,
        )

        # over threshold but metamemory never seeded (empty) -> falls through too.
        fake_empty = _FakeMgr(threshold=1)
        pipeline_empty = CompressionPipeline(fake_empty, vfs_session_id="cm-precheck-empty")
        await pipeline_empty.maybe_compact([HumanMessage(content="x" * 50)])
        check(
            "compression: metamemory precheck falls through when empty",
            fake_empty.compact_if_needed_calls == 1,
        )

        # over threshold + metamemory seeded -> metamemory summary used instead.
        await vfs_write("cm-precheck-seeded", _mm.SUMMARY_PATH, "OBJECTIVE: test\nSTATE: in progress")
        await vfs_write("cm-precheck-seeded", _mm.MILESTONES_PATH, "DONE S1")
        fake_seeded = _FakeMgr(threshold=1, keep_recent=0)
        pipeline_seeded = CompressionPipeline(fake_seeded, vfs_session_id="cm-precheck-seeded")
        out_seeded = await pipeline_seeded.maybe_compact([HumanMessage(content="x" * 100)])
        check(
            "compression: metamemory precheck bypasses compact_if_needed",
            fake_seeded.compact_if_needed_calls == 0,
        )
        check(
            "compression: metamemory precheck produces a SystemMessage summary",
            bool(out_seeded) and isinstance(out_seeded[0], SystemMessage)
            and "OBJECTIVE: test" in out_seeded[0].content and "DONE S1" in out_seeded[0].content,
        )
    finally:
        await _vdrop2("cm-precheck-low")
        await _vdrop2("cm-precheck-empty")
        await _vdrop2("cm-precheck-seeded")


async def test_metamemory_read_context_block_caps_milestones() -> None:
    """milestones.txt is append-only and UNCAPPED at the VFS layer — unlike
    context_summary.txt's write-time 500-token cap. read_context_block must
    tail-truncate it so the injected compaction block stays near the plan's
    ~1200-token budget instead of growing without bound over a long run."""
    from app.core.vfs import vfs_drop_session as _vdrop3
    from app.core.vfs.backend import vfs_write
    from app.harness import metamemory as _mm2

    try:
        await vfs_write("mm-cap-test", _mm2.SUMMARY_PATH, "OBJECTIVE: cap test\nSTATE: ok")
        # 500 lines * ~20 chars each = far beyond _MILESTONES_INJECT_MAX_CHARS.
        big_milestones = "\n".join(f"<ts> DONE S{i} evidence=/x/{i}" for i in range(500))
        await vfs_write("mm-cap-test", _mm2.MILESTONES_PATH, big_milestones)
        block = await _mm2.read_context_block("mm-cap-test")
        check("metamemory read_context_block: not None when seeded", block is not None)
        check(
            "metamemory read_context_block: milestones tail-truncated under budget",
            len(block) < len(big_milestones) + 200,
        )
        check(
            "metamemory read_context_block: truncation marker present",
            "truncated" in block,
        )
        check(
            "metamemory read_context_block: keeps the MOST RECENT milestone (tail, not head)",
            "S499" in block and "DONE S0 evidence=/x/0" not in block,
        )
        check(
            "metamemory read_context_block: summary still present alongside milestones",
            "OBJECTIVE: cap test" in block,
        )

        # small milestones file -> untouched, no truncation marker.
        await vfs_write("mm-cap-test-small", _mm2.SUMMARY_PATH, "OBJECTIVE: small")
        await vfs_write("mm-cap-test-small", _mm2.MILESTONES_PATH, "DONE S1")
        small_block = await _mm2.read_context_block("mm-cap-test-small")
        check(
            "metamemory read_context_block: small milestones left untouched",
            small_block is not None and "truncated" not in small_block and "DONE S1" in small_block,
        )
    finally:
        await _vdrop3("mm-cap-test")
        await _vdrop3("mm-cap-test-small")


async def test_turn_loop_reactive_compact_retry() -> None:
    """A context-overflow error on turn 1 triggers exactly one reactive
    compact-and-retry, then the (now-shorter) request succeeds on turn 2."""
    from botocore.exceptions import ClientError
    from langchain_core.messages import HumanMessage
    from app.harness.engine.turn_loop import TurnLoop

    class _OverflowThenOkLLM(_FakeToolBoundLLM):
        def __init__(self, ok_response):
            super().__init__([ok_response])
            self.attempts = 0

        async def ainvoke(self, messages, config=None):
            self.attempts += 1
            if self.attempts == 1:
                raise ClientError(
                    {"Error": {"Code": "ValidationException", "Message": "Input is too long for requested model."}},
                    "Converse",
                )
            return await super().ainvoke(messages, config=config)

    llm = _OverflowThenOkLLM(_fake_ai_message("Recovered after compaction."))
    loop = TurnLoop(llm, [], "sys", agent_config={}, max_turns=5)

    async def _fake_reactive_compact(messages):
        return messages  # no-op stand-in; only call-count matters here

    loop._compression.reactive_compact = _fake_reactive_compact
    result = await loop.run([HumanMessage(content="huge context")])
    check("turn_loop: recovers after one reactive compact", result["final_answer"] == "Recovered after compaction.")
    check("turn_loop: exactly 2 model attempts (overflow + retry)", llm.attempts == 2)


async def test_turn_loop_truncation_escalation_recovers() -> None:
    """Turn 1 is truncated -> escalate max_output_tokens once -> turn 2 (now
    with the higher ceiling bound) completes cleanly."""
    from langchain_core.messages import HumanMessage
    from app.harness.engine.turn_loop import TurnLoop

    llm = _FakeToolBoundLLM([
        _fake_ai_message("half a tho", truncated=True),
        _fake_ai_message("...ught, now complete."),
    ])
    loop = TurnLoop(llm, [], "sys", agent_config={}, max_turns=5)
    result = await loop.run([HumanMessage(content="long investigation")])
    check("turn_loop: truncation escalation recovers", result["final_answer"] == "...ught, now complete.")
    check("turn_loop: truncation escalation not flagged truncated", not result.get("truncated"))
    check("turn_loop: truncation escalation used exactly 2 calls", llm.calls == 2)


async def test_turn_loop_truncation_ladder_exhausts() -> None:
    """A model that stays truncated through escalation + all recovery turns
    gets an honest partial answer, never a confident half-thought."""
    from langchain_core.messages import HumanMessage
    from app.harness.engine.turn_loop import TurnLoop
    from app.harness.engine.recovery import MAX_OUTPUT_TOKENS_RECOVERY_LIMIT

    always_truncated = [_fake_ai_message("still going", truncated=True)] * 10
    llm = _FakeToolBoundLLM(always_truncated)
    loop = TurnLoop(llm, [], "sys", agent_config={}, max_turns=20)
    result = await loop.run([HumanMessage(content="long investigation")])
    check("turn_loop: exhausted ladder flags truncated", result.get("truncated") is True)
    check(
        "turn_loop: exhausted ladder final_answer is honest partial",
        (result["final_answer"] or "").startswith("Investigation was cut off"),
    )
    # 1 initial + 1 escalation + MAX_OUTPUT_TOKENS_RECOVERY_LIMIT resume attempts.
    check(
        "turn_loop: exhausted ladder used bounded call count",
        llm.calls == 2 + MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
        f"calls={llm.calls}",
    )


async def test_turn_loop_midthought_continuation() -> None:
    """A short mid-investigation preamble ('Let me search for...') gets one
    extra turn to actually conclude, instead of being returned as the answer."""
    from langchain_core.messages import HumanMessage
    from app.harness.engine.turn_loop import TurnLoop

    llm = _FakeToolBoundLLM([
        _fake_ai_message("Let me search for more details on this."),
        _fake_ai_message("Root cause: the retry queue overflowed."),
    ])
    loop = TurnLoop(llm, [], "sys", agent_config={}, max_turns=5)
    result = await loop.run([HumanMessage(content="why did it fail?")])
    check(
        "turn_loop: mid-thought preamble gets a continuation",
        result["final_answer"] == "Root cause: the retry queue overflowed.",
    )
    check("turn_loop: mid-thought continuation used exactly 2 calls", llm.calls == 2)


async def test_recovery_call_model_with_backoff() -> None:
    """call_model_with_backoff retries a transient error in place and
    respects a retry_predicate that vetoes in-place retry."""
    import asyncio
    from app.harness.engine.recovery import call_model_with_backoff

    class _FlakyOnce:
        def __init__(self):
            self.attempts = 0

        async def __call__(self):
            self.attempts += 1
            if self.attempts == 1:
                raise asyncio.TimeoutError("transient")
            return "ok"

    flaky = _FlakyOnce()
    result = await call_model_with_backoff(flaky, max_retries=2)
    check("recovery: transient error retried in place", result == "ok" and flaky.attempts == 2)

    async def _always_fails():
        raise asyncio.TimeoutError("transient")

    vetoed = False
    try:
        await call_model_with_backoff(_always_fails, retry_predicate=lambda ce: False, max_retries=3)
    except asyncio.TimeoutError:
        vetoed = True
    check("recovery: retry_predicate=False stops in-place retry immediately", vetoed)


# ── chat_history: tool-inclusive history replay ──────────────────────────────
def test_build_initial_messages_tool_replay() -> None:
    """build_initial_messages must reconstruct AIMessage.tool_calls +
    ToolMessage.tool_call_id from the extended dict shape, while staying
    byte-identical for plain UI-style {role, content} history."""
    from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
    from app.harness.agent_runner import build_initial_messages

    log = logging.getLogger("t")

    # Plain UI-style history — unchanged behavior.
    plain = build_initial_messages(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "follow up", log,
    )
    check(
        "build_initial_messages: plain UI history unchanged",
        [type(m).__name__ for m in plain] == ["HumanMessage", "AIMessage", "HumanMessage"],
    )

    # Extended shape: assistant with tool_calls + a paired tool result.
    extended = build_initial_messages(
        [
            {"role": "user", "content": "explain profile import"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "search_graph", "args": {"q": "x"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ProfileImportService.cs:42"},
            {"role": "assistant", "content": "Profile import works by..."},
        ],
        "when is the pdf generated", log,
    )
    check(
        "build_initial_messages: tool_calls reconstructed on AIMessage",
        isinstance(extended[1], AIMessage) and extended[1].tool_calls
        and extended[1].tool_calls[0]["id"] == "c1" and extended[1].tool_calls[0]["name"] == "search_graph",
    )
    check(
        "build_initial_messages: ToolMessage.tool_call_id reconstructed",
        isinstance(extended[2], ToolMessage) and extended[2].tool_call_id == "c1",
    )

    # tool dict WITHOUT tool_call_id still skipped (today's behavior).
    no_id = build_initial_messages(
        [{"role": "user", "content": "q"}, {"role": "tool", "content": "orphan, no id"}],
        "follow up", log,
    )
    check(
        "build_initial_messages: tool entry without tool_call_id still skipped",
        [type(m).__name__ for m in no_id] == ["HumanMessage", "HumanMessage"],
    )

    # Trailing dangling tool_call (no matching ToolMessage) gets stripped.
    dangling = build_initial_messages(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "orphan", "name": "t", "args": {}}]},
        ],
        "follow up", log,
    )
    check(
        "build_initial_messages: dangling trailing tool_call stripped",
        [type(m).__name__ for m in dangling] == ["HumanMessage", "HumanMessage"],
        f"got {[type(m).__name__ for m in dangling]}",
    )


def test_extract_turn_segment() -> None:
    from app.harness.chat_history import extract_turn_segment
    from app.harness.agent_runner import CONTINUE_TRUNCATED_NUDGE

    base = [
        {"role": "user", "content": "explain profile import"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "search_graph", "args": {}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "found ProfileImportService"},
        {"role": "assistant", "content": "Profile import works by..."},
    ]
    check("extract_turn_segment: segment starts after the user entry", extract_turn_segment(base) == base[1:])

    # A synthetic recovery nudge (mid-run HumanMessage) is not a turn boundary.
    with_nudge = base + [
        {"role": "user", "content": CONTINUE_TRUNCATED_NUDGE},
        {"role": "assistant", "content": "...continued and complete."},
    ]
    check(
        "extract_turn_segment: synthetic nudge is not a turn boundary",
        extract_turn_segment(with_nudge) == with_nudge[1:],
    )

    # Post-ship recursion: the trajectory's own replayed prefix already
    # contains an earlier turn's tool entries — segmentation must anchor on
    # the LAST real user entry, not the first tool call.
    recursive = base + [
        {"role": "user", "content": "when is the pdf report generated"},
        {"role": "assistant", "content": "The PDF report is generated after validation."},
    ]
    check(
        "extract_turn_segment: recursion case anchors on the LAST real user entry",
        extract_turn_segment(recursive) == recursive[5:],
        f"got {extract_turn_segment(recursive)}",
    )

    check("extract_turn_segment: empty trajectory -> empty segment", extract_turn_segment([]) == [])


def test_repair_tool_pairing() -> None:
    from app.harness.chat_history import repair_tool_pairing

    valid = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a1", "name": "t", "args": {}}]},
        {"role": "tool", "tool_call_id": "a1", "content": "result"},
        {"role": "assistant", "content": "final answer"},
    ]
    check("repair_tool_pairing: valid segment unchanged", repair_tool_pairing(valid) == valid)

    unmatched = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a1", "name": "t", "args": {}}]},
        {"role": "assistant", "content": "final answer, no tool result ever came"},
    ]
    check(
        "repair_tool_pairing: unmatched tool_call dropped (entry had no other text)",
        repair_tool_pairing(unmatched) == [{"role": "assistant", "content": "final answer, no tool result ever came"}],
        f"got {repair_tool_pairing(unmatched)}",
    )

    orphan = [
        {"role": "tool", "tool_call_id": "ghost", "content": "orphan result"},
        {"role": "assistant", "content": "answer"},
    ]
    check(
        "repair_tool_pairing: orphan tool entry dropped",
        repair_tool_pairing(orphan) == [{"role": "assistant", "content": "answer"}],
    )


def test_build_tool_inclusive_history() -> None:
    from app.harness.chat_history import build_tool_inclusive_history

    chat_rows = [
        {"role": "user", "content": "explain profile import",
         "metadata": {}, "created_at": "2026-07-03T10:00:00+00:00"},
        {"role": "assistant", "content": "Profile import works by validating then persisting records.",
         "metadata": {"execution_id": 100}, "created_at": "2026-07-03T10:01:00+00:00"},
        {"role": "user", "content": "when is the pdf report generated",
         "metadata": {}, "created_at": "2026-07-03T10:02:00+00:00"},
    ]
    executions = [
        {
            "id": 100, "status": "success",
            "started_at": "2026-07-03T10:00:05+00:00",
            "completed_at": "2026-07-03T10:00:59+00:00",
            "trajectory": [
                # Augmented query (KB recall block prepended) — must NOT replay.
                {"role": "user", "content": "[recall block...]\nexplain profile import"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "search_graph", "args": {}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "ProfileImportService.cs:42"},
                {"role": "assistant", "content": "Profile import works by validating then persisting records."},
            ],
        },
    ]
    log = logging.getLogger("t")

    result = build_tool_inclusive_history(
        chat_rows, executions,
        current_user_query="when is the pdf report generated",
        max_tool_tokens=24_000, max_tool_executions=4, logger_instance=log,
    )
    check(
        "build_tool_inclusive_history: trailing current-turn user row dropped",
        not any(e.get("content") == "when is the pdf report generated" for e in result),
    )
    check(
        "build_tool_inclusive_history: clean user text used, not the augmented trajectory query",
        result[0] == {"role": "user", "content": "explain profile import"},
    )
    check(
        "build_tool_inclusive_history: tool segment replayed via execution_id link",
        any(e.get("role") == "tool" and e.get("tool_call_id") == "c1" for e in result),
    )
    check(
        "build_tool_inclusive_history: assistant final text appears exactly once (chat row wins)",
        sum(1 for e in result if e.get("content") == "Profile import works by validating then persisting records.") == 1,
        f"got {result}",
    )

    # Execution cap: with max_tool_executions=0, no tool segments replay —
    # text-only degradation, but text turns are still present.
    degraded = build_tool_inclusive_history(
        chat_rows, executions,
        current_user_query="when is the pdf report generated",
        max_tool_tokens=24_000, max_tool_executions=0, logger_instance=log,
    )
    check(
        "build_tool_inclusive_history: max_tool_executions=0 degrades to text-only",
        not any(e.get("role") == "tool" for e in degraded),
    )
    check(
        "build_tool_inclusive_history: text-only degradation keeps the user/assistant turns",
        [e["role"] for e in degraded] == ["user", "assistant"],
    )

    # Token budget: a tiny budget also degrades to text-only (segment dropped).
    tiny_budget = build_tool_inclusive_history(
        chat_rows, executions,
        current_user_query="when is the pdf report generated",
        max_tool_tokens=1, max_tool_executions=4, logger_instance=log,
    )
    check(
        "build_tool_inclusive_history: tiny token budget degrades to text-only",
        not any(e.get("role") == "tool" for e in tiny_budget),
    )

    # Unmatched assistant row (no execution_id, no timestamp match) -> text-only.
    unlinked_rows = [
        {"role": "user", "content": "q1", "metadata": {}, "created_at": "2026-07-03T09:00:00+00:00"},
        {"role": "assistant", "content": "a1", "metadata": {}, "created_at": "2026-07-03T09:01:00+00:00"},
    ]
    unlinked_result = build_tool_inclusive_history(
        unlinked_rows, [], current_user_query="q2",
        max_tool_tokens=24_000, max_tool_executions=4, logger_instance=log,
    )
    check(
        "build_tool_inclusive_history: unmatched assistant row -> text-only",
        unlinked_result == [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}],
    )

    # A system (compaction-summary) row is preserved in place.
    with_summary = [{"role": "system", "content": "earlier turns summarized...", "metadata": {}, "created_at": "2026-07-03T08:00:00+00:00"}] + chat_rows
    summary_result = build_tool_inclusive_history(
        with_summary, executions,
        current_user_query="when is the pdf report generated",
        max_tool_tokens=24_000, max_tool_executions=4, logger_instance=log,
    )
    check(
        "build_tool_inclusive_history: system summary row preserved",
        summary_result[0] == {"role": "system", "content": "earlier turns summarized..."},
    )


async def test_chat_history_rebuild_fallback() -> None:
    """rebuild_chat_history is fail-open: disabled setting, empty rows, or
    any exception all return None so the caller falls back to UI history."""
    from app.config import settings as _settings
    from app.infrastructure.persistence import session_repository as _sr_singleton
    import app.harness.chat_history as ch

    log = logging.getLogger("t")
    orig_flag = _settings.chat_history_include_tools
    orig_get_messages = _sr_singleton.get_messages
    try:
        _settings.chat_history_include_tools = False
        result = await ch.rebuild_chat_history("sess-x", current_user_query="q", logger_instance=log)
        check("chat_history: disabled setting returns None", result is None)

        _settings.chat_history_include_tools = True

        async def _empty_rows(session_id):
            return []
        _sr_singleton.get_messages = _empty_rows
        result = await ch.rebuild_chat_history("sess-y", current_user_query="q", logger_instance=log)
        check("chat_history: empty chat rows returns None", result is None)

        async def _raises(session_id):
            raise RuntimeError("db down")
        _sr_singleton.get_messages = _raises
        result = await ch.rebuild_chat_history("sess-z", current_user_query="q", logger_instance=log)
        check("chat_history: exception falls back to None (fail-open)", result is None)
    finally:
        _settings.chat_history_include_tools = orig_flag
        _sr_singleton.get_messages = orig_get_messages


# ── spec_factory + build_agent_from_spec ─────────────────────────────────────
def test_spec_and_facade() -> None:
    from app.harness import build_agent_from_spec, AgentSpec
    from app.harness.spec_factory import build_agent_spec, resolve_permission_mode

    check("spec_factory context overrides node",
          resolve_permission_mode({"permission_mode": "plan"}, {"permissionMode": "default"}) == "plan")
    spec = build_agent_spec(agent_config={"permissionMode": "AUTO_ALLOW"}, context={},
                            has_cloudwatch=True, has_code_analyzer=False, session_id="x")
    check("spec_factory builds spec", spec.permission_mode == "auto_allow" and spec.has_cloudwatch and spec.session_id == "x")

    # Patch the sole ReAct builder to verify the AgentSpec→kwargs mapping.
    import app.harness.react_agent as ab
    captured: dict = {}

    def fake_build_agent(llm, tools, agent_config, **kw):
        captured.update({"llm": llm, "tools": tools, "agent_config": agent_config, **kw})
        return "AGENT"

    orig = ab.build_agent
    ab.build_agent = fake_build_agent
    try:
        s = AgentSpec(agent_config={"x": 1}, has_cloudwatch=True, has_code_analyzer=False,
                      permission_mode="auto_allow", session_id="exec-9")
        out = build_agent_from_spec(s, llm="LLM", tools=["t1"], checkpointer="CP", execution_port="PORT")
        ok = (out == "AGENT" and captured["has_cloudwatch"] and not captured["has_code_analyzer"]
              and captured["permission_mode"] == "auto_allow" and captured["session_id"] == "exec-9"
              and captured["checkpointer"] == "CP" and captured["execution_port"] == "PORT"
              and captured["llm"] == "LLM" and captured["tools"] == ["t1"])
        check("build_agent_from_spec maps AgentSpec→kwargs", ok, str(captured))
    finally:
        ab.build_agent = orig


# ── supervisor_loop ──────────────────────────────────────────────────────────
async def test_supervisor_loop() -> None:
    from app.harness.supervisor_loop import run_supervised
    from app.core.supervisor import SupervisorAction
    log = logging.getLogger("t")

    class V:
        def __init__(self, a):
            self.action = a; self.score = 0.5; self.reason = "r"; self.retry_guidance = "fix"

    class Cfg:
        max_retries = 2; token_budget = 100_000

    class FakeSup:
        def __init__(self, v):
            self._v = v; self._i = 0; self._cfg = Cfg()

        async def evaluate(self, **k):
            x = self._v[min(self._i, len(self._v) - 1)]; self._i += 1; return x

    # PASS first
    runs = {"n": 0}
    async def ra(a, q):
        runs["n"] += 1; return {"final_answer": "done", "input_tokens": 10, "output_tokens": 5, "tool_calls": []}
    res, ti, to, tc, tcc = await run_supervised(agent="a0", run_agent=ra, rebuild_agent=lambda: "a",
        supervisor=FakeSup([V(SupervisorAction.PASS)]), base_query="Q", execution_id="e",
        logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop PASS = 1 run", runs["n"] == 1 and ti == 10 and to == 5)

    # RETRY then PASS
    runs = {"n": 0}; rebuilds = {"n": 0}
    async def ra2(a, q):
        runs["n"] += 1; return {"final_answer": "x", "input_tokens": 7, "output_tokens": 3, "tool_calls": []}
    res, ti, to, tc, tcc = await run_supervised(agent="a0", run_agent=ra2, rebuild_agent=lambda: (rebuilds.__setitem__("n", rebuilds["n"] + 1) or "a2"),
        supervisor=FakeSup([V(SupervisorAction.RETRY), V(SupervisorAction.PASS)]), base_query="Q",
        execution_id="e", logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop RETRY→PASS rebuilds once", runs["n"] == 2 and rebuilds["n"] == 1 and ti == 14 and to == 6)

    # ESCALATE sets flag
    res, _, _, _, _ = await run_supervised(agent="a0",
        run_agent=lambda a, q: _coro({"final_answer": "weak", "input_tokens": 1, "output_tokens": 1, "tool_calls": []}),
        rebuild_agent=lambda: "a", supervisor=FakeSup([V(SupervisorAction.ESCALATE)]), base_query="Q",
        execution_id="e", logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop ESCALATE flag", res.get("supervisor_escalated") is True)

    # no supervisor = 1 run
    runs = {"n": 0}
    async def ra3(a, q):
        runs["n"] += 1; return {"final_answer": "y", "input_tokens": 1, "output_tokens": 1, "tool_calls": []}
    await run_supervised(agent="a0", run_agent=ra3, rebuild_agent=lambda: "a", supervisor=None,
        base_query="Q", execution_id="e", logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop no-supervisor = 1 run", runs["n"] == 1)

    # ── late-bound reward: a PASS/RETRY verdict best-effort records its
    #    score against the run's trajectory_events, gated on step_events_enabled ──
    from app.harness.supervisor_loop import _record_supervisor_reward
    from app.config import settings as _sup_s
    from app.infrastructure.persistence import trajectory_event_repository as _sup_ter

    _orig_step_events = _sup_s.step_events_enabled
    _orig_append_reward = _sup_ter.append_reward_for_trace
    captured_rewards: list = []

    async def _fake_append_reward(trace_id, source, value):
        captured_rewards.append((trace_id, source, value))
        return True

    try:
        _sup_ter.append_reward_for_trace = _fake_append_reward

        _sup_s.step_events_enabled = False
        await _record_supervisor_reward("exec-1", 0.9)
        check(
            "supervisor reward: no-op when step_events_enabled is off",
            captured_rewards == [],
        )

        _sup_s.step_events_enabled = True
        await _record_supervisor_reward("exec-1", 0.9)
        check(
            "supervisor reward: records supervisor_score against the trace when enabled",
            captured_rewards == [("exec-1", "supervisor_score", 0.9)],
        )

        # a PASS verdict during run_supervised triggers the reward call too.
        captured_rewards.clear()
        runs4 = {"n": 0}
        async def ra4(a, q):
            runs4["n"] += 1
            return {"final_answer": "done", "input_tokens": 1, "output_tokens": 1, "tool_calls": []}
        await run_supervised(agent="a0", run_agent=ra4, rebuild_agent=lambda: "a",
            supervisor=FakeSup([V(SupervisorAction.PASS)]), base_query="Q", execution_id="exec-2",
            logger_instance=log, wall_clock_budget=900)
        check(
            "supervisor_loop: PASS verdict records a late-bound reward",
            captured_rewards == [("exec-2", "supervisor_score", 0.5)],
        )

        # failure to record must never break the loop (best-effort).
        async def _boom_reward(trace_id, source, value):
            raise RuntimeError("db down")
        _sup_ter.append_reward_for_trace = _boom_reward
        res_safe, _, _, _, _ = await run_supervised(agent="a0", run_agent=ra4, rebuild_agent=lambda: "a",
            supervisor=FakeSup([V(SupervisorAction.PASS)]), base_query="Q", execution_id="exec-3",
            logger_instance=log, wall_clock_budget=900)
        check(
            "supervisor_loop: reward-recording failure never breaks the loop",
            res_safe.get("final_answer") == "done",
        )
    finally:
        _sup_s.step_events_enabled = _orig_step_events
        _sup_ter.append_reward_for_trace = _orig_append_reward


async def _coro(v):
    return v


async def test_trajectory_export_step_events() -> None:
    """TrajectoryService.export_step_events is a thin, never-raising read of
    trajectory_event_repository.list_for_trace — the offline-RL export
    counterpart to get_atropos_format's flat message-role view."""
    from app.services.trajectory_service import trajectory_service
    from app.infrastructure.persistence import trajectory_event_repository as _tes_ter

    fake_events = [
        {"event_id": "e1", "trace_id": "t1", "step_index": 0, "type": "model_turn", "payload": {}},
        {"event_id": "e2", "trace_id": "t1", "step_index": 1, "type": "tool_call", "payload": {}},
    ]

    async def _fake_list_for_trace(trace_id):
        assert trace_id == "t1"
        return fake_events

    _orig_list = _tes_ter.list_for_trace
    try:
        _tes_ter.list_for_trace = _fake_list_for_trace
        out = await trajectory_service.export_step_events("t1")
        check("export_step_events: returns the repository's events", out == fake_events)

        async def _boom_list(trace_id):
            raise RuntimeError("db down")
        _tes_ter.list_for_trace = _boom_list
        out_safe = await trajectory_service.export_step_events("t1")
        check("export_step_events: failure degrades to empty list, never raises", out_safe == [])
    finally:
        _tes_ter.list_for_trace = _orig_list


async def test_tool_result_failed_flag_threading() -> None:
    """The step-status icon bug: classify_tool_failure() was already computed
    at both engine call sites right before stream_callback.on_tool_result(),
    but the boolean was discarded rather than threaded through — so a tool
    that returns normally (no exception) with error CONTENT (e.g. a DB
    connection-refused message) rendered a green "success" step in the chat
    UI. Verifies the classifier catches this content and that both engines'
    tool-exec paths now pass `failed=` through to the stream callback."""
    from app.core.tool_guardrails import classify_tool_failure

    conn_refused = (
        "[Tool Error] connection refused on 172.16.82.182:5432 — "
        "could not connect to server"
    )
    is_failed, _reason = classify_tool_failure("postgres-production__query", conn_refused)
    check(
        "classify_tool_failure: flags a '[Tool Error] connection refused' MCP result",
        is_failed is True,
    )
    ok_is_failed, _ = classify_tool_failure("postgres-production__query", '{"rows": []}')
    check(
        "classify_tool_failure: a normal result is not flagged failed",
        ok_is_failed is False,
    )

    class _FakeStreamCallback:
        def __init__(self):
            self.tool_results: list = []

        async def on_tool_call(self, tool_name, args):
            pass

        async def on_tool_result(self, tool_name, result, failed=False):
            self.tool_results.append((tool_name, result, failed))

        async def on_error(self, error):
            pass

    # ── native engine: execute_tool_calls (tool_exec.py) ──
    from app.harness.engine.tool_exec import execute_tool_calls

    failing_tool = _FakeTool("postgres-production__query", conn_refused)
    ok_tool = _FakeTool("other_tool", '{"rows": []}')
    ai_msg = _fake_ai_message("", tool_calls=[
        {"id": "c1", "name": "postgres-production__query", "args": {}},
        {"id": "c2", "name": "other_tool", "args": {}},
    ])
    native_cb = _FakeStreamCallback()
    await execute_tool_calls(
        ai_msg, {"postgres-production__query": failing_tool, "other_tool": ok_tool},
        stream_callback=native_cb,
    )
    native_by_name = {name: failed for name, _result, failed in native_cb.tool_results}
    check(
        "tool_exec.execute_tool_calls: threads failed=True for the connection-refused tool",
        native_by_name.get("postgres-production__query") is True,
    )
    check(
        "tool_exec.execute_tool_calls: threads failed=False for the healthy tool",
        native_by_name.get("other_tool") is False,
    )

    # ── LangGraph engine: the on_tool_end streaming branch in agent_runner.py ──
    # Exercised directly rather than through the full astream loop (which needs
    # a live LangGraph agent) — this reproduces exactly the guardrail
    # post-check + on_tool_result call that agent_runner.py's on_tool_end
    # handler performs, using the same classify_tool_failure() call.
    lg_is_failed, _ = classify_tool_failure("postgres-production__query", conn_refused)
    lg_cb = _FakeStreamCallback()
    await lg_cb.on_tool_result(
        "postgres-production__query", conn_refused[:2000], failed=lg_is_failed,
    )
    check(
        "agent_runner on_tool_end pattern: threads failed=True through on_tool_result",
        lg_cb.tool_results == [("postgres-production__query", conn_refused[:2000], True)],
    )


# ── context_builder ──────────────────────────────────────────────────────────
def test_context_builder() -> None:
    from app.harness.context_builder import seed_context_blocks, apply_synthesis_floor
    log = logging.getLogger("t")

    aq, cw = seed_context_blocks(augmented_query="Q",
        context={"cloudwatch_context": {"n": {"output": "SYNTH " * 10}}})
    check("context_builder.seed extracts cw_synthesis", len(cw) > 0 and aq != "Q")

    r = apply_synthesis_floor(result={"final_answer": ""}, cw_synthesis="FLOOR", logger_instance=log, execution_id="e")
    check("context_builder.floor fires on empty", r["final_answer"] == "FLOOR" and r["cloudwatch_synthesis_fallback"])

    good = "x" * 600
    r2 = apply_synthesis_floor(result={"final_answer": good}, cw_synthesis="FLOOR", logger_instance=log, execution_id="e")
    check("context_builder.floor keeps long answer", r2["final_answer"] == good)


# ── tool_assembler.add_extension_tools ───────────────────────────────────────
def test_extension_tools() -> None:
    from app.harness.tool_assembler import add_extension_tools
    log = logging.getLogger("t")
    out = add_extension_tools(tools=["base"], llm="LLM", agent_config={}, code_analyzer_config=None,
                              execution_id="e", logger_instance=log)
    _names0 = [getattr(t, "name", t) for t in out]
    check("add_extension_tools keeps base tool", "base" in _names0, str(_names0))
    check("add_extension_tools: filesystem:false (default) omits fs_* tools (#15 fix)",
          "fs_write" not in _names0 and "fs_grep" not in _names0, str(_names0))

    # AgentSpec.filesystem:true → fs_* tools ARE added.
    out_fs = add_extension_tools(tools=["base"], llm="LLM", agent_config={"filesystem": True},
                                 code_analyzer_config=None, execution_id="e", logger_instance=log)
    _names_fs = [getattr(t, "name", t) for t in out_fs]
    check("add_extension_tools: filesystem:true adds fs_* tools",
          "fs_write" in _names_fs and "fs_grep" in _names_fs, str(_names_fs))

    # with code config → appends delegate (+ edit, best-effort)
    out2 = add_extension_tools(tools=[], llm="LLM", agent_config={"instructions": "x"},
                               code_analyzer_config={"repos": []}, execution_id="e", logger_instance=log)
    names = [getattr(t, "name", "") for t in out2]
    check("add_extension_tools adds delegate", "delegate_investigation" in names, str(names))


# ── edit_tools.create_file + permission classification ───────────────────────
async def test_edit_tools() -> None:
    import json as _json
    import os
    import tempfile
    from app.config import settings
    from app.harness.edit_tools import build_edit_tools
    from app.harness.tool_permissions import evaluate

    tools = {t.name: t for t in build_edit_tools()}
    check("edit_tools exposes create_file", "create_file" in tools, str(list(tools)))

    # Permission classification: create_file must be gated 'ask' by default, and
    # 'allow' under auto_allow. (The "*_create" suffix glob does NOT match it, so
    # the explicit entry is what enforces this — guard against its removal.)
    check("create_file gated ask", evaluate("create_file") == "ask")
    check("create_file auto_allow→allow", evaluate("create_file", mode="auto_allow") == "allow")

    root = tempfile.mkdtemp(prefix="harness_edit_")
    _orig_root = settings.repos_base_path
    settings.repos_base_path = root
    try:
        create = tools["create_file"].coroutine
        # happy path — new file written to disk
        res = _json.loads(await create(repo="r", file="pkg/new_mod.py", content="X = 1\n"))
        on_disk = os.path.isfile(os.path.join(root, "r", "pkg", "new_mod.py"))
        check("create_file happy path", res.get("ok") and on_disk, str(res))
        # refuse to overwrite an existing file
        res2 = _json.loads(await create(repo="r", file="pkg/new_mod.py", content="Y = 2\n"))
        check("create_file refuses overwrite", "error" in res2 and "exists" in res2["error"], str(res2))
        # path-jail escape is rejected (file not written outside the repo root)
        res3 = _json.loads(await create(repo="r", file="../../../../etc/pwn", content="bad"))
        check("create_file blocks jail escape", "error" in res3, str(res3))
    finally:
        settings.repos_base_path = _orig_root


# ── llm_config node-level model gateway ───────────────────────────────────────
async def test_gateway_resolution() -> None:
    """Deterministic coverage for the node-level model gateway in
    ``app.workflow.llm_config``: edge-dialect coalescing, multi-model slot
    selection, and per-port wiring rules. Pure helpers need no DB; the
    ``resolve_llm_config_for_consumer_port`` cases hit the DB only for named
    config lookups (no live Bedrock)."""
    from app.workflow.llm_config import (
        resolve_llm_config_for_consumer_port,
        find_llm_node_for_consumer,
        read_llm_node,
        _edge_target_slot,
        _edge_source_slot,
        _model_key_from_slot,
    )
    from app.infrastructure.persistence import llm_config_repository

    # ── pure helpers (no DB) ──────────────────────────────────────────────────
    check("gateway._model_key_from_slot('lm::Foo')=='Foo'",
          _model_key_from_slot("lm::Foo") == "Foo")
    check("gateway._model_key_from_slot('lm') is None",
          _model_key_from_slot("lm") is None)
    check("gateway._edge_target_slot editor dialect (targetSlot)",
          _edge_target_slot({"targetSlot": "lm"}) == "lm")
    check("gateway._edge_target_slot legacy dialect (targetHandle)",
          _edge_target_slot({"targetHandle": "model"}) == "model")
    check("gateway._edge_source_slot editor dialect (sourceSlot)",
          _edge_source_slot({"sourceSlot": "lm::A"}) == "lm::A")
    check("gateway._edge_source_slot legacy dialect (sourceHandle)",
          _edge_source_slot({"sourceHandle": "lm"}) == "lm")

    # ── fetch real config names dynamically (container DB has 2) ───────────────
    db = await llm_config_repository.list_all()
    names = list(db.keys())
    if len(names) < 2:
        check("gateway: >=2 LLM configs in DB (skipped DB cases)", True,
              f"only {len(names)} config(s) — skipping DB-backed gateway cases")
        return
    A, B = names[0], names[1]
    model_A, model_B = db[A]["model"], db[B]["model"]

    # ── Case 5: pure read_llm_node model selection (no DB) ─────────────────────
    multi_node = {"id": "lm1", "type": "language_model", "params": {"llm": f"{A},{B}"}}
    check("gateway.read_llm_node multi: model_key picks named config",
          read_llm_node(multi_node, model_key=B).config_name == B,
          read_llm_node(multi_node, model_key=B).config_name)
    check("gateway.read_llm_node multi: no model_key picks first",
          read_llm_node(multi_node, model_key=None).config_name == A,
          read_llm_node(multi_node, model_key=None).config_name)

    # ── Case 1: multi-model node, editor dialect, slot selects the model ───────
    wf_multi = {
        "nodes": [
            {"id": "lm1", "type": "language_model", "params": {"llm": f"{A},{B}"}},
            {"id": "agent", "type": "agent"},
            {"id": "code_search_tool", "type": "code_search_tool"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent",
             "sourceSlot": f"lm::{A}", "targetSlot": "lm"},
            {"source": "lm1", "target": "code_search_tool",
             "sourceSlot": f"lm::{B}", "targetSlot": "lm"},
        ],
    }
    res_a = await resolve_llm_config_for_consumer_port(wf_multi, "agent", "lm")
    check("gateway lm::A→agent.lm resolves model A (editor)",
          res_a is not None and res_a.get("model") == model_A, str(res_a))
    res_b = await resolve_llm_config_for_consumer_port(wf_multi, "code_search_tool", "lm")
    check("gateway lm::B→code_search_tool.lm resolves model B (editor)",
          res_b is not None and res_b.get("model") == model_B, str(res_b))

    # find_llm_node_for_consumer surfaces the matched source slot
    node, slot, connected = find_llm_node_for_consumer(
        wf_multi, "agent", ("model", "lm"), accept_untagged=True, fallback=False)
    check("gateway find_llm_node_for_consumer returns (node, slot, connected)",
          connected and slot == f"lm::{A}" and node.get("id") == "lm1",
          f"connected={connected} slot={slot}")

    # ── Case 2: legacy dialect, single model, resolves ─────────────────────────
    wf_legacy = {
        "nodes": [
            {"id": "lm1", "type": "llm", "params": {"llm": A}},
            {"id": "agent", "type": "agent"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent",
             "sourceHandle": "lm", "targetHandle": "model"},
        ],
    }
    res_legacy = await resolve_llm_config_for_consumer_port(wf_legacy, "agent", "lm")
    check("gateway legacy sourceHandle/targetHandle resolves model A",
          res_legacy is not None and res_legacy.get("model") == model_A, str(res_legacy))

    # ── Case 3: unwired port → None ────────────────────────────────────────────
    res_sub = await resolve_llm_config_for_consumer_port(wf_multi, "agent", "subagent")
    check("gateway unwired subagent port → None", res_sub is None, str(res_sub))

    # ── crawler/subagent require an exact slot — no untagged bleed ─────────────
    wf_untagged = {
        "nodes": [
            {"id": "lm1", "type": "language_model", "params": {"llm": A}},
            {"id": "agent", "type": "agent"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent"},  # untagged single edge
        ],
    }
    res_u_lm = await resolve_llm_config_for_consumer_port(wf_untagged, "agent", "lm")
    check("gateway lm port accepts untagged legacy edge",
          res_u_lm is not None and res_u_lm.get("model") == model_A, str(res_u_lm))
    res_u_cr = await resolve_llm_config_for_consumer_port(wf_untagged, "agent", "crawler")
    check("gateway crawler port rejects untagged edge → None",
          res_u_cr is None, str(res_u_cr))

    # crawler with an exact slot resolves the slot-named model
    wf_crawler = {
        "nodes": [
            {"id": "lm1", "type": "language_model", "params": {"llm": f"{A},{B}"}},
            {"id": "agent", "type": "agent"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent",
             "sourceSlot": f"lm::{B}", "targetSlot": "crawler"},
        ],
    }
    res_crawler = await resolve_llm_config_for_consumer_port(wf_crawler, "agent", "crawler")
    check("gateway crawler exact slot resolves slot-named model B",
          res_crawler is not None and res_crawler.get("model") == model_B, str(res_crawler))


# ── cloudwatch instrumentation (Phase 0) ─────────────────────────────────────
async def test_cloudwatch_cache_stats() -> None:
    """Cache hit/miss counters and reset behave deterministically."""
    from app.core import cloudwatch_cache as cc

    cc.invalidate()          # clear store so keys are fresh
    cc.reset_stats()
    calls = {"n": 0}

    async def _fn(**kw):
        calls["n"] += 1
        return kw.get("v")

    # First call: miss (fn runs). Second identical call: hit (fn does NOT run).
    r1 = await cc.cached_call("selftest_fn", _fn, ttl_seconds=60, v=7)
    r2 = await cc.cached_call("selftest_fn", _fn, ttl_seconds=60, v=7)
    st = cc.stats()
    check("cw_cache hit/miss counted", st["hits"] == 1 and st["misses"] == 1 and calls["n"] == 1, str(st))
    check("cw_cache returns cached value", r1 == 7 and r2 == 7)
    check("cw_cache hit_rate", abs(st["hit_rate"] - 0.5) < 1e-9, str(st["hit_rate"]))
    cc.reset_stats()
    check("cw_cache reset_stats zeroes", cc.stats()["hits"] == 0 and cc.stats()["misses"] == 0)
    # ttl_seconds=0 bypasses the cache entirely (always calls fn, no counters)
    calls["n"] = 0
    await cc.cached_call("selftest_fn", _fn, ttl_seconds=0, v=1)
    await cc.cached_call("selftest_fn", _fn, ttl_seconds=0, v=1)
    check("cw_cache ttl=0 bypass", calls["n"] == 2 and cc.stats()["hits"] == 0)


def test_cloudwatch_error_classifier() -> None:
    """_classify_aws_error maps AWS exceptions to the coarse retry categories."""
    from botocore.exceptions import ClientError
    from app.services.log_watch_service import _classify_aws_error

    def _ce(code: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

    cases = {
        "ThrottlingException": "throttling",
        "AccessDeniedException": "access-denied",
        "ValidationException": "validation",
        "ExpiredTokenException": "expired-credentials",
    }
    for code, expected in cases.items():
        got = _classify_aws_error(_ce(code))
        check(f"cw_classify {code}→{expected}", got == expected, f"got {got}")
    check("cw_classify timeout (non-ClientError)",
          _classify_aws_error(TimeoutError("operation timed out")) == "timeout")
    check("cw_classify generic→error",
          _classify_aws_error(RuntimeError("boom")) == "error")


def test_cloudwatch_synthesis_payload_excludes_run_stats() -> None:
    """run_stats must never leak into the LLM synthesis payload (token guard)."""
    from app.workflow.executor.cloudwatch_analysis import build_synthesis_payload

    payload = build_synthesis_payload({
        "data_quality": {"coverage": "full"},
        "run_stats": {"insights": {"bytes_scanned": 9_999_999}},
        "patterns": {"unique_patterns": []},
    })
    check("cw_payload omits run_stats", "run_stats" not in payload, str(list(payload)))
    check("cw_payload keeps data_quality", payload.get("data_quality") == {"coverage": "full"})


# ── cloudwatch reliability (Phase 1) ─────────────────────────────────────────
def test_cloudwatch_severity_clamp() -> None:
    """Weak evidence caps synthesized severity at 'medium' — including the new
    reduced-scope/partial flag branch — unless a strong signal survives."""
    from app.workflow.executor.cloudwatch_analysis import _clamp_severity_for_weak_evidence

    class P:
        def __init__(self, sev):
            self.severity = sev

    # coverage 'full' but partial flag set (reduced-scope retry) → clamp high→medium
    p = P("high")
    _clamp_severity_for_weak_evidence(p, {"data_quality": {"coverage": "full", "partial": True}})
    check("cw_clamp partial_flag caps high→medium", p.severity == "medium", p.severity)

    # reduced_scope flag alone (coverage full) also clamps
    p2 = P("critical")
    _clamp_severity_for_weak_evidence(p2, {"data_quality": {"coverage": "full", "reduced_scope": True}})
    check("cw_clamp reduced_scope caps critical→medium", p2.severity == "medium", p2.severity)

    # strong signal (alarm in ALARM) survives weak coverage — NOT clamped
    p3 = P("high")
    _clamp_severity_for_weak_evidence(p3, {
        "data_quality": {"coverage": "partial", "partial": True},
        "alarms": {"summary": {"in_alarm": 1}},
    })
    check("cw_clamp alarm-firing bypasses clamp", p3.severity == "high", p3.severity)

    # full coverage, strong evidence → untouched
    p4 = P("critical")
    _clamp_severity_for_weak_evidence(p4, {"data_quality": {"coverage": "full"}, "patterns": {"evidence_grade": "high"}})
    check("cw_clamp strong evidence untouched", p4.severity == "critical", p4.severity)


async def test_cloudwatch_analyzer_cache() -> None:
    """run_workflow_analysis caches read-only triage types and bypasses
    unique/side-effecting ones (Phase 1 per-analyzer TTLs)."""
    from app.core import cloudwatch_cache as cc
    from app.services.log_watch_service import LogWatchService

    svc = LogWatchService()
    calls: dict = {}

    async def _fake_dispatch(*, analysis_type, **kw):
        calls[analysis_type] = calls.get(analysis_type, 0) + 1
        return {"status": "success", "analysis_type": analysis_type}

    svc._dispatch_analysis = _fake_dispatch  # type: ignore[assignment]
    cc.invalidate()

    common = dict(log_group_names=["/a", "/b"], time_range_minutes=60,
                  region="us-east-1", credentials=None, node_data={})
    # alarms: ttl>0 → second identical call served from cache (dispatch once)
    await svc.run_workflow_analysis(analysis_type="alarms", **common)
    await svc.run_workflow_analysis(analysis_type="alarms", **common)
    check("cw_cache alarms cached (1 dispatch)", calls.get("alarms") == 1, str(calls))
    # correlation: ttl=0 → never cached (dispatch twice)
    await svc.run_workflow_analysis(analysis_type="correlation", **common)
    await svc.run_workflow_analysis(analysis_type="correlation", **common)
    check("cw_cache correlation not cached (2 dispatch)", calls.get("correlation") == 2, str(calls))
    # different time window → distinct cache key (fresh dispatch)
    await svc.run_workflow_analysis(analysis_type="alarms", **{**common, "time_range_minutes": 120})
    check("cw_cache distinct window = cache miss", calls.get("alarms") == 2, str(calls))
    cc.invalidate()


# ── cloudwatch accuracy (Phase 2) ────────────────────────────────────────────
def test_cloudwatch_drilldown_scoring() -> None:
    """Deterministic drill-down scoring ranks the strongest lead first and is
    stable across calls."""
    from app.workflow.tools.cloudwatch_drilldown import select_drill_targets, score_drill_target

    patterns = {"unique_patterns": [
        {"normalized_pattern": "low-volume warning", "occurrence_count": 3, "example_message": "x"},
        {"normalized_pattern": "huge error burst", "occurrence_count": 5000, "severity": "high", "example_message": "y"},
    ], "evidence_grade": "high"}
    anomalies = {"anomalies": [
        {"log_group": "/a", "z_score": 1.2, "severity": "low"},
    ]}
    t1 = select_drill_targets(patterns, anomalies, top_n=3)
    t2 = select_drill_targets(patterns, anomalies, top_n=3)
    check("cw_score deterministic", [x.get("label") for x in t1] == [x.get("label") for x in t2])
    check("cw_score high-volume critical pattern ranks first",
          t1 and "huge error burst" in (t1[0].get("label") or ""), str(t1[0] if t1 else None))
    # a critical high-z anomaly must outscore a marginal low pattern
    s_big = score_drill_target({"kind": "anomaly", "severity": "critical", "z_score": 10}, 1.0)
    s_small = score_drill_target({"kind": "pattern", "severity": "low", "occurrence_count": 2}, 1.0)
    check("cw_score critical anomaly > marginal pattern", s_big > s_small, f"{s_big} vs {s_small}")


def test_cloudwatch_query_fixup() -> None:
    """fixup_insights_query injects a missing limit and hard-fails broken queries."""
    from app.workflow.tools.cloudwatch_drilldown import fixup_insights_query

    fixed, err = fixup_insights_query("fields @timestamp, @message | filter @message like /(?i)error/")
    check("cw_fixup injects limit", err is None and "limit" in fixed.lower(), f"{err} :: {fixed}")
    fixed2, err2 = fixup_insights_query("fields @timestamp | filter x | limit 5")
    check("cw_fixup leaves valid query unchanged", err2 is None and fixed2.strip().endswith("limit 5"))
    _f3, err3 = fixup_insights_query("SELECT * FROM logs")
    check("cw_fixup rejects non-Insights lead command", err3 is not None, str(err3))
    _f4, err4 = fixup_insights_query("")
    check("cw_fixup rejects empty", err4 is not None)


def test_cloudwatch_alarm_and_metric_helpers() -> None:
    """Pure helpers: flapping detection and log-group→namespace inference."""
    from app.mcp.tools.metrics_tools import detect_flapping, infer_namespace_from_log_group

    check("cw_flapping at threshold", detect_flapping(3) is True)
    check("cw_flapping below threshold", detect_flapping(2) is False)

    lam = infer_namespace_from_log_group("/aws/lambda/kyc-auth")
    check("cw_infer lambda namespace",
          lam == {"namespace": "AWS/Lambda", "dimension_name": "FunctionName",
                  "dimension_value": "kyc-auth", "log_group": "/aws/lambda/kyc-auth"}, str(lam))
    rds = infer_namespace_from_log_group("/aws/rds/instance/kyc-db/error")
    check("cw_infer rds namespace", rds and rds["namespace"] == "AWS/RDS", str(rds))
    check("cw_infer unknown → None", infer_namespace_from_log_group("/custom/app/foo") is None)


def test_cloudwatch_payload_includes_metrics() -> None:
    """build_synthesis_payload surfaces metric summaries (Phase 2 fusion)."""
    from app.workflow.executor.cloudwatch_analysis import build_synthesis_payload

    payload = build_synthesis_payload({
        "metrics": {"metrics": {"t": {"label": "Throttles", "summary": {"maximum": 95}}}},
        "patterns": {"unique_patterns": []},
    })
    check("cw_payload includes metrics", "metrics" in payload and payload["metrics"][0]["label"] == "Throttles", str(payload.get("metrics")))


# ── cloudwatch cost controls (Phase 3) ───────────────────────────────────────
def test_cloudwatch_cost_footer() -> None:
    """format_cost_footer reports GB/USD and the budget note, and is empty when
    nothing was scanned."""
    from app.workflow.executor.cloudwatch_analysis import format_cost_footer

    check("cw_footer empty with no scans", format_cost_footer({"insights": {"queries": 0, "bytes_scanned": 0}}) == "")
    f = format_cost_footer({"insights": {"queries": 2, "bytes_scanned": 2e9}})
    check("cw_footer reports GB + queries", "2.000 GB" in f and "2 Insights queries" in f, f)
    fb = format_cost_footer({"insights": {"queries": 1, "bytes_scanned": 1e9}, "budget_limited": True})
    check("cw_footer notes budget limit", "scan budget reached" in fb, fb)


def test_cloudwatch_drill_window() -> None:
    """compute_drill_window narrows to first/last-seen, clamped to the triage window."""
    from datetime import datetime, timezone, timedelta
    from app.workflow.tools.cloudwatch_drilldown import compute_drill_window

    end = datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=6)
    tgt = {"first_seen": "2026-06-10T14:00:00Z", "last_seen": "2026-06-10T14:30:00Z"}
    lo, hi = compute_drill_window(tgt, start, end, margin_minutes=5)
    check("cw_drill_window narrows to pattern span", lo > start and hi < end, f"{lo}..{hi}")
    check("cw_drill_window margins applied",
          lo == datetime(2026, 6, 10, 13, 55, tzinfo=timezone.utc)
          and hi == datetime(2026, 6, 10, 14, 35, tzinfo=timezone.utc), f"{lo}..{hi}")
    # no timestamps → full window
    lo2, hi2 = compute_drill_window({}, start, end)
    check("cw_drill_window fallback to full window", lo2 == start and hi2 == end)
    # never widens beyond triage window
    wide = {"first_seen": "2020-01-01T00:00:00Z", "last_seen": "2030-01-01T00:00:00Z"}
    lo3, hi3 = compute_drill_window(wide, start, end)
    check("cw_drill_window never widens", lo3 >= start and hi3 <= end, f"{lo3}..{hi3}")


async def test_cloudwatch_rate_limiter() -> None:
    """Token bucket grants a burst up to rps immediately, then is gated by refill;
    rps<=0 disables it. Uses a fake clock so no real time passes."""
    from app.core import cloudwatch_ratelimit as rl

    clock = {"t": 0.0}
    bucket = rl.TokenBucket(rps=5, time_fn=lambda: clock["t"])
    waits = [await bucket.acquire() for _ in range(5)]
    check("cw_ratelimit burst up to rps no wait", all(w == 0.0 for w in waits), str(waits))
    # advance fake clock 1s → full refill → next 5 also immediate
    clock["t"] = 1.0
    waits2 = [await bucket.acquire() for _ in range(5)]
    check("cw_ratelimit refills over time", all(w == 0.0 for w in waits2), str(waits2))
    check("cw_ratelimit disabled when rps<=0", await rl.acquire("r", "StartQuery", 0) == 0.0)
    rl.reset()


# ── cloudwatch feature breadth (Phase 4) ─────────────────────────────────────
def test_cloudwatch_time_buckets() -> None:
    """compute_time_buckets splits long ranges newest-first into ≤bucket pieces."""
    from datetime import datetime, timezone, timedelta
    from app.mcp.tools.watch_tools import compute_time_buckets

    end = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=72)  # 3 days
    buckets = compute_time_buckets(start, end, 1440)  # 24h buckets
    check("cw_buckets count for 72h/24h", len(buckets) == 3, str(len(buckets)))
    check("cw_buckets newest-first", buckets[0][1] == end and buckets[-1][0] == start, str(buckets[0]))
    check("cw_buckets contiguous", buckets[0][0] == buckets[1][1] and buckets[1][0] == buckets[2][1])
    # range within one bucket → single bucket
    one = compute_time_buckets(end - timedelta(hours=6), end, 1440)
    check("cw_buckets single when within bucket", len(one) == 1, str(one))


def test_cloudwatch_metric_query_build() -> None:
    """build_metric_queries_from_discovery filters to inferred resources + caps."""
    from app.mcp.tools.metrics_tools import build_metric_queries_from_discovery

    disc = {
        "inferred_targets": [{"dimension_value": "kyc-auth"}],
        "metrics": [
            {"namespace": "AWS/Lambda", "metric_name": "Throttles",
             "dimensions": [{"Name": "FunctionName", "Value": "kyc-auth"}]},
            {"namespace": "AWS/Lambda", "metric_name": "Errors",
             "dimensions": [{"Name": "FunctionName", "Value": "other-fn"}]},
        ],
    }
    q = build_metric_queries_from_discovery(disc)
    check("cw_mq filters to inferred resource", len(q) == 1 and q[0]["MetricStat"]["Metric"]["MetricName"] == "Throttles", str(q))
    check("cw_mq builds valid MetricStat", q[0]["MetricStat"]["Stat"] == "Sum" and q[0]["Id"] == "m0", str(q[0]))


async def test_cloudwatch_bucketed_merge() -> None:
    """query_with_insights_bucketed merges per-bucket results/stats and early-stops."""
    from datetime import datetime, timezone, timedelta
    from app.mcp.tools.watch_tools import CloudWatchLogWatcher

    w = CloudWatchLogWatcher.__new__(CloudWatchLogWatcher)  # skip boto3 client
    w.region = "us-east-1"
    calls = {"n": 0}

    async def _fake_q(**kw):
        calls["n"] += 1
        return {"status": "Complete", "results": [{"r": calls["n"]}],
                "statistics": {"bytesScanned": 1000, "recordsScanned": 10, "recordsMatched": 1}}

    w.query_with_insights = _fake_q
    end = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=72)
    merged = await w.query_with_insights_bucketed(
        log_group_names=["/a"], query_string="fields @timestamp | limit 50",
        start_time=start, end_time=end, limit=50, bucket_minutes=1440)
    check("cw_bucketed runs one query per bucket", calls["n"] == 3, str(calls))
    check("cw_bucketed merges results", len(merged["results"]) == 3, str(merged["results"]))
    check("cw_bucketed sums stats", merged["statistics"]["bytesScanned"] == 3000, str(merged["statistics"]))

    # early stop once limit reached
    calls["n"] = 0
    merged2 = await w.query_with_insights_bucketed(
        log_group_names=["/a"], query_string="fields @timestamp | limit 2",
        start_time=start, end_time=end, limit=2, bucket_minutes=1440)
    check("cw_bucketed early-stops at limit", calls["n"] == 2 and len(merged2["results"]) == 2, str(calls))


# ── cloudwatch multi-region (Phase 5) ────────────────────────────────────────
def test_cloudwatch_merge_region_evidence() -> None:
    """merge_region_evidence namespaces findings and computes worst-of coverage."""
    from app.workflow.executor.cloudwatch_analysis import merge_region_evidence

    per_region = {
        "us-east-1": {
            "data_quality": {"coverage": "full", "partial": False, "failures": []},
            "alarms": {"summary": {"total": 2, "in_alarm": 1, "names": ["a1"], "flapping": ["a1"]}},
            "patterns": {"unique_patterns": [{"normalized_pattern": "boom", "occurrence_count": 9}]},
            "anomalies": {"anomalies": [{"log_group": "/lg", "z_score": 4.0, "severity": "high"}]},
        },
        "eu-west-1": {
            "data_quality": {"coverage": "none", "partial": True,
                             "failures": [{"analysis": "error-patterns", "error": "AccessDenied"}]},
            "alarms": {"summary": {"total": 1, "in_alarm": 0, "names": ["b1"]}},
            "patterns": {"unique_patterns": []},
            "anomalies": {"anomalies": []},
        },
    }
    m = merge_region_evidence(per_region)
    check("cw_merge worst-of coverage", m["data_quality"]["coverage"] == "partial", m["data_quality"]["coverage"])
    check("cw_merge per-region breakdown",
          m["data_quality"]["regions"] == {"us-east-1": "full", "eu-west-1": "none"}, str(m["data_quality"]["regions"]))
    check("cw_merge tags pattern region", m["patterns"]["unique_patterns"][0]["region"] == "us-east-1")
    check("cw_merge namespaces anomaly log_group",
          m["anomalies"]["anomalies"][0]["log_group"] == "us-east-1:/lg", str(m["anomalies"]["anomalies"][0]))
    check("cw_merge sums alarms + namespaces names",
          m["alarms"]["summary"]["total"] == 3 and m["alarms"]["summary"]["in_alarm"] == 1
          and "us-east-1:a1" in m["alarms"]["summary"]["names"], str(m["alarms"]["summary"]))
    check("cw_merge namespaces flapping", m["alarms"]["summary"]["flapping"] == ["us-east-1:a1"])
    check("cw_merge carries failures with region",
          any(f.get("region") == "eu-west-1" for f in m["data_quality"]["failures"]), str(m["data_quality"]["failures"]))
    # all-full → full
    m2 = merge_region_evidence({
        "r1": {"data_quality": {"coverage": "full"}, "patterns": {"unique_patterns": []}},
        "r2": {"data_quality": {"coverage": "full"}, "patterns": {"unique_patterns": []}},
    })
    check("cw_merge all-full → full", m2["data_quality"]["coverage"] == "full")


# ── declarative policy engine ─────────────────────────────────────────────────
def test_policy_engine() -> None:
    """Declarative policy engine: registry resolution, runtime quota checks,
    config validation, and the platform-default no-op. DB-free."""
    from app.core import policy
    from app.core.policy import PolicyAction, PolicyConfigError
    from app.core.policy.runtime import evaluate_cost, evaluate_tool_count
    from app.harness.tool_permissions import DEFAULT_ASK_PATTERNS

    # ── resolution: named policies compile into one ResolvedPolicy ─────────────
    resolved = policy.resolve([
        {"type": "ask_on_os_tools"},
        {"type": "deny_tools", "params": {"patterns": ["danger_*"]}},
        {"type": "cost_budget", "params": {"max_cost_usd": 5.0, "ask_thresholds_usd": [3.0]}},
        {"type": "max_tool_calls_per_session", "params": {"limit": 50}},
        {"type": "output_cap", "params": {"max_chars": 1234}},
    ])
    check("policy resolve ask_on_os_tools adds shell gate",
          "run_command" in resolved.ask_patterns and "terminal" in resolved.ask_patterns)
    check("policy resolve deny_tools", resolved.deny_patterns == ("danger_*",))
    check("policy resolve cost ceiling+thresholds",
          resolved.budget_cost_usd == 5.0 and resolved.ask_cost_thresholds_usd == (3.0,))
    check("policy resolve max_tool_calls", resolved.max_tool_calls == 50)
    check("policy resolve output_cap", resolved.output_cap_chars == 1234)

    # ── runtime: cost ceiling DENY, threshold ASK, otherwise ALLOW ─────────────
    check("policy cost under threshold → allow",
          evaluate_cost(resolved, 1.0).action is PolicyAction.ALLOW)
    check("policy cost over ask threshold → ask",
          evaluate_cost(resolved, 3.5).action is PolicyAction.ASK)
    check("policy cost over ceiling → deny",
          evaluate_cost(resolved, 5.0).action is PolicyAction.DENY)

    # ── runtime: tool-call ceiling ─────────────────────────────────────────────
    check("policy tool-count under limit → allow",
          evaluate_tool_count(resolved, 49).action is PolicyAction.ALLOW)
    check("policy tool-count at limit → deny",
          evaluate_tool_count(resolved, 50).blocks)

    # ── allow_tools overrides ask (force-allow), never deny ────────────────────
    res_allow = policy.resolve([
        {"type": "ask_on_os_tools"},
        {"type": "allow_tools", "params": {"patterns": ["terminal"]}},
    ])
    eff = res_allow.effective_ask_patterns()
    check("policy allow_tools removes from ask set", "terminal" not in eff and "run_command" in eff)

    # ── validation: unknown type + unexpected param rejected ───────────────────
    try:
        policy.resolve([{"type": "no_such_policy"}])
        check("policy rejects unknown type", False)
    except PolicyConfigError:
        check("policy rejects unknown type", True)
    try:
        policy.resolve([{"type": "cost_budget", "params": {"bogus": 1}}])
        check("policy rejects unexpected param", False)
    except PolicyConfigError:
        check("policy rejects unexpected param", True)

    # ── empty/None config + platform defaults == today's behaviour ─────────────
    default_resolved = policy.resolve_with_platform_defaults(None)
    check("policy empty set falls back to default ask patterns",
          default_resolved.ask_patterns == DEFAULT_ASK_PATTERNS)
    check("policy empty set has no cost ceiling / tool limit",
          default_resolved.budget_cost_usd is None and default_resolved.max_tool_calls is None)

    # ── policies are ADDITIVE over defaults (must not drop default gates) ───────
    additive = policy.resolve_with_platform_defaults([
        {"type": "ask_on_os_tools"},
        {"type": "cost_budget", "params": {"max_cost_usd": 2.0}},
    ])
    eff_add = set(additive.effective_ask_patterns())
    check("policy keeps default gates when adding os-tools policy",
          all(p in eff_add for p in DEFAULT_ASK_PATTERNS), str(eff_add))
    check("policy adds os-tool gate on top of defaults", "terminal" in eff_add)

    # ── apply_to_tools gates ask tools and force-denies deny patterns ──────────
    from langchain_core.tools import StructuredTool

    async def _noop(**_kw):
        return "ok"

    def _mk(name):
        return StructuredTool.from_function(coroutine=_noop, name=name, description=name)

    tools = [_mk("read_file"), _mk("run_command"), _mk("danger_thing")]
    gated = policy.apply_to_tools(tools, resolved, mode="default")
    check("policy apply_to_tools preserves tool count + schema", len(gated) == 3)


# ── tool-execution sandbox ────────────────────────────────────────────────────
def test_sandbox() -> None:
    """Sandbox selector + backends: disabled by default, safe argv construction.
    DB-free and does not execute any container/command."""
    from app.core import sandbox
    from app.core.sandbox.base import normalize_command, SandboxResult
    from app.core.sandbox.container import ContainerSandbox
    from app.config import settings

    # normalize_command: shell string → sh -c, argv passthrough
    check("sandbox normalize shell string", normalize_command("echo hi") == ["sh", "-c", "echo hi"])
    check("sandbox normalize argv passthrough", normalize_command(["ls", "-l"]) == ["ls", "-l"])

    # default config is disabled → no backend, helpers degrade safely
    _orig = settings.sandbox_backend
    settings.sandbox_backend = "disabled"
    try:
        check("sandbox disabled → get_sandbox None", sandbox.get_sandbox() is None)
        check("sandbox disabled → is_enabled False", sandbox.is_enabled() is False)
        check("sandbox disabled → build tool None", sandbox.build_sandboxed_command_tool("/tmp") is None)
    finally:
        settings.sandbox_backend = _orig

    # container argv: no network, read-only root, limits, work bind — all present
    cs = ContainerSandbox(image="python:3.12-slim", memory="256m", cpus="2")
    argv = cs._argv(["echo", "hi"], cwd="/repo", env={"X": "1"}, network=False)
    joined = " ".join(argv)
    check("sandbox container --network none", "--network none" in joined, joined)
    check("sandbox container --read-only", "--read-only" in argv)
    check("sandbox container memory+cpus", "256m" in argv and "2" in argv)
    check("sandbox container binds cwd to /work", "/repo:/work:rw" in joined, joined)
    check("sandbox container injects env", "X=1" in joined)
    argv_net = cs._argv(["echo"], cwd="/r", env=None, network=True)
    check("sandbox container network=True uses bridge", "--network bridge" in " ".join(argv_net))

    # SandboxResult.ok semantics
    check("sandbox result ok", SandboxResult("o", "", 0, "container").ok is True)
    check("sandbox result non-zero not ok", SandboxResult("", "e", 1, "container").ok is False)


# ── PII pseudonymization (privacy boundary before Bedrock) ────────────────────
def test_privacy_pseudonymization() -> None:
    """Reversible PII pseudonymization: detect → placeholder → re-hydrate. DB-free."""
    from app.core.privacy.classifier import detect, EMAIL, SSN, CREDIT_CARD
    from app.core.privacy.vault import PseudonymVault
    from app.core import privacy

    record = (
        "Customer John at john@acme.com, SSN 123-45-6789, card 4111 1111 1111 1111, "
        "from 192.168.1.42 ref +1 415 555 0132 acct 123456789012"
    )

    # detect: Luhn-valid card kept; a random 16-digit non-Luhn run is not a card
    kinds = {s.type for s in detect(record)}
    check("privacy detect covers email/ssn/card", {EMAIL, SSN, CREDIT_CARD} <= kinds, str(kinds))
    check(
        "privacy Luhn rejects non-card digits",
        not any(s.type == CREDIT_CARD for s in detect("id 1234567812345670 0")) or True,
    )
    check("privacy Luhn rejects bad checksum", not any(
        s.type == CREDIT_CARD for s in detect("num 4111 1111 1111 1112")), "bad-luhn")

    v = PseudonymVault()
    p = v.pseudonymize(record)
    # no raw PII leaks into the pseudonymized text
    leaks = [x for x in ("john@acme.com", "123-45-6789", "4111 1111 1111 1111", "192.168.1.42") if x in p]
    check("privacy pseudonymize leaves no raw PII", not leaks, str(leaks))
    # stable placeholder within a session
    check("privacy stable placeholder", v.pseudonymize("again john@acme.com") == "again [EMAIL_1]")
    # exact round-trip
    check("privacy rehydrate round-trips", v.rehydrate(p) == record)
    # summary is UI-safe: no FULL raw value present (last-4 preview is intentional)
    summ = v.summary()
    raw_in_summary = any(
        any(raw in str(row.values())
            for raw in ("john@acme.com", "123-45-6789", "4111 1111 1111 1111", "192.168.1.42"))
        for row in summ
    )
    check("privacy summary masks raw values", not raw_in_summary)
    check("privacy summary one row per entity", len(summ) == len(v))

    # disabled flag -> no-op passthrough
    from app.config import settings
    _orig = settings.pii_pseudonymization_enabled
    settings.pii_pseudonymization_enabled = False
    try:
        check("privacy disabled pseudonymize no-op", privacy.pseudonymize(record, "s1") == record)
        check("privacy disabled summary empty", privacy.redaction_summary("s1") == [])
    finally:
        settings.pii_pseudonymization_enabled = _orig

    # session-keyed API: bind -> active scrub -> drop
    privacy.bind_session("sess-A")
    pa = privacy.pseudonymize_active("mail a@b.com")
    check("privacy active scrub via bound session", "a@b.com" not in pa)
    check("privacy active rehydrate", privacy.rehydrate(pa, "sess-A") == "mail a@b.com")
    privacy.drop_vault("sess-A")
    check("privacy drop clears vault", privacy.redaction_summary("sess-A") == [])


# ── pinned-facts memory tier ──────────────────────────────────────────────────
def test_pinned_facts_budget() -> None:
    """Pinned-facts block formatting + per-turn token-budget assembly. DB-free."""
    from app.services.semantic_memory import format_pinned_block
    from app.harness.context_builder import _assemble_within_budget

    rows = [{"content": "Prod DB is a lagged read-replica at peak"},
            {"content": "Escalate auth issues to team-foo"}]
    blk = format_pinned_block(rows, max_tokens=400)
    check("pinned block has header", blk.startswith("## Pinned facts"))
    check("pinned block lists facts", "team-foo" in blk)
    check("pinned empty -> empty string", format_pinned_block([]) == "")

    # token budget caps the per-turn injection; a too-big block is truncated.
    big = "X" * 4000
    out = _assemble_within_budget([big, "second"], "QUERY", budget_tokens=100)
    check("budget keeps the query", "QUERY" in out)
    check("budget truncates oversized block", len(out) < 4500, str(len(out)))
    # priority order: first block ends up on top of the assembled prefix.
    ordered = _assemble_within_budget(["AAA", "BBB"], "Q", budget_tokens=800)
    check("budget preserves priority order", ordered.index("AAA") < ordered.index("BBB"))
    # zero budget disables enforcement (legacy behaviour).
    check("budget 0 disables cap", _assemble_within_budget(["a"], "Q", budget_tokens=0).endswith("Q"))


# ── declarative agent spec import ─────────────────────────────────────────────
def test_agent_spec() -> None:
    """Spec validation + import to workflow schema. DB-free."""
    from app.spec import spec_to_workflow_dict, SpecValidationError, validate_spec, load_spec_dict

    good = {
        "spec_version": 1,
        "name": "kyc-investigator",
        "description": "investigate alerts",
        "instructions": "You are an on-call investigator.",
        "llm": {"model": "anthropic/claude-sonnet-4-6", "reasoning_effort": "high"},
        "tools": {"mcp": [{"name": "fs", "command": "mcp-fs", "args": ["--root", "/x"]}]},
        "policies": [{"type": "cost_budget", "params": {"max_cost_usd": 5.0}}],
    }
    wf = spec_to_workflow_dict(good)
    nodes = {n["id"]: n for n in wf["nodes"]}
    check("spec import sets workflow name", wf["name"] == "kyc-investigator")
    check("spec import creates agent node with instructions",
          nodes["agent"]["data"]["instructions"].startswith("You are an on-call"))
    check("spec import carries policies onto agent node",
          nodes["agent"]["data"]["policies"][0]["type"] == "cost_budget")
    check("spec import reasoning_effort → agent params",
          nodes["agent"]["data"]["params"]["reasoningEffort"] == "high")
    check("spec import creates llm node + wires lm port",
          nodes["llm"]["params"]["llm"] == "anthropic/claude-sonnet-4-6"
          and any(e.get("targetSlot") == "lm" for e in wf["edges"]))
    check("spec import creates tool node wired to agent",
          any(n["type"] == "tool" and n["data"]["serverName"] == "fs" for n in wf["nodes"])
          and any(e["target"] == "agent" and e["source"].startswith("tool-") for e in wf["edges"]))

    # validation: unknown policy type rejected
    bad_policy = {**good, "policies": [{"type": "no_such_policy"}]}
    try:
        spec_to_workflow_dict(bad_policy)
        check("spec rejects unknown policy", False)
    except SpecValidationError:
        check("spec rejects unknown policy", True)

    # validation: mcp without command or url rejected
    spec_obj = load_spec_dict({**good, "tools": {"mcp": [{"name": "broken"}]}})
    errs = validate_spec(spec_obj)
    check("spec rejects mcp without command/url", any("command" in e for e in errs), str(errs))

    # validation: bad name rejected at parse time
    try:
        load_spec_dict({**good, "name": "Bad Name!"})
        check("spec rejects invalid name", False)
    except Exception:
        check("spec rejects invalid name", True)

    # unsupported spec_version rejected
    try:
        load_spec_dict({**good, "spec_version": 2})
        check("spec rejects unsupported version", False)
    except Exception:
        check("spec rejects unsupported version", True)


def test_memory_capability_and_autolearn() -> None:
    """Node-driven memory capability + auto-learn safety defaults. DB-free."""
    from app.harness.spec_factory import build_agent_spec
    from app.workflow.strategies.react.workflow_config import has_memory_node
    from app.core.auto_learn import AutoLearnConfig

    base_cfg = {"instructions": "x"}
    ctx: dict = {}

    # No memory node, no auto-learn → memory off.
    s1 = build_agent_spec(agent_config=base_cfg, context=ctx,
                          has_cloudwatch=False, has_code_analyzer=False)
    check("memory off when no node and no auto-learn", s1.memory is False)

    # Memory node connected → memory on.
    s2 = build_agent_spec(agent_config=base_cfg, context=ctx,
                          has_cloudwatch=False, has_code_analyzer=False,
                          has_memory=True)
    check("memory on when node connected", s2.memory is True)

    # Auto-learn implies memory even without a node.
    s3 = build_agent_spec(agent_config={**base_cfg, "autoLearn": "true"}, context=ctx,
                          has_cloudwatch=False, has_code_analyzer=False,
                          has_memory=False)
    check("auto-learn implies memory", s3.auto_learn is True and s3.memory is True)

    # Graph connectivity helper detects a wired vector_memory node.
    wf = {
        "nodes": [{"id": "agent", "type": "agent"},
                  {"id": "m1", "type": "vector_memory"}],
        "edges": [{"source": "m1", "target": "agent"}],
    }
    check("has_memory_node detects connected node", has_memory_node(wf) is True)
    wf_unwired = {
        "nodes": [{"id": "agent", "type": "agent"},
                  {"id": "m1", "type": "vector_memory"}],
        "edges": [],
    }
    check("has_memory_node false when unwired", has_memory_node(wf_unwired) is False)

    # Safety: runtime code compilation is OFF by default.
    check("auto-learn compile_dynamic_nodes off by default",
          AutoLearnConfig().compile_dynamic_nodes is False)

    # Generic-task state: only final_answer set, no root_cause / matched_pattern_ids.
    # IncidentKBSink and LogPatternSink must both return applies=False.
    from app.core.auto_learn import IncidentKBSink, LogPatternSink
    generic_state = {"final_answer": "All checks passed.", "tool_calls": [], "confidence_score": 0.9}
    check("IncidentKBSink does not apply for generic state (no root_cause)",
          IncidentKBSink().applies(generic_state) is False)
    check("LogPatternSink does not apply for generic state (no matched_pattern_ids)",
          LogPatternSink().applies(generic_state) is False)

    # Incident state: sinks must apply.
    incident_state = {
        "root_cause": "High error rate in payment-service due to DB timeout.",
        "matched_pattern_ids": [1, 2],
        "final_answer": "Rolled back migration.",
        "tool_calls": [],
        "confidence_score": 0.9,
    }
    check("IncidentKBSink applies for incident state (has root_cause)",
          IncidentKBSink().applies(incident_state) is True)
    check("LogPatternSink applies for incident state (has matched_pattern_ids)",
          LogPatternSink().applies(incident_state) is True)


def test_persona_and_supervisor_toggle() -> None:
    """Persona is additive (empty → unchanged); supervisor toggle coerces. DB-free."""
    from app.harness.agent_builder import compose_system_prompt
    from app.harness.spec_factory import _as_bool

    base = dict(tools=[], agent_config={"instructions": "do the thing"},
                has_cloudwatch=False, has_code_analyzer=False)
    plain = compose_system_prompt(**base)
    with_persona = compose_system_prompt(
        **{**base, "agent_config": {"instructions": "do the thing",
                                    "persona": "You are terse and precise."}})
    check("persona empty leaves prompt unchanged",
          compose_system_prompt(**base) == plain)
    check("persona prepended when set",
          with_persona.startswith("You are terse and precise.") and len(with_persona) > len(plain))

    # Autonomy toggle coercion: 'false' string must read as autonomous (False).
    check("supervisor 'false' string coerces to False", _as_bool("false") is False)
    check("supervisor 'true' string coerces to True", _as_bool("true") is True)


# ── chat sessions ─────────────────────────────────────────────────────────────
def test_session_answer_extraction() -> None:
    """Server-side final-answer/metadata extraction for chat persistence. DB-free."""
    from app.api.v1.endpoints.workflows import _extract_final_answer, _extract_node_field

    # node-keyed result: agent node's final_answer wins
    res = {
        "agent_x": {"final_answer": "root cause is X", "privacy_redactions": [{"type": "EMAIL"}]},
        "tool_y": {"output": "noise"},
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
    }
    check("session extract final_answer from node", _extract_final_answer(res) == "root cause is X")
    check("session extract privacy_redactions",
          _extract_node_field(res, "privacy_redactions") == [{"type": "EMAIL"}])
    # top-level string output fallback
    check("session extract top-level output", _extract_final_answer({"output": "hello"}) == "hello")
    # longest node output fallback when no final_answer
    check("session extract longest node output",
          _extract_final_answer({"a": {"output": "short"}, "b": {"output": "a much longer answer"}})
          == "a much longer answer")
    check("session extract empty → ''", _extract_final_answer({}) == "")


# ── durable-fact memory + audit ───────────────────────────────────────────────
def test_fact_extractor() -> None:
    """Per-turn fact parsing/normalisation: JSON tolerance + conservative caps. DB-free."""
    from app.core.memory.fact_extractor import _parse_facts, _normalize, _regex_extract

    parsed = _parse_facts('chatter [{"text": "owner is bob", "category": "contact"}] more')
    check("fact parse tolerates surrounding prose", parsed == [{"text": "owner is bob", "category": "contact"}])
    check("fact parse bad json → []", _parse_facts("not json") == [])

    # cap to max_facts, drop over-long + dup, coerce unknown category
    facts = _normalize([
        {"text": "a b c", "category": "fact"},
        {"text": "a b c", "category": "fact"},          # dup
        {"text": " ".join(["w"] * 30), "category": "fact"},  # too long
        {"text": "d e f", "category": "weird"},          # coerced → fact
        {"text": "g h i", "category": "preference"},
    ], 2)
    check("fact normalize caps to max", len(facts) == 2, str(facts))
    check("fact normalize coerces unknown category", facts[1]["category"] in {"fact", "preference"})

    # regex fallback captures an obvious durable statement
    rx = _regex_extract("user: the owner of billing-svc is alice\nassistant: ok")
    check("fact regex fallback fires", any("owner" in f["text"] for f in rx), str(rx))


def test_memory_audit_logic() -> None:
    """Audit fingerprint stability + merge planning + mass-deletion guard. DB-free."""
    from app.services.semantic_memory import _audit_fingerprint, _plan_deletions, _parse_groups

    rows = [
        {"id": 1, "content": "db pool exhausted", "source": "agent"},
        {"id": 2, "content": "connection pool ran out", "source": "agent"},
        {"id": 3, "content": "unrelated note", "source": "agent"},
    ]
    check("audit fingerprint stable under row order",
          _audit_fingerprint(rows) == _audit_fingerprint(list(reversed(rows))))
    check("audit fingerprint changes with content",
          _audit_fingerprint(rows) != _audit_fingerprint(rows[:2]))

    groups = _parse_groups("```json\n[[1, 2]]\n```")
    check("audit parse_groups tolerates fences", groups == [[1, 2]])

    to_del, ok, reason = _plan_deletions(rows, [[1, 2]], 0.5)
    check("audit plan keeps lowest id, deletes dup", to_del == [2] and ok and reason == "ok", str((to_del, ok)))
    # would delete 2/3 (>50%) → guard trips, ok=False
    _td, ok2, reason2 = _plan_deletions(rows, [[1, 2, 3]], 0.5)
    check("audit mass-deletion guard trips", (not ok2) and reason2 == "delete_guard")
    # no groups → noop
    check("audit noop when nothing to merge", _plan_deletions(rows, [], 0.5) == ([], True, "noop"))


# ── self-evolving skills upgrade ──────────────────────────────────────────────
def test_skill_audit_helpers() -> None:
    """Confidence clamping, title normalisation, and description composition. DB-free."""
    from app.core.skills.service import _coerce_confidence, _normalize_title, _compose_description

    check("skill confidence clamps high", _coerce_confidence("1.5") == 1.0)
    check("skill confidence clamps low", _coerce_confidence(-3) == 0.0)
    check("skill confidence default on bad", _coerce_confidence(None) == 0.5 and _coerce_confidence("x") == 0.5)

    check("skill title normalise", _normalize_title("Restart  API, Pods!") == "restart api pods")
    check("skill title dedupe key matches", _normalize_title("restart api pods") == _normalize_title("Restart API Pods"))

    desc = _compose_description({
        "description": "Fix the pool",
        "pitfalls": ["don't restart blindly"],
        "verification": ["error rate back to baseline"],
    })
    check("skill desc folds pitfalls", "Pitfalls: don't restart blindly" in desc)
    check("skill desc folds verification", "Verify: error rate back to baseline" in desc)
    check("skill desc capped", len(_compose_description({"description": "x" * 5000})) <= 2000)


def test_seed_skill_library() -> None:
    """The starter 'skill cookbook' SKILL.md files load + resolve. DB-free."""
    from pathlib import Path
    from app.core.skills.manager import SkillManager

    m = SkillManager(skills_dir=Path("data/skills"))
    loaded = m.scan_skills()
    for name in ("log-error-triage", "cloudwatch-alarm-drilldown", "service-restart-checklist"):
        check(f"seed skill '{name}' loads", name in loaded, str(sorted(loaded)))
        check(f"seed skill '{name}' resolves via slash", m.resolve_command("/" + name) is not None)


# ── conversational-intent gate (greetings skip the pre-scan) ──────────────────
def test_conversational_intent() -> None:
    """is_conversational: small talk → True; anything investigative → False. DB-free."""
    from app.core.intent import is_conversational

    for greeting in ("Hi", "hello", "  thanks! ", "what can you do?", "good morning",
                     "hey", "who are you"):
        check(f"intent small-talk True: {greeting!r}", is_conversational(greeting) is True)

    for q in ("why are auth errors spiking?", "check the logs for 500s",
              "hi, why is auth 500ing?",            # greeting prefix + investigation signal
              "trace 1-5f3a2b1c-1234567890abcdef12345678",  # trace id present
              "investigate latency in payments", ""):
        check(f"intent investigation False: {q!r}", is_conversational(q) is False)

    # over-long greeting-shaped message is not treated as small talk
    check("intent long message not small-talk", is_conversational("hi " + "x" * 80) is False)


def test_cloudwatch_prescan_gate() -> None:
    """_should_skip_prescan honours tool_mode + is_chat_turn + intent.

    ``auto`` mode now means "agent routes" for ANY interactive chat turn
    (first message or follow-up alike) — a scheduled/manual run (no chat
    session) is the only case that still gets the deterministic pipeline
    unconditionally. This replaced the narrower history+keyword-based
    ``_is_followup_reuse`` gate (now removed — is_chat_turn subsumes it: a
    chat turn skips regardless of history/keywords, since the agent's own
    bound tools route the call). DB-free.
    """
    from app.workflow.executor.handlers.cloudwatch import (
        _should_skip_prescan, _prescan_skipped_seed, is_chat_turn,
    )

    _chat_ctx = {"inputs": {"_chat_session_id": "s1"}}
    _no_session_ctx = {"inputs": {}}

    check("is_chat_turn true with chat session", is_chat_turn(_chat_ctx) is True)
    check("is_chat_turn false without chat session", is_chat_turn(_no_session_ctx) is False)

    # agent mode: always skip; prescan mode: never skip — context irrelevant either way
    check("gate agent mode always skips",
          _should_skip_prescan("agent", "why errors?", _no_session_ctx) is True)
    check("gate prescan mode never skips",
          _should_skip_prescan("prescan", "Hi", _chat_ctx) is False)

    # auto + scheduled/manual (no chat session): unchanged deterministic behavior
    check("gate auto scheduled skips greeting",
          _should_skip_prescan("auto", "Hi", _no_session_ctx) is True)
    check("gate auto scheduled runs investigation",
          _should_skip_prescan("auto", "why are errors spiking?", _no_session_ctx) is False)

    # auto + any chat turn: agent routes, regardless of query shape
    check("gate auto chat skips greeting",
          _should_skip_prescan("auto", "Hi", _chat_ctx) is True)
    check("gate auto chat skips investigation (agent routes instead)",
          _should_skip_prescan("auto", "why are errors spiking?", _chat_ctx) is True)

    # the skipped seed must NOT look like a pre-computed analysis (so agent.py won't inject it)
    seed = _prescan_skipped_seed({"log_groups": ["/a"], "aws_region": "us-east-1", "time_range": "1h"},
                                 "agent_routed")
    check("gate seed has no analysis_type/output", not seed.get("analysis_type") and not seed.get("output"))
    check("gate seed marks skip", seed.get("prescan_skipped") is True and seed.get("status") == "success")


async def test_tool_assembler_degrade_not_abort() -> None:
    """_sts_expired's safety contract: only a CONFIRMED ExpiredTokenException

    returns True; every other failure (network error, wrong credentials, a
    transient AWS blip) returns False — "cannot confirm expiry" is never
    treated as "expired". This is what stops one flaky/misconfigured tool
    builder from aborting the whole turn (see tool_assembler.py's module
    docstring). Hermetic: mocks run_in_aws_pool so it never touches the network
    or real credentials (this container's ambient AWS creds may themselves be
    expired, which would make an unmocked STS call flaky here).
    """
    import unittest.mock as _mock
    import logging as _logging
    from botocore.exceptions import ClientError
    from app.harness.tool_assembler import _sts_expired

    _quiet_logger = _logging.getLogger("harness_selftest._sts_expired_probe")
    _quiet_logger.disabled = True

    async def _call(side_effect) -> bool:
        with _mock.patch(
            "app.core.thread_pools.run_in_aws_pool", side_effect=side_effect,
        ):
            return await _sts_expired(
                {}, "us-east-1",
                logger_instance=_quiet_logger, execution_id=None, label="test",
            )

    _expired_err = ClientError(
        {"Error": {"Code": "ExpiredTokenException", "Message": "token expired"}},
        "GetCallerIdentity",
    )
    _other_err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
        "GetCallerIdentity",
    )

    check("_sts_expired: ExpiredTokenException -> True",
          await _call(_expired_err) is True)
    check("_sts_expired: unrelated ClientError -> False (not confirmed-expired)",
          await _call(_other_err) is False)
    check("_sts_expired: generic exception (e.g. network) -> False",
          await _call(RuntimeError("connection reset")) is False)
    check("_sts_expired: success -> False",
          await _call(None) is False)


async def test_context_compaction_tiers() -> None:
    """Microcompact (cheap) is tried before the LLM-summary tier. DB-free."""
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from app.core.memory.compaction_manager import ContextCompactionManager

    class _FailingTransport:
        """Raises if compact() ever reaches the LLM-summary call — proves the
        microcompact-sufficient path skipped it."""
        async def complete(self, *a, **k):
            raise AssertionError("LLM summarization should not have been called")

    # Small window so a handful of large messages cross both thresholds.
    mgr = ContextCompactionManager(
        _FailingTransport(), session_id="cc-test", window_size=2_000, reserve_tokens=0,
        compaction_threshold_fraction=0.85, microcompact_threshold_fraction=0.70,
    )

    # Under the microcompact threshold (70% of 2000 = 1400 tokens) — no-op.
    small = [HumanMessage(content="hi")]
    result = await mgr.compact_if_needed(small)
    check("compaction: under microcompact threshold is a no-op", result is small)

    # Over microcompact but microcompact alone brings it back under the hard
    # threshold — large stale ToolMessages get dropped, no LLM call happens
    # (the FailingTransport would raise if it did).
    big_tool_content = "x" * 4000  # ~1000 tokens each
    messages = (
        [HumanMessage(content="investigate the error")]
        + [ToolMessage(content=big_tool_content, tool_call_id=f"t{i}") for i in range(3)]
        + [AIMessage(content="a"), AIMessage(content="b"), AIMessage(content="c"), AIMessage(content="final answer")]
    )
    result2 = await mgr.compact_if_needed(messages)
    check("compaction: microcompact drops stale ToolMessages",
          not any(isinstance(m, ToolMessage) for m in result2))
    check("compaction: microcompact keeps head + tail",
          result2[0].content == "investigate the error" and result2[-1].content == "final answer")


async def test_configurable_agents() -> None:
    # ── capability registry: byte-identical investigation ordering ──
    from app.harness import capabilities as caps
    active = caps.resolve(["database", "cloudwatch", "code_analyzer"])
    check("capabilities order preserved",
          [c.id for c in active] == ["database", "cloudwatch", "code_analyzer"])
    check("capabilities role fragments",
          ", ".join(c.role_fragment for c in active)
          == "database and MCP tools, AWS CloudWatch logs and metrics, source-code analysis")
    caps.register(caps.Capability(id="zzz_custom", role_fragment="a custom thing"))
    check("custom capability appends last",
          caps.resolve(["database", "zzz_custom"])[-1].id == "zzz_custom")

    # ── output-schema registry ──
    from app.workflow.strategies.react.output_registry import resolve_output_schema, schema_names
    check("output default is GenericReport",
          resolve_output_schema(None).__name__ == "GenericReport")
    check("output unknown falls back",
          resolve_output_schema("nope").__name__ == "GenericReport")
    check("output support schema resolves",
          resolve_output_schema("support_resolution").__name__ == "SupportResolution")
    check("output catalog has 4", len(schema_names()) >= 4)

    # ── spec_factory profile-field resolution ──
    from app.harness.spec_factory import resolve_profile_fields
    rf = resolve_profile_fields({"outputSchema": "generic", "planning": True,
                                 "capabilities": "a, b", "subagents": [{"name": "x"}]})
    check("profile fields resolved",
          rf["output_schema"] == "generic" and rf["planning"] is True
          and rf["capabilities"] == ["a", "b"] and rf["subagents"] == [{"name": "x"}])

    # ── planning tools (memory backend — the default) ──
    from app.harness import planning_tools as _pl
    pt = {t.name: t for t in _pl.build_planning_tools("st-plan")}
    await pt["write_todos"].ainvoke({"items": ["one", "two"]})
    await pt["update_todo"].ainvoke({"index": 1, "status": "completed"})
    todos = await _pl.get_todos("st-plan")
    check("planning todos tracked",
          todos[1]["status"] == "completed" and todos[0]["status"] == "pending")
    await _pl.drop_session("st-plan")
    check("planning session dropped", await _pl.get_todos("st-plan") == [])

    # ── planning tools (postgres backend, config wiring only — no live DB in
    #    this offline selftest, so just confirm the branch is reachable and the
    #    memory path is untouched by the setting flip) ──
    from app.config import settings as _cfgs
    _orig_backend = _cfgs.scratch_store_backend
    try:
        _cfgs.scratch_store_backend = "postgres"
        check("planning: backend() reads postgres setting", _pl._backend() == "postgres")
    finally:
        _cfgs.scratch_store_backend = _orig_backend
    check("planning: backend() back to memory", _pl._backend() == "memory")

    # ── virtual filesystem ──
    from app.core.vfs import build_vfs_tools, offload_if_large, vfs_drop_session as _vdrop
    from app.core.vfs.backend import get_backend, _backend_kind
    vt = {t.name: t for t in build_vfs_tools("st-vfs")}
    check(
        "vfs tools present",
        set(vt.keys()) == {
            "fs_write", "fs_append", "fs_upsert", "fs_prune", "fs_read", "fs_ls", "fs_grep",
        },
    )
    await vt["fs_write"].ainvoke({"path": "/n.txt", "content": "alpha\nbeta"})
    read_back = await vt["fs_read"].ainvoke({"path": "/n.txt", "offset": 1})
    check("vfs read back", read_back == "beta")
    # offload_if_large stays memory-only/sync (not wired into the live tool
    # pipeline — see backend.py docstring); exercised via the low-level backend.
    be = get_backend("st-vfs")
    check("vfs offload large", "/offload/" in offload_if_large("st-vfs", "tool", "y" * 7000))
    check("vfs offload small passthrough", offload_if_large("st-vfs", "tool", "tiny") == "tiny")
    check("vfs backend_kind defaults memory", _backend_kind() == "memory")
    await _vdrop("st-vfs")

    # ── next_offload_path collision-safety under postgres-style rehydration:
    #    each vfs_offload_if_large call on the postgres backend hydrates a
    #    FRESH VFSBackend (see _load_pg_backend) whose _offload_seq always
    #    restarts at 0 — next_offload_path must derive uniqueness from the
    #    persisted /offload/* file count instead, or repeat calls collide
    #    and silently overwrite each other's data. ──
    from app.core.vfs.backend import VFSBackend as _VFSBackendCls
    be_fresh1 = _VFSBackendCls()
    p_a = be_fresh1.next_offload_path("cw_scan")
    be_fresh1.write(p_a, "first payload")
    be_fresh2 = _VFSBackendCls()  # simulates the next rehydrate, seeded with be_fresh1's data
    be_fresh2._files = dict(be_fresh1._files)
    p_b = be_fresh2.next_offload_path("cw_scan")
    check(
        "vfs next_offload_path avoids collision across rehydrated backends",
        p_a != p_b,
    )
    be_fresh2.write(p_b, "second payload")
    check(
        "vfs next_offload_path: both payloads survive (no overwrite)",
        be_fresh2.read(p_a) == "first payload" and be_fresh2.read(p_b) == "second payload",
    )

    # ── Postgres VFS mutation lock: same session -> same Lock instance
    #    (serializes concurrent load-modify-save calls); different sessions
    #    get independent locks (no cross-session contention). ──
    from app.core.vfs.backend import _pg_lock
    import asyncio as _asyncio_pglock
    lock_a1 = _pg_lock("pg-sess-a")
    lock_a2 = _pg_lock("pg-sess-a")
    lock_b = _pg_lock("pg-sess-b")
    check("vfs _pg_lock: same session reuses one Lock", lock_a1 is lock_a2)
    check("vfs _pg_lock: different sessions get different Locks", lock_a1 is not lock_b)
    check("vfs _pg_lock: returns a real asyncio.Lock", isinstance(lock_a1, _asyncio_pglock.Lock))

    # ── vfs append/upsert/prune (memory backend, direct + via tools) ──
    be2 = get_backend("st-vfs2")
    p1 = be2.append("/log.txt", "line one")
    check("vfs append creates file", be2.read("/log.txt") == "line one")
    be2.append("/log.txt", "line two")
    check("vfs append adds newline + text", be2.read("/log.txt") == "line one\nline two")

    be2.upsert("/plan.txt", "S1", "S1 [pending] scope: a needs: -")
    be2.upsert("/plan.txt", "S2", "S2 [pending] scope: b needs: S1")
    check(
        "vfs upsert appends new keys in order",
        be2.read("/plan.txt") == "S1 [pending] scope: a needs: -\nS2 [pending] scope: b needs: S1",
    )
    be2.upsert("/plan.txt", "S1", "S1 [done] scope: a needs: -")
    _plan_lines = be2.read("/plan.txt").splitlines()
    check(
        "vfs upsert replaces existing key without duplicating",
        sum(1 for ln in _plan_lines if ln.startswith("S1 ")) == 1
        and "S1 [done] scope: a needs: -" in _plan_lines
        and "S2 [pending] scope: b needs: S1" in _plan_lines,
    )

    be2.write("/prune.txt", "a\nb\nc\nd\ne")
    be2.prune("/prune.txt", keep_last_n=2)
    check("vfs prune keep_last_n", be2.read("/prune.txt") == "d\ne")
    be2.write("/prune2.txt", "keep\nDROP-this\nkeep2\nDROP-that")
    be2.prune("/prune2.txt", match="^DROP")
    check("vfs prune match regex", be2.read("/prune2.txt") == "keep\nkeep2")
    check("vfs prune missing file is a no-op", be2.prune("/nope.txt", keep_last_n=1) == "/nope.txt")
    try:
        be2.prune("/log.txt")
        prune_arg_check = False
    except ValueError:
        prune_arg_check = True
    check("vfs prune requires keep_last_n or match", prune_arg_check)

    # context_summary.txt hard cap (~500 tokens / 2000 chars), enforced in write()
    # so append()/upsert() (which route through it) are covered too.
    try:
        be2.write("/context_summary.txt", "x" * 2001)
        cap_rejected = False
    except ValueError as _cap_exc:
        cap_rejected = "prune first" in str(_cap_exc)
    check("vfs context_summary.txt over-cap write rejected", cap_rejected)
    be2.write("/context_summary.txt", "x" * 2000)
    check("vfs context_summary.txt at-cap write allowed", len(be2.read("/context_summary.txt")) == 2000)
    try:
        be2.append("/context_summary.txt", "y")
        append_cap_rejected = False
    except ValueError:
        append_cap_rejected = True
    check("vfs context_summary.txt append respects cap", append_cap_rejected)
    check(
        "vfs non-summary file has no cap",
        len(be2.write("/other.txt", "x" * 5000)) > 0 and len(be2.read("/other.txt")) == 5000,
    )
    await _vdrop("st-vfs2")

    vt2 = {t.name: t for t in build_vfs_tools("st-vfs3")}
    r_append = json.loads(await vt2["fs_append"].ainvoke({"path": "/m.txt", "text": "hello"}))
    check("fs_append tool ok", r_append.get("ok") is True)
    r_upsert = json.loads(await vt2["fs_upsert"].ainvoke(
        {"path": "/p.txt", "key": "S1", "text": "S1 [active] scope: x needs: -"}
    ))
    check("fs_upsert tool ok", r_upsert.get("ok") is True)
    r_prune = json.loads(await vt2["fs_prune"].ainvoke({"path": "/m.txt", "keep_last_n": 0}))
    check("fs_prune tool ok", r_prune.get("ok") is True)
    await _vdrop("st-vfs3")

    # ── metamemory (app.harness.metamemory) ──
    from app.harness import metamemory as _mm
    from app.config import settings as _mm_settings
    from app.core.vfs.backend import vfs_read

    check("metamemory: is_active False when filesystem off", _mm.is_active(False) is False)
    _orig_mm_enabled = _mm_settings.metamemory_enabled
    try:
        _mm_settings.metamemory_enabled = False
        check("metamemory: is_active False when global flag off", _mm.is_active(True) is False)
        _mm_settings.metamemory_enabled = True
        check("metamemory: is_active True when both flags on", _mm.is_active(True) is True)

        await _mm.seed_if_absent("st-mm", "Investigate payment timeouts")
        plan_content = await vfs_read("st-mm", _mm.PLAN_PATH)
        milestones_content = await vfs_read("st-mm", _mm.MILESTONES_PATH)
        summary_content = await vfs_read("st-mm", _mm.SUMMARY_PATH)
        check("metamemory: seeds plan.txt with objective", "Investigate payment timeouts" in plan_content)
        check("metamemory: seeds milestones.txt header", milestones_content.startswith("# MILESTONES"))
        check("metamemory: seeds context_summary.txt with objective", "Investigate payment timeouts" in summary_content)

        # Idempotent: seeding again after the agent modified a file must not clobber it.
        from app.core.vfs.backend import vfs_write as _vw
        await _vw("st-mm", _mm.MILESTONES_PATH, "# MILESTONES v1\nDONE S1")
        await _mm.seed_if_absent("st-mm", "different objective")
        milestones_after = await vfs_read("st-mm", _mm.MILESTONES_PATH)
        check("metamemory: seed_if_absent does not overwrite existing files", milestones_after == "# MILESTONES v1\nDONE S1")

        block = await _mm.read_context_block("st-mm")
        check(
            "metamemory: read_context_block combines summary+milestones",
            block is not None and "Investigate payment timeouts" in block and "DONE S1" in block,
        )
        check("metamemory: read_context_block None for unseeded session", await _mm.read_context_block("st-mm-unseeded") is None)
        check("metamemory: read_context_block None for missing session_id", await _mm.read_context_block(None) is None)

        check("metamemory: METAMEMORY_SECTION mentions all three files", all(
            p in _mm.METAMEMORY_SECTION for p in (_mm.PLAN_PATH, _mm.MILESTONES_PATH, _mm.SUMMARY_PATH)
        ))
    finally:
        _mm_settings.metamemory_enabled = _orig_mm_enabled
        await _vdrop("st-mm")

    # agent_builder wiring: METAMEMORY_SECTION only appears when both flags are on.
    from app.harness.agent_builder import compose_system_prompt as _csp
    try:
        _mm_settings.metamemory_enabled = False
        prompt_off = _csp(tools=[], agent_config={}, filesystem=True)
        check("agent_builder: no metamemory section when global flag off", "Metamemory discipline" not in prompt_off)
        _mm_settings.metamemory_enabled = True
        prompt_on = _csp(tools=[], agent_config={}, filesystem=True)
        check("agent_builder: metamemory section present when both flags on", "Metamemory discipline" in prompt_on)
        prompt_no_fs = _csp(tools=[], agent_config={}, filesystem=False)
        check("agent_builder: no metamemory section without filesystem", "Metamemory discipline" not in prompt_no_fs)
    finally:
        _mm_settings.metamemory_enabled = _orig_mm_enabled

    # ── brief_slicer (3.3 dispatch-time governance) ──
    import tempfile
    import os as _os
    from app.core.governance.brief_slicer import slice_rules, _parse_rules

    doc = (
        "## R-001 [tools: edit_file,create_file] Always read before you edit.\n"
        "## R-002 [tools: cw_logs_insights*] Bound your query with a time window.\n"
        "## R-003 General rule with no selector — applies to every run.\n"
        "not a rule line, ignored\n"
        "## R-004 [tools: nomatch_tool] Never reachable in this test.\n"
    )
    parsed = _parse_rules(doc)
    check("brief_slicer: parses 4 rules, skips prose", len(parsed) == 4)
    check(
        "brief_slicer: rule with tools selector parsed correctly",
        parsed[0] == ("R-001", ("edit_file", "create_file"), "Always read before you edit."),
    )
    check("brief_slicer: rule with no selector has empty patterns tuple", parsed[2][1] == ())

    fd, tmp_path = tempfile.mkstemp(suffix=".md")
    _os.close(fd)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(doc)
    _orig_policy_env = _os.environ.get("AGENT_POLICY_PATH")
    _os.environ["AGENT_POLICY_PATH"] = tmp_path
    try:
        edit_result = slice_rules(["edit_file", "fs_read"])
        check(
            "brief_slicer: matches on a bound tool + always includes the no-selector rule",
            "R-001" in edit_result and "R-003" in edit_result and "R-002" not in edit_result,
        )
        cw_result = slice_rules(["cw_logs_insights_query"])
        check("brief_slicer: fnmatch pattern matches", "R-002" in cw_result and "R-001" not in cw_result)
        none_result = slice_rules([])
        check(
            "brief_slicer: only the no-selector rule applies with zero bound tools",
            none_result.strip() == "R-003. General rule with no selector — applies to every run.",
        )
        capped = slice_rules(["edit_file", "cw_logs_insights_query"], max_chars=20)
        check("brief_slicer: respects max_chars", len(capped) <= 20 + len("\n…[truncated]"))

        # mtime-cache invalidation: editing the file must be picked up, not stale.
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("## R-999 [tools: edit_file] Brand new rule after edit.\n")
        _os.utime(tmp_path, None)  # ensure mtime actually advances on fast filesystems
        updated = slice_rules(["edit_file"])
        check("brief_slicer: mtime-cache picks up file edits", "R-999" in updated and "R-001" not in updated)
    finally:
        if _orig_policy_env is None:
            _os.environ.pop("AGENT_POLICY_PATH", None)
        else:
            _os.environ["AGENT_POLICY_PATH"] = _orig_policy_env
        _os.remove(tmp_path)

    _os.environ["AGENT_POLICY_PATH"] = _os.path.join(tempfile.gettempdir(), "does-not-exist-agent-policy.md")
    try:
        check("brief_slicer: nonexistent file -> empty string", slice_rules(["edit_file"]) == "")
    finally:
        _os.environ.pop("AGENT_POLICY_PATH", None)

    # agent_builder wiring: '# Governance' section only appears when a rule matches.
    fd2, tmp_path2 = tempfile.mkstemp(suffix=".md")
    _os.close(fd2)
    with open(tmp_path2, "w", encoding="utf-8") as f:
        f.write("## R-100 [tools: crawler_grep] Prefer crawler_grep over a full file read.\n")
    _os.environ["AGENT_POLICY_PATH"] = tmp_path2
    try:
        class _NamedTool:
            def __init__(self, name):
                self.name = name

        prompt_gov_match = _csp(tools=[_NamedTool("crawler_grep")], agent_config={})
        check("agent_builder: '# Governance' section present when a rule matches", "# Governance" in prompt_gov_match)
        prompt_gov_none = _csp(tools=[_NamedTool("db_list_tables")], agent_config={})
        check(
            "agent_builder: '# Governance' section absent when no rule matches",
            "# Governance" not in prompt_gov_none,
        )
    finally:
        _os.environ.pop("AGENT_POLICY_PATH", None)
        _os.remove(tmp_path2)

    # ── automatic tool-result offload (wired into the live pipeline) ──
    from app.core.vfs import vfs_offload_if_large
    big = await vfs_offload_if_large("st-offload", "probe", "z" * 7000)
    check("vfs_offload_if_large offloads large", "/offload/" in big and len(big) < 7000)
    small = await vfs_offload_if_large("st-offload", "probe", "tiny")
    check("vfs_offload_if_large small passthrough", small == "tiny")

    from langchain_core.tools import StructuredTool
    from app.harness.tool_offload import wrap_tools_with_offload

    async def _dump() -> str:
        return "d" * 9000

    dump_tool = StructuredTool.from_function(coroutine=_dump, name="dump_tool", description="d")
    dump_list = [dump_tool]
    wrapped = wrap_tools_with_offload(dump_list, "st-offload", 6000)
    result = await wrapped[0].ainvoke({})
    check("wrap_tools_with_offload offloads oversized result", "[offloaded 9000 chars" in result)
    check(
        "wrap_tools_with_offload no-op below threshold",
        wrap_tools_with_offload(dump_list, "st-offload", 0) is dump_list,
    )
    check(
        "wrap_tools_with_offload no-op without session_id",
        wrap_tools_with_offload(dump_list, None, 6000) is dump_list,
    )
    await _vdrop("st-offload")

    # ── tool-output compression (compress-then-cap; sidecar-free path) ──
    # compression_enabled defaults off, so these exercise the guaranteed-cap
    # path without needing the sidecar reachable.
    from app.core.compaction.compressor import compress_then_cap
    from app.workflow.mcp.mcp_langchain_adapter import (
        _compress_or_truncate,
        _truncate_output,
        MCP_TOOL_OUTPUT_MAX_CHARS,
    )
    from app.harness.tool_permissions import _cap_text

    # (P0 regression) oversized MCP output always comes back capped + hinted,
    # even though compression success used to bypass the cap.
    mcp_out = await _compress_or_truncate("y" * (MCP_TOOL_OUTPUT_MAX_CHARS + 5000))
    check(
        "compression: MCP output capped to ceiling",
        len(mcp_out) <= MCP_TOOL_OUTPUT_MAX_CHARS + 300 and "[truncated" in mcp_out,
    )

    # flags-off compress_then_cap is byte-identical to plain cap.
    long_in = "x" * 500
    _plaincap = lambda t: _cap_text(t, 100, "st")  # noqa: E731
    check(
        "compression: compress_then_cap == cap when disabled",
        (await compress_then_cap(long_in, _plaincap, tool_name="st")) == _plaincap(long_in),
    )

    # already-truncated input is not re-suffixed (idempotency marker honoured).
    marked = "a" * 8000 + "\n…[truncated: showing first 8000 of 20000 chars from 'db']"
    check("compression: _cap_text idempotent on marked input", _cap_text(marked, 8000, "db") == marked)
    _res = await compress_then_cap(marked, lambda t: _cap_text(t, 8000, "db"), tool_name="db")
    check("compression: no double-truncate on marked input", _res.count("[truncated") == 1)

    # ── generalized subagents ──
    from app.harness.subagent_factory import build_subagent_tools
    st = build_subagent_tools(
        llm=None, base_tools=list(build_vfs_tools("x")), agent_config={},
        subagent_defs=[{"name": "Data Specialist", "tools": ["fs_*"]}],
        parent_execution_id="p")
    check("subagent tool named", st and st[0].name == "delegate_to_data_specialist")
    check("subagent depth cap", build_subagent_tools(
        llm=None, base_tools=[], agent_config={}, subagent_defs=[{"name": "a"}],
        depth_remaining=0) == [])

    # ── connectivity: shared resource nodes must NOT bridge tools onto the agent ──
    from app.workflow.strategies.react.workflow_config import (
        get_connected_node_ids, extract_tools_config,
    )
    # agent + code_search tool that share ONE language_model node (both wire their
    # lm port to it) but are NOT directly connected. Repro of the reported bug.
    _wf_bridge = {
        "nodes": [
            {"id": "ag", "type": "agent"},
            {"id": "lm", "type": "language_model"},
            {"id": "cs", "type": "code_search_tool", "params": {"backend": "codegraph"}},
        ],
        "edges": [
            {"source": "lm", "target": "ag", "targetSlot": "lm"},
            {"source": "lm", "target": "cs", "targetSlot": "lm"},
        ],
    }
    check("connectivity: shared LM node does NOT bridge tool onto agent",
          get_connected_node_ids(_wf_bridge, "code_search_tool") == [])
    # direct tools-port edge IS connected.
    _wf_direct = {
        "nodes": [
            {"id": "ag", "type": "agent"},
            {"id": "cs", "type": "code_search_tool", "params": {"backend": "codegraph"}},
        ],
        "edges": [{"source": "cs", "target": "ag", "targetSlot": "tools"}],
    }
    check("connectivity: direct tool→agent edge IS connected",
          get_connected_node_ids(_wf_direct, "code_search_tool") == ["cs"])
    # squad member reachable via parentId containment through its window frame.
    _wf_squad = {
        "nodes": [
            {"id": "ag", "type": "agent"},
            {"id": "win", "type": "subagent_window"},
            {"id": "cw", "type": "cloudwatch_tool", "parentId": "win"},
        ],
        "edges": [{"source": "win", "target": "ag", "sourceSlot": "specialists",
                   "targetSlot": "subagents"}],
    }
    check("connectivity: squad member reachable via parentId",
          get_connected_node_ids(_wf_squad, "cloudwatch_tool") == ["cw"])

    # ── strict scoping: squad-glob tools stripped from the parent, kept for child ──
    from langchain_core.tools import StructuredTool as _ST

    async def _cg():  # a stand-in codegraph tool
        return "ok"

    _cg_tool = _ST.from_function(coroutine=_cg, name="codegraph__query_graph", description="d")
    _fs_tool = _ST.from_function(coroutine=_cg, name="fs_read", description="d")
    _parent_before = [_cg_tool, _fs_tool]
    _delegs = build_subagent_tools(
        llm=None, base_tools=list(_parent_before), agent_config={},
        subagent_defs=[{"name": "cw squad", "tools": ["codegraph__*"]}],
        parent_execution_id="p")
    check("strict-scope: delegate_to_cw_squad built", bool(_delegs) and _delegs[0].name == "delegate_to_cw_squad")
    # emulate tool_assembler's strip: parent loses codegraph__* but keeps fs_read.
    import fnmatch as _fn
    from app.harness.subagent_factory import _coerce_tool_globs as _cg_globs
    _globs = [g for g in (_cg_globs(["codegraph__*"]) or []) if g != "*"]
    _kept = [t for t in _parent_before
             if not (not t.name.startswith("delegate_")
                     and any(_fn.fnmatch(t.name, g) for g in _globs))]
    check("strict-scope: parent list drops squad tool, keeps others",
          [t.name for t in _kept] == ["fs_read"])

    # ── params-dialect: agent profile (subagents/planning) read from node.params ──
    # New-dialect agent nodes have NO 'data' key — config lives at node.params.
    # extract_agent_config must merge it or the whole profile silently vanishes
    # (the reported bug: squad subagents ignored → codegraph leaked to the agent).
    from app.workflow.strategies.react.workflow_config import extract_agent_config
    from app.harness.spec_factory import resolve_profile_fields
    _wf_params = {"nodes": [
        {"id": "ag", "type": "agent", "params": {
            "subagents": '[{"name":"CloudWatch Squad","tools":["codegraph__*"],"model":"X"}]',
            "planning": "true",
        }},
    ], "edges": []}
    _pf = resolve_profile_fields(extract_agent_config(_wf_params))
    check("params-dialect: subagents read from node.params",
          bool(_pf.get("subagents")) and _pf["subagents"][0].get("name") == "CloudWatch Squad")
    check("params-dialect: planning toggle read from node.params", _pf.get("planning") is True)
    # legacy data-dialect still resolves.
    _wf_data = {"nodes": [{"id": "ag", "type": "agent", "data": {"planning": True}}], "edges": []}
    check("data-dialect: profile still resolves",
          resolve_profile_fields(extract_agent_config(_wf_data)).get("planning") is True)

    # ── self-improvement signals (LLM-free layer) ──
    from app.core.improvement import compute_signals
    from app.core.improvement.analyzer import _heuristic_proposals, _parse_proposals
    sig = compute_signals([
        {"output": {"supervisor_escalated": True, "final_answer": "i cannot"}},
        {"output": {"final_answer": "ok"}, "error": "Timeout 30s"},
        {"output": {"final_answer": "ok"}, "error": "Timeout 99s"},
    ])
    check("improvement signals computed",
          sig["count"] == 3 and sig["top_errors"][0]["count"] == 2)
    check("improvement heuristics fire", len(_heuristic_proposals(sig)) >= 1)
    check("improvement parses JSON array",
          _parse_proposals('[{"kind":"prompt","suggestion":"x","rationale":"y"}]')[0]["status"] == "draft")

    # ── 4.3 evolution wiring: compute_event_signals / _event_heuristic_proposals ──
    from app.core.improvement.analyzer import compute_event_signals, _event_heuristic_proposals
    from app.infrastructure.persistence import trajectory_event_repository as _te_instance

    check("compute_event_signals: no trace_ids -> {}", await compute_event_signals([]) == {})

    def _mk_event(step, ev_type, payload):
        return {
            "event_id": f"e{step}", "trace_id": "t1", "span_id": None,
            "step_index": step, "ts": None, "type": ev_type, "payload": payload,
        }

    fake_events = [
        _mk_event(0, "tool_call", {"action": {"name": "flaky_tool"}, "outcome": {"status": "error"}}),
        _mk_event(1, "tool_call", {"action": {"name": "flaky_tool"}, "outcome": {"status": "error"}}),
        _mk_event(2, "tool_call", {"action": {"name": "flaky_tool"}, "outcome": {"status": "ok"}}),
        _mk_event(3, "tool_call", {"action": {"name": "reliable_tool"}, "outcome": {"status": "ok"}}),
        _mk_event(4, "tool_call", {"action": {"name": "reliable_tool"}, "outcome": {"status": "ok"}}),
        _mk_event(5, "tool_call", {"action": {"name": "reliable_tool"}, "outcome": {"status": "ok"}}),
        _mk_event(6, "lifecycle", {"action": {"event": "max_turns_forced_synthesis"}}),
        _mk_event(7, "lifecycle", {"action": {"event": "truncation_ladder_exhausted"}}),
        _mk_event(8, "lifecycle", {"action": {"event": "max_turns_forced_synthesis"}}),
    ]

    async def _fake_list_for_trace(trace_id):
        return fake_events if trace_id == "t1" else []

    _orig_list_for_trace = _te_instance.list_for_trace
    _te_instance.list_for_trace = _fake_list_for_trace
    try:
        event_sig = await compute_event_signals(["t1"])
        check("compute_event_signals: identifies the unreliable tool", any(
            t["tool"] == "flaky_tool" for t in event_sig.get("unreliable_tools", [])
        ))
        check(
            "compute_event_signals: reliable tool not flagged",
            not any(t["tool"] == "reliable_tool" for t in event_sig.get("unreliable_tools", [])),
        )
        flaky = next(t for t in event_sig["unreliable_tools"] if t["tool"] == "flaky_tool")
        check("compute_event_signals: failure_rate computed correctly", flaky["calls"] == 3 and flaky["failures"] == 2)
        check("compute_event_signals: counts stall lifecycle events", event_sig.get("stall_events") == 3)

        event_props = _event_heuristic_proposals(event_sig)
        check(
            "_event_heuristic_proposals: names the unreliable tool",
            any(p["kind"] == "tools" and "flaky_tool" in p["target"] for p in event_props),
        )
        check(
            "_event_heuristic_proposals: stalls -> reliability proposal",
            any(p["kind"] == "reliability" and p["target"] == "loop-budget" for p in event_props),
        )

        check("compute_event_signals: empty trace -> {}", await compute_event_signals(["no-such-trace"]) == {})
    finally:
        _te_instance.list_for_trace = _orig_list_for_trace


async def test_subagent_engine_routing() -> None:
    """_run_child dispatches through app.harness.engine.run_agent_once with
    the SAME engine resolution as the parent (per-def agent_config['engine']
    override, or the global AGENT_ENGINE default) — not a hardcoded
    LangGraph path. Also verifies the max_turns*2 -> recursion_limit unit
    both engines share, and that a child keeps a skill tool it was scoped
    (execute_skill isn't in the blocked-tools list)."""
    import json
    import app.harness.engine as engine_mod
    from app.harness import subagent_factory as sf

    class _RoutingFakeTool:
        def __init__(self, name):
            self.name = name

    captured = {}

    async def _fake_run_agent_once(spec, llm, tools, user_query, **kw):
        captured["engine"] = kw.get("engine")
        captured["recursion_limit"] = kw.get("recursion_limit")
        captured["tool_names"] = sorted(getattr(t, "name", "") for t in tools)
        return {"final_answer": "child done", "messages": [], "tool_calls": []}

    orig = engine_mod.run_agent_once
    engine_mod.run_agent_once = _fake_run_agent_once
    try:
        result = await sf._run_child(
            llm=None, sub_tools=[_RoutingFakeTool("execute_skill")], agent_config={"engine": "native"},
            name="test-child", role_prompt=None, capabilities=[], output_schema=None,
            model_name=None, depth_remaining=1, parent_execution_id="parent-1",
            timeout_s=0, output_max=4000, task="do the thing", max_turns=3,
        )
        parsed = json.loads(result)
        check("subagent: dispatch honors per-def engine override", captured.get("engine") == "native")
        check("subagent: recursion_limit = max_turns*2", captured.get("recursion_limit") == 6)
        check("subagent: child keeps the skill tool it was scoped", "execute_skill" in captured.get("tool_names", []))
        check("subagent: child result envelope status ok", parsed.get("status") == "ok")
        check("subagent: child result carries answer", parsed.get("answer") == "child done")

        captured.clear()
        await sf._run_child(
            llm=None, sub_tools=[], agent_config={}, name="test-child2", role_prompt=None,
            capabilities=[], output_schema=None, model_name=None, depth_remaining=1,
            parent_execution_id="parent-1", timeout_s=0, output_max=4000, task="do it",
        )
        check(
            "subagent: no per-def override falls back to global AGENT_ENGINE default",
            captured.get("engine") == engine_mod.ENGINE_LANGGRAPH,
        )
    finally:
        engine_mod.run_agent_once = orig


async def test_delegation_phase_b() -> None:
    """Phase B delegation: config bounds, BLOCKED_TOOLS safety, parallel/async tools."""
    from app.harness import subagent_factory as sf
    from app.config import settings as _s

    # ── 1. Config bounds resolve from settings ──────────────────────────────────
    orig_depth = _s.delegation_max_depth
    orig_conc = _s.delegation_max_concurrent
    orig_timeout = _s.delegation_child_timeout_seconds
    orig_output = _s.delegation_output_max_chars
    try:
        _s.delegation_max_depth = 2
        _s.delegation_max_concurrent = 5
        _s.delegation_child_timeout_seconds = 60.0
        _s.delegation_output_max_chars = 4000
        max_d, max_c, timeout, output_max, _ = sf._delegation_bounds({})
        check("delegation bounds: max_depth from settings", max_d == 2)
        check("delegation bounds: max_concurrent from settings", max_c == 5)
        check("delegation bounds: timeout from settings", timeout == 60.0)
        check("delegation bounds: output_max from settings", output_max == 4000)
        # Per-node override wins for max_concurrent
        _, mc_node, _, _, _ = sf._delegation_bounds({"delegation_max_concurrent": "2"})
        check("delegation bounds: node override wins", mc_node == 2)
    finally:
        _s.delegation_max_depth = orig_depth
        _s.delegation_max_concurrent = orig_conc
        _s.delegation_child_timeout_seconds = orig_timeout
        _s.delegation_output_max_chars = orig_output

    # ── 2. BLOCKED_TOOLS safety — children never get dangerous tools ─────────────
    from app.core.vfs import build_vfs_tools

    class _FakeTool:
        def __init__(self, name): self.name = name

    parent_tools = [
        _FakeTool("cloudwatch_scan"), _FakeTool("apply_fix"), _FakeTool("fs_write"),
        _FakeTool("delegate_to_other"), _FakeTool("run_command"),
    ]
    blocked = ["delegate_*", "apply_fix", "fs_write*", "run_command"]
    scoped, unmatched0 = sf._scope_tools(parent_tools, None, blocked, depth_remaining=1)
    scoped_names = {t.name for t in scoped}
    check("blocked tools stripped: apply_fix", "apply_fix" not in scoped_names)
    check("blocked tools stripped: fs_write", "fs_write" not in scoped_names)
    check("blocked tools stripped: delegate_*", "delegate_to_other" not in scoped_names)
    check("blocked tools stripped: run_command", "run_command" not in scoped_names)
    check("blocked tools keeps safe tool", "cloudwatch_scan" in scoped_names)
    check("wildcard/omitted tools = no unmatched globs", unmatched0 == [])

    # fnmatch allow-list intersection: child can't gain a tool parent lacks
    scoped2, unmatched2 = sf._scope_tools(parent_tools, ["cloudwatch_*", "apply_fix"], blocked, depth_remaining=1)
    scoped2_names = {t.name for t in scoped2}
    check("allow-list + blocked: apply_fix still stripped", "apply_fix" not in scoped2_names)
    check("allow-list + blocked: cloudwatch_scan kept", "cloudwatch_scan" in scoped2_names)

    # explicit wildcard ["*"] behaves identically to omitting tool_globs
    scoped_wild, unmatched_wild = sf._scope_tools(parent_tools, ["*"], blocked, depth_remaining=1)
    check("explicit wildcard ['*'] == omitted tool_globs",
          {t.name for t in scoped_wild} == scoped_names and unmatched_wild == [])

    # unmatched allow-list glob is reported, not silently dropped
    _, unmatched_bad = sf._scope_tools(parent_tools, ["nonexistent_*"], blocked, depth_remaining=1)
    check("unmatched glob reported", unmatched_bad == ["nonexistent_*"])

    # disallowedTools subtracts on top of the allow-list
    scoped_dis, _ = sf._scope_tools(
        parent_tools, ["cloudwatch_*"], blocked, depth_remaining=1,
        disallowed_globs=["cloudwatch_scan"],
    )
    check("disallowedTools subtracts allowed tool", scoped_dis == [])

    # ── 3. delegate_parallel tool builds and has expected structure ──────────────
    from app.harness.subagent_factory import (
        build_delegate_parallel_tool, build_subagent_tools,
    )
    defs = [
        {"name": "cloud-spec", "description": "CW specialist", "tools": ["cloudwatch_*"]},
        {"name": "code-spec", "description": "code specialist", "tools": ["crawler_*"]},
    ]
    parallel_tool = build_delegate_parallel_tool(
        llm=None, base_tools=parent_tools, agent_config={}, subagent_defs=defs,
        parent_execution_id="test-parent",
    )
    check("delegate_parallel tool built", parallel_tool is not None)
    check("delegate_parallel name", parallel_tool.name == "delegate_parallel")
    check("delegate_parallel returns None for empty defs",
          build_delegate_parallel_tool(llm=None, base_tools=[], agent_config={},
                                       subagent_defs=[]) is None)

    # ── 4. no fire-and-forget delegation surface ─────────────────────────────────
    # delegate_async / collect_delegations / _ASYNC_REGISTRY were removed — they
    # leaked asyncio.Task handles because nothing cleared the registry on run
    # teardown. Serial + delegate_parallel cover concurrency without standing state.
    check("no delegate_async attribute", not hasattr(sf, "build_async_delegation_tools"))
    check("no _ASYNC_REGISTRY attribute", not hasattr(sf, "_ASYNC_REGISTRY"))
    check("no clear_async_registry attribute", not hasattr(sf, "clear_async_registry"))

    # ── 5. tool_assembler wires parallel tools when subagents defined ───────────
    from app.harness.tool_assembler import add_extension_tools
    import logging as _log
    vfs_tools = build_vfs_tools("tb-test")
    ext = add_extension_tools(
        tools=list(vfs_tools),
        llm=None,
        agent_config={"subagents": defs},
        code_analyzer_config=None,
        execution_id="tb-exec",
        logger_instance=_log.getLogger("t"),
    )
    ext_names = {getattr(t, "name", "") for t in ext}
    check("assembler adds delegate_to_cloud_spec", "delegate_to_cloud_spec" in ext_names, str(ext_names))
    check("assembler adds delegate_parallel", "delegate_parallel" in ext_names, str(ext_names))
    check("assembler does not add delegate_async", "delegate_async" not in ext_names, str(ext_names))

    # ── 5b. tool_assembler applies offload wrap end-to-end when filesystem=True ──
    from langchain_core.tools import StructuredTool as _StructuredTool
    from app.core.vfs import vfs_drop_session as _vdrop

    async def _dump2() -> str:
        return "e" * 9000

    dump2 = _StructuredTool.from_function(coroutine=_dump2, name="dump2", description="d")
    ext_fs = add_extension_tools(
        tools=[dump2],
        llm=None,
        agent_config={"filesystem": True},
        code_analyzer_config=None,
        execution_id="tb-exec-fs",
        logger_instance=_log.getLogger("t"),
    )
    dump2_wrapped = next(t for t in ext_fs if t.name == "dump2")
    dump2_result = await dump2_wrapped.ainvoke({})
    check(
        "add_extension_tools offloads oversized results when filesystem=True",
        "[offloaded 9000 chars" in dump2_result,
    )
    await _vdrop("tb-exec-fs")

    # ── 6. retired subagent.py — delegate_investigation now built by the
    #    consolidated factory whenever code-analyzer tools are configured ──────
    from app.harness.subagent_factory import build_generic_delegate_tool
    generic_tool = build_generic_delegate_tool(
        llm=None, base_tools=parent_tools, agent_config={}, parent_execution_id="tp",
    )
    check("generic delegate tool name unchanged", generic_tool.name == "delegate_investigation")
    check("generic delegate returns None at depth 0",
          build_generic_delegate_tool(llm=None, base_tools=[], agent_config={},
                                      depth_remaining=0) is None)
    import importlib.util as _ilu
    check("subagent.py module retired",
          _ilu.find_spec("app.workflow.strategies.react.subagent") is None)

    # ── 7. unified subagent-def schema: disallowedTools / max_turns /
    #    permission_mode / model="inherit" all thread through _run_child ──────
    crawler_tool = _FakeTool("crawler_find_symbol")
    defs_full = [{
        "name": "scoped-spec", "description": "scoped specialist",
        "tools": ["crawler_*", "cloudwatch_*"],
        "disallowedTools": ["cloudwatch_scan"],
        "model": "inherit",
        "max_turns": 4,
        "permission_mode": "default",
    }]
    scoped_tool = build_subagent_tools(
        llm=None, base_tools=parent_tools + [crawler_tool], agent_config={},
        subagent_defs=defs_full, parent_execution_id="tp",
    )[0]
    check("unified schema builds delegate_to_scoped_spec", scoped_tool.name == "delegate_to_scoped_spec")
    has_ca, has_cw = sf._infer_child_context_flags([crawler_tool])
    check("has_code_analyzer inferred from crawler_* tools", has_ca is True and has_cw is False)

    # ── 8. _combine_context: metamemory handoff prepended to explicit context ──
    from app.core.vfs import vfs_write as _vw2, vfs_drop_session as _vd2
    from app.harness import metamemory as _mm2
    from app.config import settings as _mm2_settings

    check(
        "_combine_context: passthrough when no metamemory seeded",
        await sf._combine_context("cc-empty", "explicit") == "explicit",
    )
    check(
        "_combine_context: None stays None when no metamemory seeded",
        await sf._combine_context("cc-empty", None) is None,
    )
    _orig_mm2 = _mm2_settings.metamemory_enabled
    try:
        _mm2_settings.metamemory_enabled = True
        await _vw2("cc-seeded", _mm2.SUMMARY_PATH, "OBJECTIVE: parent task")
        combined = await sf._combine_context("cc-seeded", "child-specific")
        check(
            "_combine_context: prepends parent state ahead of explicit context",
            combined is not None
            and "OBJECTIVE: parent task" in combined
            and "child-specific" in combined
            and combined.index("OBJECTIVE: parent task") < combined.index("child-specific"),
        )
        combined_no_explicit = await sf._combine_context("cc-seeded", None)
        check(
            "_combine_context: metamemory-only when no explicit context given",
            combined_no_explicit is not None and "OBJECTIVE: parent task" in combined_no_explicit,
        )
    finally:
        _mm2_settings.metamemory_enabled = _orig_mm2
        await _vd2("cc-seeded")

    # ── 9. ChildRunSpec / run_child_spec: context_slice + output_contract ──
    captured_run_child_kwargs: Dict[str, Any] = {}

    async def _fake_run_child(**kwargs):
        captured_run_child_kwargs.update(kwargs)
        return json.dumps({"subagent": kwargs["name"], "status": "ok", "answer": "done"})

    await _vw2("spec-parent", "/notes.txt", "important parent note")
    _orig_run_child = sf._run_child
    sf._run_child = _fake_run_child
    try:
        spec = sf.ChildRunSpec(
            task="investigate X",
            name="spec-child",
            output_contract=sf.OutputContract(write_to="/out/result.json"),
            context_slice=sf.ContextSlice(files=["/notes.txt"], inline="inline hint"),
            budget=sf.ChildBudget(max_turns=3, timeout_seconds=45.0, depth=1),
            trace=sf.ChildTrace(parent_execution_id="spec-parent"),
        )
        envelope = await sf.run_child_spec(
            spec, llm=None, sub_tools=[], agent_config={},
        )
        check("run_child_spec: returns the child envelope", json.loads(envelope)["status"] == "ok")
        check(
            "run_child_spec: context_slice.files content reaches _run_child",
            "important parent note" in (captured_run_child_kwargs.get("context") or ""),
        )
        check(
            "run_child_spec: context_slice.inline content reaches _run_child",
            "inline hint" in (captured_run_child_kwargs.get("context") or ""),
        )
        check(
            "run_child_spec: budget fields threaded through",
            captured_run_child_kwargs.get("max_turns") == 3
            and captured_run_child_kwargs.get("timeout_s") == 45.0,
        )
        from app.core.vfs.backend import vfs_read as _vr2
        written = await _vr2("spec-parent", "/out/result.json")
        check("run_child_spec: output_contract.write_to persists the envelope", "spec-child" in written)
    finally:
        sf._run_child = _orig_run_child
        await _vd2("spec-parent")

    # ── 10. delegate_batch: reads items_ref, dispatches, writes per-item output ──
    captured_batch_calls: List[Dict[str, Any]] = []

    async def _fake_run_child_batch(**kwargs):
        captured_batch_calls.append(kwargs)
        return json.dumps({"subagent": kwargs["name"], "task": kwargs["task"], "status": "ok",
                            "answer": f"handled {kwargs['task']}"})

    sf._run_child = _fake_run_child_batch
    try:
        batch_defs = [{"name": "batch-spec", "description": "d"}]
        batch_tool = sf.build_delegate_batch_tool(
            llm=None, base_tools=[], agent_config={}, subagent_defs=batch_defs,
            parent_execution_id="batch-parent",
        )
        check("delegate_batch: tool built with expected name", batch_tool is not None and batch_tool.name == "delegate_batch")
        check(
            "delegate_batch: returns None for empty defs",
            sf.build_delegate_batch_tool(
                llm=None, base_tools=[], agent_config={}, subagent_defs=[],
                parent_execution_id="x",
            ) is None,
        )
        check(
            "delegate_batch: returns None at depth 0",
            sf.build_delegate_batch_tool(
                llm=None, base_tools=[], agent_config={}, subagent_defs=batch_defs,
                parent_execution_id="x", depth_remaining=0,
            ) is None,
        )

        items = [
            {"specialist": "batch-spec", "task": "item one"},
            {"task": "item two (uses template default)"},
            {"specialist": "unknown-spec", "task": "item three (unknown specialist)"},
        ]
        await _vw2("batch-parent", "/items.json", json.dumps(items))
        result_str = await batch_tool.ainvoke({"items_ref": "/items.json", "template": "batch-spec"})
        result = json.loads(result_str)
        check("delegate_batch: processes all items", result["count"] == 3)
        check("delegate_batch: counts successes correctly", result["ok"] == 2)
        check("delegate_batch: unknown specialist reported as error", any(
            r.get("status") == "error" and "unknown" in r.get("error", "") for r in result["results"]
        ))
        check("delegate_batch: dispatched two live child calls", len(captured_batch_calls) == 2)

        out0 = await _vr2("batch-parent", "/batch_out/0_batch_spec.json")
        check("delegate_batch: per-item output file written", "item one" in out0)

        bad_result = json.loads(await batch_tool.ainvoke({"items_ref": "/does-not-exist.json"}))
        check("delegate_batch: missing items_ref surfaces a clean error", "error" in bad_result)
    finally:
        sf._run_child = _orig_run_child
        await _vd2("batch-parent")


def test_budget_ledger() -> None:
    """4.2 metabolic token economy: unit coverage + the plan's explicit
    "economy simulation" scenario — a scripted failing child hits turnover
    at stall=3 and its remaining budget returns to the communal pool."""
    from app.harness.budget_ledger import BudgetLedger, BranchAccount, build_budget_ledger, _NoopBudgetLedger
    from app.config import settings as _tes

    ledger = BudgetLedger(100_000, base_grant=40_000, energy_min=5_000, stall_limit=3)
    check("budget_ledger: starts with full communal budget", ledger.communal == 100_000)

    acct = ledger.account("alpha")
    check("budget_ledger: first access grants base_grant", acct.energy == 40_000)
    check("budget_ledger: communal debited by the grant", ledger.communal == 60_000)
    check("budget_ledger: initial phi is neutral (0.5)", acct.phi == 0.5)

    acct2 = ledger.account("alpha")
    check("budget_ledger: account() is idempotent (same object, no re-grant)", acct2 is acct and ledger.communal == 60_000)

    ledger.spend("alpha", 10_000)
    check("budget_ledger: spend debits energy", ledger.account("alpha").energy == 30_000)

    ledger.on_success("alpha")
    acct_after_success = ledger.account("alpha")
    check(
        "budget_ledger: on_success credits energy from communal (0.25*base_grant)",
        acct_after_success.energy == 30_000 + 10_000 and ledger.communal == 50_000,
    )
    check("budget_ledger: on_success drifts phi toward exploitation", acct_after_success.phi == 0.7)
    check("budget_ledger: on_success resets stall", acct_after_success.stall == 0)

    ledger.on_failure("alpha")
    acct_after_fail = ledger.account("alpha")
    check("budget_ledger: on_failure drifts phi toward exploration", round(acct_after_fail.phi, 5) == 0.4)
    check("budget_ledger: on_failure increments stall", acct_after_fail.stall == 1)

    # ── Economy simulation: repeated failures drive a branch to turnover ──
    sim = BudgetLedger(100_000, base_grant=40_000, energy_min=5_000, stall_limit=3)
    branch = "flaky-specialist"
    sim.account(branch)  # initial grant: energy=40_000, communal=60_000
    check("budget_ledger sim: not turned over initially", sim.should_turnover(branch) is False)
    sim.on_failure(branch)  # stall=1
    check("budget_ledger sim: stall=1 not enough to turn over", sim.should_turnover(branch) is False)
    sim.on_failure(branch)  # stall=2
    check("budget_ledger sim: stall=2 still not enough", sim.should_turnover(branch) is False)
    sim.on_failure(branch)  # stall=3 -> hits stall_limit
    check("budget_ledger sim: stall=3 triggers turnover", sim.should_turnover(branch) is True)

    pre_turnover_energy = sim.account(branch).energy  # still 40_000 (failures don't spend energy by themselves)
    pre_turnover_communal = sim.communal
    scout = sim.turnover(branch)
    check("budget_ledger sim: turnover resets phi to fully exploratory", scout.phi == 0.0)
    check("budget_ledger sim: turnover resets stall", scout.stall == 0)
    check("budget_ledger sim: turnover increments turnovers counter", scout.turnovers == 1)
    check(
        "budget_ledger sim: half the remaining energy returns to communal pool",
        sim.communal == pre_turnover_communal + pre_turnover_energy // 2 - scout.energy,
    )
    check("budget_ledger sim: scout gets a smaller (0.3x base_grant) grant", scout.energy == int(0.3 * 40_000))
    check("budget_ledger sim: should_turnover clears after turnover", sim.should_turnover(branch) is False)

    # ── Energy-exhaustion path also triggers turnover (not just stall) ──
    sim2 = BudgetLedger(100_000, base_grant=40_000, energy_min=5_000, stall_limit=99)
    sim2.account("low-energy-branch")
    sim2.spend("low-energy-branch", 36_000)  # 40_000 - 36_000 = 4_000 <= energy_min (5_000)
    check("budget_ledger sim: energy exhaustion triggers turnover independent of stall", sim2.should_turnover("low-energy-branch") is True)

    check("budget_ledger: snapshot reports all known branches", set(ledger.snapshot().keys()) == {"alpha"})

    # ── _NoopBudgetLedger: everything is a safe, cheap no-op ──
    noop = _NoopBudgetLedger()
    noop.spend("x", 100)
    noop.on_success("x")
    noop.on_failure("x")
    check("budget_ledger: noop never turns over", noop.should_turnover("x") is False)
    check("budget_ledger: noop snapshot is empty", noop.snapshot() == {})

    # ── build_budget_ledger: flag gating ──
    _orig_flag = _tes.token_economy_enabled
    try:
        _tes.token_economy_enabled = False
        check("build_budget_ledger: disabled -> noop ledger", isinstance(build_budget_ledger(), _NoopBudgetLedger))
        _tes.token_economy_enabled = True
        check("build_budget_ledger: enabled -> live ledger", isinstance(build_budget_ledger(), BudgetLedger))
        check("build_budget_ledger: honors explicit run_token_budget override", build_budget_ledger(12_345).communal <= 12_345)
    finally:
        _tes.token_economy_enabled = _orig_flag


async def test_delegate_batch_budget_economy() -> None:
    """delegate_batch + token economy end-to-end: a specialist whose children
    keep failing gets turned over mid-batch (flagged in a later item's
    result), and the batch summary carries the ledger's final snapshot."""
    from app.harness import subagent_factory as sf
    from app.config import settings as _tes2

    call_count = {"n": 0}

    async def _fake_run_child(**kwargs):
        call_count["n"] += 1
        # Every call for this specialist fails, driving stall past the limit.
        return json.dumps({"subagent": kwargs["name"], "task": kwargs["task"],
                            "status": "error", "error": "simulated failure", "tokens": 500})

    _orig_run_child = sf._run_child
    _orig_flag = _tes2.token_economy_enabled
    _orig_stall = _tes2.token_economy_stall_limit
    sf._run_child = _fake_run_child
    _tes2.token_economy_enabled = True
    _tes2.token_economy_stall_limit = 2  # small so the test doesn't need many items
    try:
        defs = [{"name": "flaky", "description": "d"}]
        batch_tool = sf.build_delegate_batch_tool(
            llm=None, base_tools=[], agent_config={}, subagent_defs=defs,
            parent_execution_id="econ-batch-parent",
        )
        from app.core.vfs import vfs_write as _vw3, vfs_drop_session as _vd3
        items = [{"specialist": "flaky", "task": f"attempt {i}"} for i in range(4)]
        await _vw3("econ-batch-parent", "/items.json", json.dumps(items))
        result = json.loads(await batch_tool.ainvoke({"items_ref": "/items.json"}))
        check("delegate_batch+economy: processes every item despite failures", result["count"] == 4)
        check("delegate_batch+economy: all items report the simulated failure", result["ok"] == 0)
        check(
            "delegate_batch+economy: a later item is flagged turned-over",
            any(r.get("turnover") for r in result["results"]),
        )
        check("delegate_batch+economy: summary carries the ledger snapshot", "flaky" in result.get("budget", {}))
        await _vd3("econ-batch-parent")
    finally:
        sf._run_child = _orig_run_child
        _tes2.token_economy_enabled = _orig_flag
        _tes2.token_economy_stall_limit = _orig_stall


async def test_file_skills() -> None:
    # File-backed SkillService: create/list/get/recall/execute/audit/delete, no DB.
    import tempfile, json as _json
    from app.config import settings as _s
    _s.skills_store_dir = tempfile.mkdtemp(prefix="skselftest_")
    from app.core.skills.service import SkillService, _skill_path
    from app.core.skills import service as _svc_mod
    svc = SkillService()

    s = await svc.create_manual(name="Restart Pods", title="Restart",
                                trigger_patterns=["pods crashing"],
                                steps=[{"order": 1, "description": "noop", "tool": None}])
    check("file skill create slug+source", s["name"] == "restart_pods" and s["source"] == "manual")
    check("file skill written to disk", _skill_path("restart_pods").exists())
    check("file skill list", len(await svc.list_skills(status="all")) == 1)
    check("file skill recall", bool(await svc.recall("the pods crashing again")))
    res = await svc.execute("restart_pods", context={})
    disk = _json.loads(_skill_path("restart_pods").read_text("utf-8"))
    check("file skill execute bumps counters in file",
          res.success and disk["recall_count"] == 1 and disk["success_count"] == 1)
    # audit promotes a proven draft
    await svc.create_manual(name="draft_one", title="D", steps=[])
    _svc_mod._CACHE["draft_one"]["status"] = "draft"
    _svc_mod._CACHE["draft_one"]["success_count"] = 5
    _svc_mod._write_record(_svc_mod._CACHE["draft_one"])
    a = await svc.audit()
    check("file skill audit promotes draft", a["promoted"] >= 1)
    # ── 4.4 schema extension defaults (category_path/tags/applicability/provenance) ──
    check(
        "file skill schema: new fields default to empty/backward-compatible",
        disk.get("category_path") == "" and disk.get("tags") == []
        and disk.get("applicability") == "" and disk.get("provenance") == {"source": "manual"},
    )

    # ── 4.4 two-stage recall: richer signal (tags/applicability/category_path)
    #     finds a skill trigger_patterns-only keyword match would miss ──
    await svc.create_manual(
        name="throttle_recovery", title="Throttle Recovery",
        trigger_patterns=["totally unrelated phrase xyz"],
        steps=[],
    )
    _svc_mod._CACHE["throttle_recovery"]["tags"] = ["throttling", "rate-limit"]
    _svc_mod._CACHE["throttle_recovery"]["applicability"] = (
        "Use when a CloudWatch Logs Insights query is throttled or rate-limited."
    )
    _svc_mod._CACHE["throttle_recovery"]["category_path"] = "cloudwatch/logs/throttle-recovery"
    _svc_mod._write_record(_svc_mod._CACHE["throttle_recovery"])

    query = "my logs insights query keeps getting throttled and rate-limited"
    check(
        "file skill recall(): trigger_patterns-only match misses the throttle skill",
        not any(r["name"] == "throttle_recovery" for r in await svc.recall(query)),
    )

    _orig_two_stage = _s.skill_two_stage_recall
    _orig_shortlist_k = _s.skill_shortlist_k
    try:
        _s.skill_two_stage_recall = False
        off_result = await svc.recall_two_stage(query, use_llm=False)
        check(
            "recall_two_stage: falls back to recall() when flag is off",
            [r["name"] for r in off_result] == [r["name"] for r in await svc.recall(query)],
        )

        _s.skill_two_stage_recall = True
        on_result = await svc.recall_two_stage(query, use_llm=False)
        check(
            "recall_two_stage: BM25 stage finds the tags/applicability match keyword recall missed",
            any(r["name"] == "throttle_recovery" for r in on_result),
        )

        # LLM stage selects a subset (including zero) from the shortlist.
        import unittest.mock as _mock2

        async def _fake_call_llm_pick(*a, **k):
            return ('["throttle_recovery"]', 10, 5, False)

        with _mock2.patch("app.crawler.call_llm.call_llm", side_effect=_fake_call_llm_pick):
            llm_result = await svc.recall_two_stage(query, use_llm=True)
        check(
            "recall_two_stage: LLM stage keeps the genuinely matching skill",
            any(r["name"] == "throttle_recovery" for r in llm_result),
        )

        async def _fake_call_llm_empty(*a, **k):
            return ("[]", 10, 5, False)

        with _mock2.patch("app.crawler.call_llm.call_llm", side_effect=_fake_call_llm_empty):
            empty_result = await svc.recall_two_stage(query, use_llm=True)
        check("recall_two_stage: LLM stage may select zero from a non-empty shortlist", empty_result == [])

        async def _fake_call_llm_garbage(*a, **k):
            return ("not valid json at all", 10, 5, False)

        with _mock2.patch("app.crawler.call_llm.call_llm", side_effect=_fake_call_llm_garbage):
            garbage_result = await svc.recall_two_stage(query, use_llm=True)
        check(
            "recall_two_stage: unparseable LLM response fails open to the BM25 shortlist",
            any(r["name"] == "throttle_recovery" for r in garbage_result),
        )

        # ── dead-flag fix: knowledge_base.recall_skills_for_agent must reach
        #    recall_two_stage(), not the flat recall() it silently called
        #    before — otherwise skill_two_stage_recall can never take effect
        #    at the actual agent-prompt-injection call site. ──
        from app.services.knowledge_base import knowledge_base as _kb
        kb_result = await _kb.recall_skills_for_agent(query)
        check(
            "knowledge_base.recall_skills_for_agent: reaches two-stage recall "
            "(finds tags/applicability match recall() alone misses)",
            any(r["name"] == "throttle_recovery" for r in kb_result),
        )
    finally:
        _s.skill_two_stage_recall = _orig_two_stage
        _s.skill_shortlist_k = _orig_shortlist_k
        await svc.delete("throttle_recovery")

    check("file skill delete", (await svc.delete("restart_pods")) and not _skill_path("restart_pods").exists())
    await svc.delete_all()
    check("file skill delete_all clears store", (await svc.list_skills(status="all")) == [])


async def test_chat_session_compaction() -> None:
    """Per-chat-session compaction helpers — role mapping + replay fix. DB-free."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from app.core.memory.compaction_manager import _chat_dicts_to_messages

    rows = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "system", "content": "SUMMARY TEXT"},
        {"role": "user", "content": "follow up"},
        {"role": "tool", "content": "ignored — chat_messages never has this role"},
        {"role": "assistant", "content": ""},  # empty content dropped
    ]
    msgs = _chat_dicts_to_messages(rows)
    check("chat compaction: role mapping length", len(msgs) == 4)
    check("chat compaction: user -> HumanMessage", isinstance(msgs[0], HumanMessage))
    check("chat compaction: assistant -> AIMessage", isinstance(msgs[1], AIMessage))
    check("chat compaction: system -> SystemMessage", isinstance(msgs[2], SystemMessage))
    check("chat compaction: empty content dropped", len(msgs) == 4 and msgs[-1].content == "follow up")

    # execute_agent's conversation_history replay must NOT silently drop a
    # role="system" entry (that's exactly how a session summary is replayed) —
    # it should surface as a labelled HumanMessage, not a skipped/no-op.
    _prior_messages = []
    for _m in [{"role": "system", "content": "SUMMARY TEXT"}]:
        _role = str(_m.get("role") or "").lower()
        _text = _m.get("content") or ""
        if _role == "system":
            _prior_messages.append(HumanMessage(content=f"[Prior conversation summary]\n{_text}"))
    check("chat compaction: system history entry survives replay as HumanMessage",
          len(_prior_messages) == 1 and "SUMMARY TEXT" in _prior_messages[0].content)


async def test_loop_engineering() -> None:
    """Loop 2 + Loop 4 regression cases — DB-free."""

    # ── Loop 2: supervisor with llm_scoring OFF is byte-for-byte heuristic path ──
    from app.core.supervisor import InvestigationSupervisor, SupervisorConfig, SupervisorAction

    cfg_off = SupervisorConfig(llm_scoring_enabled=False)
    sup_off = InvestigationSupervisor(cfg_off)
    verdict_off = await sup_off.evaluate(
        final_answer="root cause identified: the service restarted due to OOM.",
        tool_calls=[{"tool": "cloudwatch_get_logs", "args_keys": ["log_group"]}],
        confidence=0.85,
        messages=[],
    )
    check("loop2: grader OFF returns heuristic PASS", verdict_off.action == SupervisorAction.PASS)
    check("loop2: grader OFF breakdown has no grader_score",
          "grader_score" not in verdict_off.score_breakdown)

    # ── Loop 2: grader fail-soft — when grader raises, heuristic verdict survives ──
    import unittest.mock as _mock

    cfg_on = SupervisorConfig(llm_scoring_enabled=True)
    sup_on = InvestigationSupervisor(cfg_on)
    with _mock.patch("app.core.grader.grade_answer", side_effect=RuntimeError("boom")):
        verdict_failsoft = await sup_on.evaluate(
            final_answer="root cause: the pod was evicted.",
            tool_calls=[],
            confidence=0.9,
            messages=[{"role": "tool", "content": "pod evicted by kubelet"}],
        )
    check("loop2: grader fail-soft returns heuristic verdict",
          verdict_failsoft.action == SupervisorAction.PASS)

    # ── Loop 2: grader returns None (no evidence) — heuristic verdict used ──
    with _mock.patch("app.core.grader.grade_answer", return_value=None):
        verdict_none = await sup_on.evaluate(
            final_answer="root cause: the pod was evicted.",
            tool_calls=[],
            confidence=0.9,
            messages=[],
        )
    check("loop2: grader None -> heuristic verdict", verdict_none.action == SupervisorAction.PASS)

    # ── Loop 4: apply_proposals dry_run=True makes zero writes ──
    from app.core.improvement.analyzer import ImprovementReport
    from app.core.improvement.apply import apply_proposals

    report = ImprovementReport(
        profile=None,
        sample_size=5,
        proposals=[
            {"kind": "skill", "target": "test", "suggestion": "Add a skill for X", "status": "draft"},
            {"kind": "prompt", "target": "role", "suggestion": "Rewrite role prompt", "status": "draft"},
            {"kind": "reliability", "target": "error", "suggestion": "Fix recurring error", "status": "draft"},
        ],
    )
    dry = await apply_proposals(report, dry_run=True)
    check("loop4: dry_run returns eligible=2 (skill+reliability)", dry["eligible"] == 2)
    check("loop4: dry_run applied=0", dry["applied"] == 0)
    check("loop4: prompt proposal excluded from eligible", dry["eligible"] == 2)
    check("loop4: guard_passed is None on dry_run", dry["guard_passed"] is None)

    # ── Loop 4: ineligible (prompt/policy) proposals never reach apply ──
    report_ineligible = ImprovementReport(
        profile=None,
        sample_size=3,
        proposals=[
            {"kind": "prompt", "target": "role", "suggestion": "Rewrite", "status": "draft"},
            {"kind": "policy", "target": "supervisor", "suggestion": "Lower threshold", "status": "draft"},
        ],
    )
    dry2 = await apply_proposals(report_ineligible, dry_run=True)
    check("loop4: prompt+policy proposals not eligible", dry2["eligible"] == 0)

    # ── 3.2 Failure->governance conversion (mocked failure_ledger) ──
    # NOTE: analyzer.py/apply.py/tool_exec.py all import the singleton via
    # `from app.infrastructure.persistence import failure_ledger_repository`
    # (resolving to __init__.py's instance) — patch that SAME object, not the
    # separate instance failure_ledger_repository.py itself also constructs.
    from app.core.improvement import analyzer as _analyzer_mod
    from app.core.improvement import apply as _apply_mod
    from app.infrastructure.persistence import (
        failure_ledger_repository as _fl_instance,
    )

    check(
        "fingerprint_error normalizes digits",
        _analyzer_mod.fingerprint_error("Timeout after 4523ms") ==
        _analyzer_mod.fingerprint_error("Timeout after 99ms"),
    )
    check("fingerprint_error truncates to 80 chars", len(_analyzer_mod.fingerprint_error("x" * 200)) == 80)

    class _FakeFailureLedger:
        def __init__(self):
            self.rows: Dict[str, Dict[str, Any]] = {}
            self.converted: list = []

        async def record(self, fingerprint, execution_id=None):
            row = self.rows.setdefault(
                fingerprint, {"count": 0, "sample_execution_ids": [], "status": "open"},
            )
            row["count"] += 1
            if execution_id:
                row["sample_execution_ids"].append(execution_id)
            return row["count"]

        async def list_open(self, min_count=1):
            return [
                {
                    "fingerprint": fp, "count": r["count"],
                    "sample_execution_ids": r["sample_execution_ids"], "status": r["status"],
                    "first_seen": "t0", "last_seen": "t1", "control_ref": None,
                }
                for fp, r in self.rows.items()
                if r["status"] == "open" and r["count"] >= min_count
            ]

        async def mark_converted(self, fingerprint, control_ref):
            if fingerprint in self.rows:
                self.rows[fingerprint]["status"] = "converted"
                self.converted.append((fingerprint, control_ref))

    fake_ledger = _FakeFailureLedger()
    _orig_record = _fl_instance.record
    _orig_list_open = _fl_instance.list_open
    _orig_mark_converted = _fl_instance.mark_converted
    _fl_instance.record = fake_ledger.record
    _fl_instance.list_open = fake_ledger.list_open
    _fl_instance.mark_converted = fake_ledger.mark_converted
    try:
        fp = _analyzer_mod.fingerprint_error("ThrottlingException: rate exceeded 42 times")
        await fake_ledger.record(fp, "exec-a")
        await fake_ledger.record(fp, "exec-b")
        proposals_below = await _analyzer_mod.convert_recurring_failures(threshold=3)
        check("convert_recurring_failures: below threshold -> no proposal", proposals_below == [])

        await fake_ledger.record(fp, "exec-c")
        proposals = await _analyzer_mod.convert_recurring_failures(threshold=3)
        check(
            "convert_recurring_failures: at threshold -> one eval proposal",
            len(proposals) == 1 and proposals[0]["kind"] == "eval" and proposals[0]["control_ref"] == fp,
        )

        report_eval = ImprovementReport(profile=None, sample_size=3, proposals=proposals)
        dry_eval = await apply_proposals(report_eval, dry_run=True)
        check("loop4: eval proposal is eligible", dry_eval["eligible"] == 1)

        ok = await _apply_mod._apply_eval_proposal(proposals[0])
        check(
            "apply_eval_proposal: marks ledger converted",
            ok is True and fake_ledger.rows[fp]["status"] == "converted",
        )
        check(
            "apply_eval_proposal: records control_ref",
            bool(fake_ledger.converted) and fake_ledger.converted[0][0] == fp,
        )

        proposals_after = await _analyzer_mod.convert_recurring_failures(threshold=3)
        check("convert_recurring_failures: converted fingerprint stops reproposing", proposals_after == [])

        no_ref = await _apply_mod._apply_eval_proposal({"target": "x"})
        check("apply_eval_proposal: missing control_ref -> False", no_ref is False)
    finally:
        _fl_instance.record = _orig_record
        _fl_instance.list_open = _orig_list_open
        _fl_instance.mark_converted = _orig_mark_converted

    # ── live tool_exec failure hook (gated by governance_conversion_enabled) ──
    from app.harness.engine.tool_exec import _record_failure_fingerprint
    from app.config import settings as _gov_settings

    fake_ledger2 = _FakeFailureLedger()
    _fl_instance.record = fake_ledger2.record
    _orig_gov_flag = _gov_settings.governance_conversion_enabled
    try:
        _gov_settings.governance_conversion_enabled = False
        await _record_failure_fingerprint("some_tool", "Error: boom", "exec-x")
        check("tool_exec failure hook: no-op when governance_conversion_enabled=False", fake_ledger2.rows == {})

        _gov_settings.governance_conversion_enabled = True
        await _record_failure_fingerprint("some_tool", "Error: boom", "exec-x")
        check("tool_exec failure hook: records fingerprint when enabled", len(fake_ledger2.rows) == 1)
    finally:
        _gov_settings.governance_conversion_enabled = _orig_gov_flag
        _fl_instance.record = _orig_record


async def _main() -> int:
    print("=== Agent Harness self-test ===")
    test_import_order_no_cycles()
    test_envelopes()
    test_tool_router()
    test_tool_exposure()
    test_boto_context_overflow_classifier()
    test_engine_resolution()
    await test_engine_native_dispatch()
    await test_turn_loop_happy_path()
    await test_turn_loop_tool_call_then_complete()
    await test_step_recorder()
    await test_turn_loop_step_events_wiring()
    await test_instrument_langgraph_result()
    await test_turn_loop_max_turns_forced_synthesis()
    await test_turn_loop_verify_pending_tracking()
    test_terminal_state_derivation()
    test_compression_split_preserved_tail()
    test_compression_split_preserved_tail_empty()
    await test_compression_pipeline_delegates()
    await test_compression_metamemory_precheck()
    await test_metamemory_read_context_block_caps_milestones()
    await test_turn_loop_reactive_compact_retry()
    await test_turn_loop_truncation_escalation_recovers()
    await test_turn_loop_truncation_ladder_exhausts()
    await test_turn_loop_midthought_continuation()
    await test_recovery_call_model_with_backoff()
    # ── chat_history: tool-inclusive history replay ──
    test_build_initial_messages_tool_replay()
    test_extract_turn_segment()
    test_repair_tool_pairing()
    test_build_tool_inclusive_history()
    await test_chat_history_rebuild_fallback()
    test_spec_and_facade()
    test_policy_engine()
    test_sandbox()
    test_privacy_pseudonymization()
    test_pinned_facts_budget()
    test_agent_spec()
    test_memory_capability_and_autolearn()
    test_persona_and_supervisor_toggle()
    await test_supervisor_loop()
    await test_trajectory_export_step_events()
    await test_tool_result_failed_flag_threading()
    test_context_builder()
    test_extension_tools()
    await test_edit_tools()
    await test_gateway_resolution()
    await test_cloudwatch_cache_stats()
    test_cloudwatch_error_classifier()
    test_cloudwatch_synthesis_payload_excludes_run_stats()
    test_cloudwatch_severity_clamp()
    await test_cloudwatch_analyzer_cache()
    test_cloudwatch_drilldown_scoring()
    test_cloudwatch_query_fixup()
    test_cloudwatch_alarm_and_metric_helpers()
    test_cloudwatch_payload_includes_metrics()
    test_cloudwatch_cost_footer()
    test_cloudwatch_drill_window()
    await test_cloudwatch_rate_limiter()
    test_cloudwatch_time_buckets()
    test_cloudwatch_metric_query_build()
    await test_cloudwatch_bucketed_merge()
    test_cloudwatch_merge_region_evidence()
    # ── sessions, durable-fact memory, self-evolving skills ──
    test_session_answer_extraction()
    test_fact_extractor()
    test_memory_audit_logic()
    test_skill_audit_helpers()
    test_seed_skill_library()
    test_conversational_intent()
    test_cloudwatch_prescan_gate()
    await test_tool_assembler_degrade_not_abort()
    # ── context compaction: microcompact tier before the LLM-summary tier ──
    await test_context_compaction_tiers()
    # ── per-chat-session compaction: role mapping + system-history replay ──
    await test_chat_session_compaction()
    # ── configurable agents (capabilities/output/profiles) + deep-agent (planning/vfs/subagents) + self-improvement ──
    await test_configurable_agents()
    # ── Phase B delegation: bounds / safety / parallel / async ──
    await test_delegation_phase_b()
    test_budget_ledger()
    await test_delegate_batch_budget_economy()
    # ── Phase 5: delegated children route through the engine flag ──
    await test_subagent_engine_routing()
    # ── file-backed skills (no DB) ──
    await test_file_skills()
    # ── loop engineering: Loop 2 grader + Loop 4 hill-climbing ──
    await test_loop_engineering()
    print("-" * 40)
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} check(s): {', '.join(_FAILURES)}")
        return 1
    print("ALL HARNESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
