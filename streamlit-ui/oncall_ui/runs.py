"""Background agent runs with live progress.

Streamlit re-executes the page script on every interaction, so a chat turn
cannot simply block on the (minutes-long) synchronous ``/execute`` POST. An
:class:`AgentRun` instead owns two daemon threads:

* the **execute** thread posts ``/workflows/{name}/execute`` and holds the
  authoritative final answer when it returns;
* the **stream** thread follows ``/workflows/{name}/stream`` (SSE) for live
  tokens, tool calls, token usage and HITL approval requests.

The page polls the run from an auto-refreshing fragment, so the user can
approve/deny gated tools or stop the run while it is in flight, and can leave
the page and come back without losing it (the run lives in session state).

A run can also *re-attach* to an execution that is already in flight (e.g.
after a browser refresh): no POST is made, only the stream is followed, and
the final answer is read back from the persisted chat session.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .api import AgentApi, ApiError
from .results import (
    extract_final_answer, extract_node_field, extract_privacy_redactions,
    extract_selected_skills, extract_tokens,
)
from .sse import iter_sse

WATCHDOG_SILENCE_S = 90


@dataclass
class ToolStep:
    name: str
    args: Any
    agent: str = "agent"
    model: str = ""
    status: str = "running"  # running | done | error
    result: Any = None
    started_at: float = field(default_factory=time.time)
    duration_ms: Optional[int] = None


@dataclass
class Approval:
    execution_id: str
    request_id: str
    tool: str
    args: Any
    message: str = ""


class AgentRun:
    """One chat turn (or re-attached in-flight run) against an agent workflow."""

    def __init__(self, workflow_name: str, query: str = "", *, session_id: Optional[str] = None,
                 history: Optional[list] = None, reattach: bool = False,
                 api_factory: Callable[[], AgentApi] = AgentApi):
        self.workflow_name = workflow_name
        self.query = query
        self.session_id = session_id
        self.history = history or []
        self.reattach = reattach
        self._api_factory = api_factory

        self.lock = threading.Lock()
        self.status = "running"          # running | done | error | cancelled
        self.streamed_text = ""
        self.steps: List[ToolStep] = []
        self.status_history: List[str] = []
        self.trace: List[Dict[str, str]] = []
        self.approvals: List[Approval] = []
        self.tokens: Dict[str, int] = {"input": 0, "output": 0, "total": 0}
        self.context: Optional[Dict[str, Any]] = None
        self.final_answer = ""
        self.raw: Any = None
        self.error = ""
        self.privacy_redactions: list = []
        self.selected_skills: list = []
        self.structured: Any = None
        self.todos: Any = None
        self.session_data: Optional[dict] = None
        self.started_at = time.time()
        self.last_activity = time.time()
        self.finished_at: Optional[float] = None

        self._stream_resp = None
        self._stream_done = threading.Event()
        self._cancelled = threading.Event()
        self._threads: List[threading.Thread] = []

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> "AgentRun":
        self._push_trace("think", f'Initializing agent run for: "{self.workflow_name}"…')
        if self.query:
            q = self.query if len(self.query) <= 50 else self.query[:50] + "…"
            self._push_trace("think", f'Received request: "{q}"')
        stream = threading.Thread(target=self._follow_stream, daemon=True, name="agent-run-sse")
        self._threads.append(stream)
        stream.start()
        if not self.reattach:
            run = threading.Thread(target=self._execute, daemon=True, name="agent-run-exec")
            self._threads.append(run)
            run.start()
        return self

    @property
    def active(self) -> bool:
        return self.status == "running"

    @property
    def stale(self) -> bool:
        """True after a long stretch with no stream activity (possibly stuck)."""
        return self.active and (time.time() - self.last_activity) > WATCHDOG_SILENCE_S

    def cancel(self) -> None:
        """Stop the run: release the UI immediately, then cancel server-side."""
        if not self.active:
            return
        self._cancelled.set()
        with self.lock:
            self.status = "cancelled"
            self.final_answer = "Stopped by user."
            self.finished_at = time.time()
            self.approvals.clear()
        self._close_stream()
        try:
            self._api_factory().cancel_workflow(self.workflow_name)
        except ApiError:
            pass  # already finished, or the cancel itself failed — UI is released either way

    def resolve_approval(self, approved: bool) -> Optional[str]:
        """Approve/deny the head pending approval. Returns an error message, if any."""
        with self.lock:
            if not self.approvals:
                return None
            head = self.approvals.pop(0)
        try:
            self._api_factory().approve_hitl(head.execution_id, head.request_id, approved,
                                             "" if approved else "Denied by operator")
        except ApiError as exc:
            self._push_trace("answer", f"Approval failed: {exc}")
            return str(exc)
        return None

    # ── workers ───────────────────────────────────────────────────────────
    def _execute(self) -> None:
        api = self._api_factory()
        try:
            data = api.execute_workflow(self.workflow_name, query=self.query,
                                        session_id=self.session_id, history=self.history)
        except ApiError as exc:
            self._finish(error=str(exc))
            return
        finally:
            # The sync response is authoritative; stop listening for progress.
            self._close_stream()
        if self._cancelled.is_set():
            return
        if isinstance(data, dict) and data.get("status") == "already_running":
            self._finish(error=f'Agent "{self.workflow_name}" is already running. Wait for it to finish.')
            return
        if isinstance(data, dict) and data.get("status") == "inactive":
            self._finish(error=data.get("message") or "Workflow is not active.")
            return
        if isinstance(data, dict) and data.get("status") == "cancelled":
            self._finish(cancelled=True)
            return
        with self.lock:
            self.raw = data
            self.final_answer = (extract_final_answer(data) or self.streamed_text
                                 or "Agent completed (no answer text returned).")
            tok = extract_tokens(data)
            if tok:
                self.tokens = tok
            if isinstance(data, dict) and data.get("context_window_size"):
                self.context = {"pct": data.get("context_used_pct") or 0,
                                "used": data.get("context_used_tokens") or 0,
                                "window": data.get("context_window_size") or 0}
            self.privacy_redactions = extract_privacy_redactions(data)
            self.selected_skills = extract_selected_skills(data)
            self.structured = extract_node_field(data, "structured_output")
            todos = extract_node_field(data, "todos")
            self.todos = todos if isinstance(todos, list) and todos else None
        self._push_trace("answer", "Returned final analysis + recommendations.")
        self._finish()

    def _follow_stream(self) -> None:
        api = self._api_factory()
        try:
            resp = api.workflow_stream(self.workflow_name)
        except ApiError:
            self._stream_done.set()
            if self.reattach:
                self._reconcile_from_session()
            return
        self._stream_resp = resp
        try:
            for ev in iter_sse(resp.iter_lines(decode_unicode=True)):
                if self._cancelled.is_set():
                    break
                self._handle_event(ev.event, ev.data)
                if ev.event == "stream_end":
                    break
        except Exception:  # noqa: BLE001 — a dropped progress stream is never fatal
            pass
        finally:
            self._close_stream()
            self._stream_done.set()
        if self.reattach and not self._cancelled.is_set():
            self._reconcile_from_session()

    def _reconcile_from_session(self) -> None:
        """Re-attach path: pull the authoritative answer from the persisted session."""
        if not self.session_id:
            self._finish()
            return
        try:
            data = self._api_factory().get_session(self.session_id)
        except ApiError:
            with self.lock:
                self.final_answer = self.streamed_text or "Agent completed."
            self._finish()
            return
        msgs = data.get("messages") or []
        last = next((m for m in reversed(msgs) if m.get("role") == "assistant"), None) or {}
        meta = last.get("metadata") or {}
        with self.lock:
            self.session_data = data
            self.final_answer = (last.get("content") or self.streamed_text
                                 or "Agent completed (no answer text returned).")
            self.privacy_redactions = meta.get("privacy_redactions") or []
            self.selected_skills = meta.get("selected_skills") or []
            if meta.get("context_window_size"):
                self.context = {"pct": meta.get("context_used_pct") or 0,
                                "used": meta.get("context_used_tokens") or 0,
                                "window": meta.get("context_window_size") or 0}
        self._finish()

    # ── event handling ────────────────────────────────────────────────────
    def _handle_event(self, event: str, d: Dict[str, Any]) -> None:
        if event in ("tool_call", "tool_started"):
            name = d.get("tool") or d.get("name")
            if name:
                self._on_tool_call(name, d.get("args") or {}, d.get("agent"), d.get("model"))
        elif event == "tool_result":
            name = d.get("tool") or d.get("name")
            if name:
                self._on_tool_result(name, d.get("result", d.get("output")), bool(d.get("failed")),
                                     d.get("agent"), d.get("model"))
        elif event == "llm_token":
            tok = d.get("token") or d.get("text") or ""
            if tok:
                with self.lock:
                    self.streamed_text += tok
                    self.last_activity = time.time()
        elif event in ("node_started", "node_completed"):
            node = d.get("node_id") or d.get("nodeId")
            self._push_trace("think", f"{node} {'started' if event == 'node_started' else 'completed'}")
        elif event == "agent_progress":
            self._push_status(d.get("message") or "")
        elif event == "token_usage_delta":
            with self.lock:
                inp, out = d.get("input_tokens") or 0, d.get("output_tokens") or 0
                self.tokens = {"input": inp, "output": out, "total": d.get("total_tokens") or inp + out}
                if d.get("context_window_size") is not None:
                    self.context = {"pct": d.get("context_used_pct") or 0,
                                    "used": d.get("context_used_tokens") or 0,
                                    "window": d.get("context_window_size") or 0}
                self.last_activity = time.time()
        elif event == "hitl_pause":
            ap = Approval(execution_id=d.get("execution_id") or "", request_id=d.get("request_id") or "",
                          tool=d.get("tool") or "", args=d.get("args"), message=d.get("message") or "")
            self._push_status(f"Awaiting approval: {ap.tool}…")
            with self.lock:
                # Dedupe by request id — SSE can redeliver on reconnect.
                if ap.request_id and not any(a.request_id == ap.request_id for a in self.approvals):
                    self.approvals.append(ap)
        elif event == "agent_error":
            err = d.get("error") or d.get("message") or "agent error"
            self._push_trace("answer", f"Error: {err}")
            with self.lock:
                for s in reversed(self.steps):
                    if s.status == "running":
                        s.status, s.result = "error", err
                        s.duration_ms = int((time.time() - s.started_at) * 1000)
                        break
        elif event == "message" and d.get("message"):
            self._push_status(d["message"])

    def _on_tool_call(self, name: str, args: Any, agent: Optional[str], model: Optional[str]) -> None:
        who = agent or "agent"
        with self.lock:
            self.steps.append(ToolStep(name=name, args=args, agent=who, model=model or ""))
            self.last_activity = time.time()
        label = f"[{who}] " if who != "agent" else ""
        self._push_trace("tool", f"{label}Calling {name}…")
        self._push_status(f"{label}Calling {name}…")

    def _on_tool_result(self, name: str, result: Any, failed: bool, agent: Optional[str],
                        model: Optional[str]) -> None:
        who = agent or "agent"
        with self.lock:
            # Match the running step by tool name AND origin first, so a main
            # agent and a subagent calling the same tool don't cross-match.
            idx = next((i for i in range(len(self.steps) - 1, -1, -1)
                        if self.steps[i].status == "running" and self.steps[i].name == name
                        and self.steps[i].agent == who), -1)
            if idx < 0:
                idx = next((i for i in range(len(self.steps) - 1, -1, -1)
                            if self.steps[i].status == "running" and self.steps[i].name == name), -1)
            if idx >= 0:
                s = self.steps[idx]
                s.status = "error" if failed else "done"
                s.result = result
                s.model = s.model or model or ""
                s.duration_ms = int((time.time() - s.started_at) * 1000)
            self.last_activity = time.time()
        label = f"[{who}] " if who != "agent" else ""
        self._push_trace("tool", f"{label}{name} {'failed' if failed else 'returned'}")

    # ── helpers ───────────────────────────────────────────────────────────
    def _push_status(self, msg: str) -> None:
        if not msg:
            return
        with self.lock:
            self.status_history = (self.status_history + [msg])[-8:]
            self.last_activity = time.time()

    def _push_trace(self, level: str, text: str) -> None:
        with self.lock:
            if self.trace and self.trace[-1]["text"] == text:
                return
            self.trace.append({"level": level, "text": text, "time": time.strftime("%H:%M:%S")})

    def _close_stream(self) -> None:
        resp, self._stream_resp = self._stream_resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    def _finish(self, *, error: str = "", cancelled: bool = False) -> None:
        with self.lock:
            if self.status != "running":
                return
            if cancelled:
                self.status, self.final_answer = "cancelled", "Stopped by user."
            elif error:
                self.status, self.error = "error", error
                self.trace.append({"level": "answer", "text": f"Fatal error: {error}",
                                   "time": time.strftime("%H:%M:%S")})
            else:
                self.status = "done"
            self.approvals.clear()
            self.finished_at = time.time()

    def snapshot(self) -> Dict[str, Any]:
        """A consistent copy of the live state for rendering."""
        with self.lock:
            return {
                "status": self.status,
                "streamed_text": self.streamed_text,
                "steps": [ToolStep(**vars(s)) for s in self.steps],
                "status_history": list(self.status_history),
                "trace": list(self.trace),
                "approvals": list(self.approvals),
                "tokens": dict(self.tokens),
                "context": dict(self.context) if self.context else None,
                "final_answer": self.final_answer,
                "error": self.error,
                "stale": self.stale,
            }
