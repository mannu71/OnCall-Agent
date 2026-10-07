"""AgentRun against a fake API: live events, final answer, HITL, cancel, re-attach."""
import time

from oncall_ui.api import ApiError
from oncall_ui.runs import AgentRun


class FakeStream:
    def __init__(self, lines, gate=None):
        self.lines, self.gate, self.closed = lines, gate, False

    def iter_lines(self, decode_unicode=True):
        for line in self.lines:
            if self.gate and line == "__WAIT__":
                self.gate.wait(5)
                continue
            if self.closed:
                return
            yield line

    def close(self):
        self.closed = True


class FakeApi:
    def __init__(self, stream_lines, result=None, delay=0.0, exc=None, session=None):
        self.stream_lines, self.result, self.delay, self.exc = stream_lines, result, delay, exc
        self.session = session
        self.calls = []

    def workflow_stream(self, name):
        return FakeStream(self.stream_lines)

    def execute_workflow(self, name, **kw):
        self.calls.append(("execute", kw))
        time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result

    def cancel_workflow(self, name):
        self.calls.append(("cancel", name))

    def approve_hitl(self, eid, rid, approved, reason):
        self.calls.append(("approve", eid, rid, approved))

    def get_session(self, sid):
        return self.session


def _wait(run, timeout=5):
    end = time.time() + timeout
    while run.active and time.time() < end:
        time.sleep(0.02)


STREAM = [
    "event: tool_call", 'data: {"data": {"tool": "cloudwatch_search_logs", "args": {"q": "err"}}}', "",
    "event: tool_result", 'data: {"data": {"tool": "cloudwatch_search_logs", "result": "3 hits"}}', "",
    "event: llm_token", 'data: {"data": {"token": "Root "}}', "",
    "event: token_usage_delta", 'data: {"data": {"input_tokens": 100, "output_tokens": 20, '
                                '"context_window_size": 200000, "context_used_pct": 1.5}}', "",
    "event: hitl_pause", 'data: {"data": {"execution_id": "ex1", "request_id": "r1", "tool": "edit_file", '
                         '"args": {"file": "a.py"}}}', "",
    "event: hitl_pause", 'data: {"data": {"execution_id": "ex1", "request_id": "r1", "tool": "edit_file"}}', "",
    "event: stream_end", "data: {}", "",
]


def test_run_collects_events_and_final_answer():
    result = {"agent_1": {"final_answer": "Root cause: X", "selected_skills": ["rca"]},
              "input_tokens": 120, "output_tokens": 30,
              "results": {"agent_1": {"structured_output": {"root_cause": "X"}}}}
    api = FakeApi(STREAM, result=result, delay=0.2)
    run = AgentRun("wf", "why?", session_id="s1", history=[{"role": "user", "content": "hi"}],
                   api_factory=lambda: api).start()
    time.sleep(0.1)
    snap = run.snapshot()
    assert snap["steps"][0].name == "cloudwatch_search_logs" and snap["steps"][0].status == "done"
    assert snap["streamed_text"] == "Root "
    assert len(snap["approvals"]) == 1  # deduped by request id
    assert run.resolve_approval(True) is None
    assert ("approve", "ex1", "r1", True) in api.calls
    _wait(run)
    assert run.status == "done"
    assert run.final_answer == "Root cause: X"
    assert run.tokens == {"input": 120, "output": 30, "total": 150}
    assert run.selected_skills == ["rca"] and run.structured == {"root_cause": "X"}
    kw = api.calls[0][1]
    assert kw["query"] == "why?" and kw["session_id"] == "s1" and kw["history"]


def test_run_error_and_already_running():
    run = AgentRun("wf", "q", api_factory=lambda: FakeApi([], exc=ApiError("boom"))).start()
    _wait(run)
    assert run.status == "error" and "boom" in run.error
    run2 = AgentRun("wf", "q", api_factory=lambda: FakeApi([], result={"status": "already_running"})).start()
    _wait(run2)
    assert run2.status == "error" and "already running" in run2.error


def test_cancel_releases_immediately_and_cancels_server_side():
    api = FakeApi([], result={"agent": {"final_answer": "late"}}, delay=1.0)
    run = AgentRun("wf", "q", api_factory=lambda: api).start()
    run.cancel()
    assert run.status == "cancelled" and run.final_answer == "Stopped by user."
    assert ("cancel", "wf") in api.calls
    time.sleep(1.2)
    assert run.status == "cancelled" and run.final_answer == "Stopped by user."  # late result ignored


def test_reattach_reads_answer_from_session():
    session = {"messages": [{"role": "user", "content": "q"},
                            {"role": "assistant", "content": "persisted answer",
                             "metadata": {"selected_skills": ["s"]}}]}
    api = FakeApi(["event: llm_token", 'data: {"data": {"token": "x"}}', "", "event: stream_end", "data: {}", ""],
                  session=session)
    run = AgentRun("wf", session_id="s1", reattach=True, api_factory=lambda: api).start()
    _wait(run)
    assert run.status == "done" and run.final_answer == "persisted answer"
    assert not [c for c in api.calls if c[0] == "execute"]
