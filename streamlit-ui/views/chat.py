"""Agent chat — persisted sessions, live streaming, tool steps and HITL approvals."""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import streamlit as st

from oncall_ui.api import ApiError, get_api
from oncall_ui.results import clean_llm_text
from oncall_ui.runs import AgentRun, ToolStep
from oncall_ui.timeutils import fmt_clock
from oncall_ui.ui import guarded, load_workflows, load_active, setup_page, viewer_tz
from oncall_ui.workflow_model import agent_model, agent_tools, has_cloudwatch, is_agent_workflow_valid

# Client-side mirror of the backend trace_ids detector (cosmetic badge only).
CORRELATION_ID_RE = re.compile(
    r"\b(1-[0-9a-f]{8}-[0-9a-f]{24}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{12}|[0-9A-Z]{10,}:[0-9A-Fa-f]{8})\b")
HISTORY_TURNS = 12
WELCOME = "Welcome! Pick an agent above, then ask it anything to get started."

setup_page()
tz = viewer_tz()
ss = st.session_state
api = get_api()


# ── state ─────────────────────────────────────────────────────────────────
def _msg(role: str, content: str, **extra) -> Dict[str, Any]:
    return {"id": uuid.uuid4().hex, "role": role, "content": content,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra}


def _reset_chat(*, clear_url: bool = True) -> None:
    ss.chat_session_id = None
    ss.chat_messages = [_msg("system", WELCOME)]
    ss.chat_tokens = {"input": 0, "output": 0, "total": 0}
    ss.chat_session_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    ss.chat_context = {"pct": 0, "used": 0, "window": 0}
    ss.chat_trace = []
    ss.chat_run = None
    if clear_url:
        st.query_params.pop("session", None)


if "chat_messages" not in ss:
    # First visit in this browser session: keep ?session= so it can be restored below.
    _reset_chat(clear_url=False)


def _set_session(sid: Optional[str]) -> None:
    ss.chat_session_id = sid
    if sid:
        st.query_params["session"] = sid
    else:
        st.query_params.pop("session", None)


workflows = guarded(load_workflows, "Cannot connect to agent-api: ") or []
agents = [w for w in workflows if w.get("enabled", True) and is_agent_workflow_valid(w)]
agents_by_name = {a["name"]: a for a in agents}


def resume_session(sid: str) -> None:
    run: Optional[AgentRun] = ss.get("chat_run")
    if run and run.active:
        st.warning("Finish or stop the current run before switching conversations.")
        return
    try:
        data = api.get_session(sid)
    except ApiError as exc:
        if exc.status == 404:
            _set_session(None)
        st.error(f"Error resuming session: {exc}")
        return
    msgs = []
    for m in data.get("messages") or []:
        meta = m.get("metadata") or {}
        role = {"user": "user", "assistant": "agent"}.get(m.get("role"), "system")
        msgs.append(_msg(role, m.get("content") or "", time=m.get("created_at"),
                         privacy=meta.get("privacy_redactions") or [],
                         skills=meta.get("selected_skills") or []))
    _set_session(sid)
    ss.chat_messages = msgs or [_msg("system", WELCOME)]
    ss.chat_tokens = {"input": 0, "output": 0, "total": 0}
    ss.chat_session_tokens = {
        "input": data.get("total_input_tokens") or 0, "output": data.get("total_output_tokens") or 0,
        "cache_read": data.get("total_cache_read_tokens") or 0,
        "cache_creation": data.get("total_cache_creation_tokens") or 0}
    last = next((m for m in reversed(data.get("messages") or []) if m.get("role") == "assistant"), None)
    meta = (last or {}).get("metadata") or {}
    ss.chat_context = {"pct": meta.get("context_used_pct") or 0, "used": meta.get("context_used_tokens") or 0,
                       "window": meta.get("context_window_size") or 0}
    ss.chat_trace = []
    wf = data.get("workflow_name")
    if wf and wf in agents_by_name:
        ss.chat_agent = wf
    # Re-attach if this session's agent is still running (user left mid-run).
    if wf:
        load_active.clear()
        if wf in load_active():
            ss.chat_run = AgentRun(wf, session_id=sid, reattach=True).start()


# Restore the conversation named in the URL (survives refresh / revisit).
if not ss.chat_session_id and st.query_params.get("session") and not ss.get("_chat_restored"):
    ss._chat_restored = True
    resume_session(st.query_params["session"])


def build_history(question: str) -> List[dict]:
    hist = [{"role": {"user": "user", "system": "system"}.get(m["role"], "assistant"), "content": m["content"]}
            for m in ss.chat_messages
            if m["role"] in ("user", "agent", "system") and m.get("content") and not m.get("error")
            and m["content"] != WELCOME][-HISTORY_TURNS:]
    while hist and hist[-1]["role"] == "user" and hist[-1]["content"] == question:
        hist.pop()
    return hist


def ask(agent_name: str, question: str, *, record_user: bool = True) -> None:
    if record_user:
        ss.chat_messages.append(_msg("user", question, trace_id=bool(CORRELATION_ID_RE.search(question))))
    sid = ss.chat_session_id
    if not sid:
        try:
            sid = api.create_session(title=question[:60] or "New chat", workflow_name=agent_name)["id"]
            _set_session(sid)
        except ApiError as exc:
            st.toast(f"Could not create a session (turn will not be saved): {exc}", icon="⚠️")
    history = build_history(question)
    ss.chat_run = AgentRun(agent_name, question, session_id=sid, history=history).start()


def finalize_run(run: AgentRun) -> None:
    snap = run.snapshot()
    if run.status == "error":
        ss.chat_messages.append(_msg("agent", f"Error: {run.error}", error=True, steps=snap["steps"]))
    elif run.status == "cancelled":
        ss.chat_messages.append(_msg("agent", "Stopped by user.", error=True, steps=snap["steps"]))
    else:
        ss.chat_messages.append(_msg("agent", run.final_answer, steps=snap["steps"], structured=run.structured,
                                     todos=run.todos, privacy=run.privacy_redactions,
                                     skills=run.selected_skills))
        tok = snap["tokens"]
        ss.chat_tokens = tok
        raw = run.raw if isinstance(run.raw, dict) else {}
        if run.session_data:  # re-attached: totals come from the persisted session
            d = run.session_data
            ss.chat_session_tokens = {
                "input": d.get("total_input_tokens") or 0, "output": d.get("total_output_tokens") or 0,
                "cache_read": d.get("total_cache_read_tokens") or 0,
                "cache_creation": d.get("total_cache_creation_tokens") or 0}
        else:
            t = ss.chat_session_tokens
            ss.chat_session_tokens = {
                "input": t["input"] + tok.get("input", 0), "output": t["output"] + tok.get("output", 0),
                "cache_read": t["cache_read"] + (raw.get("cache_read_tokens") or 0),
                "cache_creation": t["cache_creation"] + (raw.get("cache_creation_tokens") or 0)}
        if snap["context"]:
            ss.chat_context = snap["context"]
    ss.chat_trace = snap["trace"]
    ss.chat_run = None


# ── rendering ─────────────────────────────────────────────────────────────
def _fmt_args(args: Any) -> str:
    if isinstance(args, (dict, list)):
        return json.dumps(args, indent=2, default=str)[:4000]
    return str(args)[:4000]


def render_steps(steps: List[ToolStep], live: bool = False) -> None:
    if not steps:
        return
    done = sum(1 for s in steps if s.status != "running")
    label = f"🛠️ {len(steps)} tool step{'s' if len(steps) != 1 else ''}" + (f" ({done} done)" if live else "")
    with st.expander(label, expanded=live):
        for s in steps:
            icon = {"running": "⏳", "done": "✅", "error": "❌"}[s.status]
            who = f" · `{s.agent}`" if s.agent and s.agent != "agent" else ""
            model = f" · {s.model}" if s.model else ""
            dur = f" · {s.duration_ms / 1000:.1f}s" if s.duration_ms is not None else ""
            st.markdown(f"{icon} **`{s.name}`**{who}{model}{dur}")
            _render_value("args", s.args)
            if s.result is not None:
                _render_value("result", s.result)


def _render_value(label: str, value: Any) -> None:
    if isinstance(value, (dict, list)) and value:
        st.caption(label)
        st.json(value, expanded=False)
    elif value not in (None, "", {}, []):
        text = str(value)
        st.caption(label)
        st.code(text if len(text) <= 2000 else text[:2000] + "\n… (truncated)", language=None)


def render_extras(m: Dict[str, Any]) -> None:
    if m.get("skills"):
        st.markdown(" ".join(f"`🔧 {s}`" for s in m["skills"]))
    if m.get("privacy"):
        with st.expander(f"🔒 {len(m['privacy'])} value(s) pseudonymized before reaching the model"):
            st.dataframe([{"Type": r.get("type"), "Placeholder": r.get("placeholder"), "Preview": r.get("preview")}
                          for r in m["privacy"]], hide_index=True, use_container_width=True)
    if m.get("todos"):
        with st.container(border=True):
            st.markdown("**Plan**")
            for t in m["todos"]:
                mark = {"completed": "✅", "in_progress": "🔄", "blocked": "⛔"}.get(t.get("status"), "⬜")
                text = t.get("text") or ""
                st.markdown(f"{mark} {'~~' + text + '~~' if t.get('status') == 'completed' else text}")
    rep = m.get("structured")
    if isinstance(rep, dict) and rep:
        with st.container(border=True):
            head = "**Structured report**"
            if rep.get("severity"):
                head += f" · `{rep['severity']}`"
            if isinstance(rep.get("confidence"), (int, float)):
                head += f" · conf {round(rep['confidence'] * 100)}%"
            st.markdown(head)
            if rep.get("root_cause"):
                st.markdown(f"**Root cause:** {rep['root_cause']}")
            if rep.get("evidence"):
                st.markdown("**Evidence**")
                for ev in rep["evidence"]:
                    line = f"{ev.get('file', '')}{':' + str(ev['line']) if ev.get('line') else ''}"
                    if ev.get("symbol"):
                        line += f" — {ev['symbol']}"
                    st.markdown(f"- `{line}`")
            if rep.get("next_steps"):
                st.markdown("**Next steps**")
                for s in rep["next_steps"]:
                    st.markdown(f"- {s}")


def render_message(m: Dict[str, Any], is_last_agent: bool, running: bool) -> None:
    if m["role"] == "system":
        st.info(m["content"], icon="ℹ️")
        return
    avatar = "🧑" if m["role"] == "user" else "🤖"
    with st.chat_message("user" if m["role"] == "user" else "assistant", avatar=avatar):
        if m["role"] == "user":
            st.markdown(m["content"])
            if m.get("trace_id"):
                st.caption("🔗 Correlation / trace ID detected — the agent will correlate logs across services.")
            return
        render_steps(m.get("steps") or [])
        if m.get("error"):
            st.error(m["content"])
        else:
            st.markdown(clean_llm_text(m["content"]))
        render_extras(m)
        cols = st.columns([1, 1, 8])
        st.caption(fmt_clock(m.get("time"), tz))
        with cols[0].popover("📋", help="Copy answer"):
            st.code(m["content"], language="markdown")
        if is_last_agent and not running and cols[1].button("↻", key=f"regen_{m['id']}", help="Regenerate"):
            last_user = next((x for x in reversed(ss.chat_messages) if x["role"] == "user"), None)
            if last_user and ss.get("chat_agent"):
                ask(ss.chat_agent, last_user["content"], record_user=False)
                st.rerun()


# ── header ────────────────────────────────────────────────────────────────
run: Optional[AgentRun] = ss.get("chat_run")
running = bool(run and run.active)

h1, h2, h3, h4 = st.columns([4, 1.2, 1.2, 1.2], vertical_alignment="bottom")
with h1:
    if not agents:
        st.warning("No agents available. Build one on the Workflows page (Agent node + LLM + at least one tool).")
    names = list(agents_by_name)
    current = ss.get("chat_agent") if ss.get("chat_agent") in agents_by_name else (names[0] if names else None)
    picked = st.selectbox(
        "Agent", names, index=names.index(current) if current in names else None, disabled=running,
        format_func=lambda n: f"{n}  ☁️" if has_cloudwatch(agents_by_name[n]) else n,
        placeholder="Select an agent")
    if picked and picked != ss.get("chat_agent"):
        if ss.get("chat_agent"):
            ss.chat_messages.append(_msg("system", f"Selected agent: **{picked}**. Ask it anything to get started."))
        ss.chat_agent = picked
with h2:
    if st.button("New chat", icon=":material/add:", use_container_width=True, disabled=running):
        _reset_chat()
        st.rerun()
with h3:
    with st.popover("History", icon=":material/history:", use_container_width=True):
        sessions = guarded(lambda: api.list_sessions(), quiet=True) or []
        if not sessions:
            st.caption("No saved conversations yet. Ask an agent something to start one.")
        for s in sessions:
            c1, c2, c3 = st.columns([6, 1, 1], vertical_alignment="center")
            label = ("📌 " if s.get("is_important") else "") + (s.get("title") or "Untitled")
            if s.get("id") == ss.chat_session_id:
                label = "▶ " + label
            if c1.button(label, key=f"sess_{s['id']}", use_container_width=True,
                         help=f"{s.get('message_count', 0)} msgs · {fmt_clock(s.get('last_message_at') or s.get('updated_at'), tz)}"):
                resume_session(s["id"])
                st.rerun()
            if c2.button("📌", key=f"pin_{s['id']}", help="Unpin" if s.get("is_important") else "Pin"):
                guarded(lambda: api.update_session(s["id"], is_important=not s.get("is_important")))
                st.rerun()
            if c3.button("🗑️", key=f"dels_{s['id']}", help="Delete conversation"):
                guarded(lambda: api.delete_session(s["id"]))
                if s["id"] == ss.chat_session_id:
                    _reset_chat()
                st.rerun()
with h4:
    with st.popover("Details", icon=":material/info:", use_container_width=True):
        agent_wf = agents_by_name.get(ss.get("chat_agent"))
        if agent_wf:
            st.markdown(f"**Model:** {agent_model(agent_wf)}")
            tools = agent_tools(agent_wf)
            st.markdown(f"**Connected tools ({len(tools)}):**")
            for t in tools:
                st.markdown(f"- `{t}`")
        t, sess, ctx = ss.chat_tokens, ss.chat_session_tokens, ss.chat_context
        st.divider()
        st.markdown(f"**Last turn:** {t.get('input', 0):,} in · {t.get('output', 0):,} out")
        st.markdown(f"**Session:** {sess['input']:,} in · {sess['output']:,} out"
                    + (f" · {sess['cache_read']:,} cache-read" if sess["cache_read"] else ""))
        if ctx.get("window"):
            st.progress(min(1.0, (ctx.get("pct") or 0) / 100),
                        text=f"Context {ctx.get('pct', 0):.0f}% · {ctx.get('used', 0):,} / {ctx['window']:,}")
        st.divider()
        st.markdown("**Run trace**")
        for step in (run.snapshot()["trace"] if run else ss.chat_trace) or []:
            st.caption(f"{step['time']} · {step['level']} · {step['text']}")

# ── conversation ──────────────────────────────────────────────────────────
last_agent_idx = max((i for i, m in enumerate(ss.chat_messages) if m["role"] == "agent"), default=-1)
for i, m in enumerate(ss.chat_messages):
    render_message(m, i == last_agent_idx, running)


@st.fragment(run_every=1.0 if running else None)
def live_run():
    r: Optional[AgentRun] = ss.get("chat_run")
    if not r:
        return
    if not r.active:
        finalize_run(r)
        st.rerun()
    snap = r.snapshot()
    with st.chat_message("assistant", avatar="🤖"):
        label = snap["status_history"][-1] if snap["status_history"] else "Thinking…"
        with st.status(label, state="running", expanded=False):
            for line in snap["status_history"]:
                st.caption(line)
        render_steps(snap["steps"], live=True)
        if snap["streamed_text"]:
            st.markdown(clean_llm_text(snap["streamed_text"]) + " ▌")
        t = snap["tokens"]
        if t.get("total"):
            st.caption(f"{t['input']:,} in · {t['output']:,} out"
                       + (f" · context {snap['context']['pct']:.0f}%" if snap["context"] else ""))
        if snap["stale"]:
            st.warning("No activity for 90s — the run may be stuck. You can stop it and try again.")
        if snap["approvals"]:
            ap = snap["approvals"][0]
            with st.container(border=True):
                more = f" (+{len(snap['approvals']) - 1} more pending)" if len(snap["approvals"]) > 1 else ""
                st.markdown(f"**🛡️ Approval required{more}** — the agent wants to run `{ap.tool}`"
                            + (f" on `{ap.args.get('file')}`" if isinstance(ap.args, dict) and ap.args.get("file") else ""))
                if ap.tool == "edit_file" and isinstance(ap.args, dict):
                    if ap.args.get("repo"):
                        st.caption(f"Repo: {ap.args['repo']}")
                    st.markdown(":red[**− Replace**]")
                    st.code(ap.args.get("old_string") or "", language=None)
                    st.markdown(":green[**+ With**]")
                    st.code(ap.args.get("new_string") or "", language=None)
                elif ap.args:
                    st.code(_fmt_args(ap.args), language="json")
                a, b, _ = st.columns([1, 1, 4])
                if a.button("Approve", type="primary", key=f"ap_ok_{ap.request_id}"):
                    err = r.resolve_approval(True)
                    if err:
                        st.error(f"Approval failed: {err}")
                if b.button("Deny", key=f"ap_no_{ap.request_id}"):
                    err = r.resolve_approval(False)
                    if err:
                        st.error(f"Approval failed: {err}")
        if st.button("Stop", icon=":material/stop_circle:", key="stop_run"):
            r.cancel()
            finalize_run(r)
            st.rerun()


live_run()

# ── composer ──────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask the agent… (type /skill-name to invoke a skill)", disabled=running or not agents)
if prompt and prompt.strip():
    question = prompt.strip()
    agent_name = ss.get("chat_agent")
    if not agent_name and agents:
        agent_name = agents[0]["name"]
        ss.chat_agent = agent_name
        ss.chat_messages.append(_msg("system", f"Auto-selected agent: **{agent_name}**"))
    if not agent_name:
        ss.chat_messages.append(_msg("user", question))
        ss.chat_messages.append(_msg("agent", "No agents available. Please create an agent workflow first."))
    else:
        ask(agent_name, question)
    st.rerun()
