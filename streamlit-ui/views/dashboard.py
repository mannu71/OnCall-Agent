"""System dashboard — stat tiles, live activity, next schedule, token usage, recent runs."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from oncall_ui.api import ApiError, get_api
from oncall_ui.results import clean_llm_text
from oncall_ui.schedules import from_api
from oncall_ui.timeutils import (
    fmt_clock, next_occurrence, parse_date, run_started_compact, scheduled_relative, zone,
)
from oncall_ui.ui import (
    esc, fmt_tokens_k, guarded, load_workflows, pill, running_set, setup_page, slug,
    stat_card, viewer_tz,
)

# Rows pulled for the stat tiles: they compare the last 24h with the 24–48h
# before, so the fetch must reach two days back. Chat turns are excluded
# server-side so the whole budget is real workflow runs. API cap is 500.
STATS_ROW_BUDGET = 500
PAGE_SIZE = 5
DAY = timedelta(days=1)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

setup_page()
tz = viewer_tz()


def _status(e: dict) -> str:
    s = (e.get("status") or "").lower()
    if s == "success":
        return "success"
    if s in ("in_progress", "running", "pending"):
        return "running"
    return "failure"


def _age(e: dict, now: datetime):
    d = parse_date(e.get("start_time"))
    return (now - d) if d else None


def _window(execs, now, newer: timedelta, older: timedelta = timedelta(0)):
    out = []
    for e in execs:
        a = _age(e, now)
        if a is not None and older <= a < newer:
            out.append(e)
    return out


def _trend(cur: int, prev: int):
    if prev > 0:
        return round((cur - prev) / prev * 100)
    return 100 if cur > 0 else None


def _queries(run: dict) -> int:
    out = run.get("output") or {}
    if out.get("analysis_type"):
        return len(out.get("log_groups_analyzed") or [])
    orch_key = next((k for k in out if k.startswith("orchestrator")), None)
    orch = out.get(orch_key) if orch_key else None
    return (orch or {}).get("queries_executed") or out.get("queries_executed") or 0


def _error_text(e: dict) -> str:
    err = e.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict) and isinstance(err.get("message"), str):
        return err["message"].strip()
    out = e.get("output") or {}
    return str(out.get("error")).strip() if out.get("error") is not None else ""


def _meta_line(e: dict, now: datetime) -> str:
    st_ = _status(e)
    tok = f"{e['total_tokens']:,} tok" if isinstance(e.get("total_tokens"), int) and e["total_tokens"] > 0 else ""
    dur = f"{e['duration']:.1f}s" if isinstance(e.get("duration"), (int, float)) else None
    if st_ == "running" and not dur:
        a = _age(e, now)
        if a is not None:
            dur = f"{max(0.1, a.total_seconds()):.1f}s"
    if st_ == "failure":
        msg = _error_text(e)
        if msg:
            return msg if len(msg) <= 90 else msg[:87] + "…"
        return " · ".join(p for p in (dur, "failed") if p)
    if st_ == "running":
        return " · ".join(p for p in (dur, "streaming…", tok) if p)
    return " · ".join(p for p in (dur, tok) if p) or "—"


def _schedule_tags(sched: dict):
    nodes = sched.get("nodes") or []
    tags = []
    if any(n.get("type") == "database" for n in nodes):
        tags.append(("postgres", "#0284c7"))
    model = ""
    for t in ("orchestrator", "agent", "llm", "language_model"):
        n = next((n for n in nodes if n.get("type") == t), None)
        if n:
            d, p = n.get("data") or {}, n.get("params") or {}
            model = d.get("model") or d.get("llm_model") or d.get("providerModel") or p.get("llm") or ""
            if model:
                break
    if model:
        s = str(model).split(",")[0].strip().split("/")[-1]
        tags.append((s if len(s) <= 24 else s[:22] + "…", "#7c3aed"))
    tools = sum(1 for n in nodes if n.get("type") == "tool" or str(n.get("type", "")).endswith("_tool"))
    if tools:
        tags.append((f"{tools} tools", "#475569"))
    return tags


# ── data ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=15, show_spinner=False)
def _load_executions():
    return get_api().list_executions(STATS_ROW_BUDGET, exclude_chat=True)


workflows = guarded(load_workflows, "Failed to connect to agent-api: ") or []
schedules = [from_api(w, tz) for w in workflows]

st.title("System Dashboard")
st.caption("Real-time monitoring and node activity tracking")


@st.fragment(run_every=30)
def overview():
    now = datetime.now(timezone.utc)
    execs = guarded(_load_executions, "Error loading executions: ", quiet=True) or []
    running = sorted(running_set())

    total = len(schedules)
    enabled = sum(1 for s in schedules if s["enabled"])
    last24 = _window(execs, now, DAY)
    prev24 = _window(execs, now, 2 * DAY, DAY)
    failed24 = [e for e in last24 if e.get("status") != "success"]
    failed_prev = [e for e in prev24 if e.get("status") != "success"]
    success_rate = round((len(last24) - len(failed24)) / len(last24) * 100) if last24 else 100

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        stat_card("Active workflows", enabled,
                  f"{total - enabled} disabled" if total - enabled > 0 else "All enabled")
    with c2:
        stat_card("Total workflows", total, f"{round(enabled / max(total, 1) * 100)}% active")
    with c3:
        stat_card("Failures (24h)", len(failed24), f"{success_rate}% success rate",
                  _trend(len(failed24), len(failed_prev)))
    with c4:
        stat_card("Running now", len(running),
                  f"{len(last24)} runs today" if last24 else "No runs today",
                  _trend(len(last24), len(prev24)))

    left, right = st.columns([2, 1])
    with left:
        with st.container(border=True):
            h1, h2 = st.columns([3, 1])
            h1.markdown("**🔴 Live activity**")
            h2.caption("auto-refresh · 30s")
            # DB rows only appear once a run finishes; prepend in-flight runs
            # from /executions/active so they show as Running.
            in_db = {e.get("workflow_name") for e in execs if _status(e) == "running"}
            feed = [{"workflow_name": n, "status": "running", "start_time": None, "total_tokens": 0,
                     "_synthetic": True} for n in running if n not in in_db][:5]
            feed += execs[: max(0, 5 - len(feed))]
            if not feed:
                st.caption("No recent activity")
            rows = []
            for e in feed:
                s = _status(e)
                color = {"running": "#f59e0b", "failure": "#dc2626"}.get(s, "#10b981")
                clock = "now" if e.get("_synthetic") else fmt_clock(e.get("start_time"), tz)
                badge = pill("Running", "#d97706") if s == "running" else (
                    pill("Failure", "#dc2626") if s == "failure" else "")
                rows.append(
                    f'<div class="oc-feed-row"><span class="oc-feed-time">{esc(clock)}</span>'
                    f'<span class="oc-dot" style="background:{color}"></span>'
                    f'<div style="flex:1;min-width:0"><div class="oc-feed-title">{esc(slug(e.get("workflow_name")))}</div>'
                    f'<div class="oc-feed-meta">{esc(_meta_line(e, now))}</div></div>{badge}</div>')
            st.markdown("".join(rows), unsafe_allow_html=True)

    with right:
        with st.container(border=True):
            st.markdown('<div class="oc-eyebrow">Next scheduled</div>', unsafe_allow_html=True)
            upcoming = [s for s in schedules if s["enabled"] and s["start_time"]]
            if not upcoming:
                st.caption("No schedules enabled")
            else:
                upcoming.sort(key=lambda s: next_occurrence(s["start_time"], tz, now) or now + 365 * DAY)
                nxt = upcoming[0]
                when = next_occurrence(nxt["start_time"], tz, now)
                tags = "".join(pill(t, c, dot=False) + " " for t, c in _schedule_tags(nxt))
                st.markdown(
                    f'<div class="oc-next-name">{esc(slug(nxt["name"]))}</div>'
                    f'<span class="oc-next-time">{esc(when.strftime("%H:%M") if when else "—")}</span> '
                    f'<span class="oc-hint">{esc(scheduled_relative(when, tz, now))}</span>'
                    f'<div style="margin-top:8px">{tags}</div>', unsafe_allow_html=True)

        with st.container(border=True):
            cur = _window(execs, now, 7 * DAY)
            prev = _window(execs, now, 14 * DAY, 7 * DAY)
            tot = sum(e.get("total_tokens") or 0 for e in cur)
            ptot = sum(e.get("total_tokens") or 0 for e in prev)
            inp = sum(e.get("input_tokens") or 0 for e in cur)
            out = sum(e.get("output_tokens") or 0 for e in cur)
            pct = _trend(tot, ptot)
            st.markdown('<div class="oc-eyebrow">Token usage · 7d</div>', unsafe_allow_html=True)
            trend = ""
            if pct is not None and tot > 0:
                trend = f' <span class="{"oc-trend-up" if pct >= 0 else "oc-trend-down"}">{"▲" if pct >= 0 else "▼"} {abs(pct)}%</span>'
            st.markdown(f'<span class="oc-stat">{esc(fmt_tokens_k(tot).replace("—", "0"))}</span>{trend}',
                        unsafe_allow_html=True)
            # Daily totals, today and the six days before (viewer's calendar days).
            series = [0] * 7
            z = zone(tz)
            local_today = now.astimezone(z).date()
            for e in execs:
                d = parse_date(e.get("start_time"))
                if d:
                    ix = 6 - (local_today - d.astimezone(z).date()).days
                    if 0 <= ix < 7:
                        series[ix] += e.get("total_tokens") or 0
            labels = [(local_today - timedelta(days=6 - i)).strftime("%a") for i in range(7)]
            fig = go.Figure(go.Scatter(x=labels, y=series, mode="lines", line=dict(color="#dc2626", width=2),
                                       hovertemplate="%{x}: %{y:,} tokens<extra></extra>"))
            fig.update_layout(height=70, margin=dict(l=0, r=0, t=0, b=0), xaxis=dict(visible=False),
                              yaxis=dict(visible=False), paper_bgcolor="rgba(0,0,0,0)",
                              plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            if tot == 0:
                st.caption("No runs with recorded token telemetry in the last 7 days.")
            a, b, c = st.columns(3)
            a.metric("Input", fmt_tokens_k(inp))
            b.metric("Output", fmt_tokens_k(out))
            c.metric("Avg/run", f"{round(tot / len(cur)):,}" if cur else "—")

    recent_runs(execs)


def recent_runs(execs):
    with st.container(border=True):
        h1, h2, h3 = st.columns([6, 1, 1])
        h1.markdown("**Recent runs**")
        if h2.button("Refresh", icon=":material/refresh:", key="dash_refresh"):
            _load_executions.clear()
            st.rerun(scope="fragment")
        if h3.button("Clear", icon=":material/delete:", key="dash_clear", disabled=not execs):
            confirm_clear(len(execs))
        if not execs:
            st.caption("No recent runs")
            return
        pages = max(1, -(-len(execs) // PAGE_SIZE))
        page = min(st.session_state.get("dash_page", 0), pages - 1)
        rows = execs[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        hdr = st.columns([3, 2, 1, 1, 1, 1, 1])
        for col, label in zip(hdr, ["Workflow", "Started", "Duration", "Queries", "Status", "Tokens", ""]):
            col.caption(label)
        for i, run in enumerate(rows):
            c = st.columns([3, 2, 1, 1, 1, 1, 1], vertical_alignment="center")
            c[0].markdown(f"**{esc(slug(run.get('workflow_name')))}**")
            c[1].write(run_started_compact(run.get("start_time"), tz))
            c[2].write(f"{run['duration']:.1f}s" if run.get("duration") else "—")
            q = _queries(run)
            c[3].write(str(q) if q else "—")
            s = _status(run)
            c[4].markdown({"success": pill("Success", "#059669"), "running": pill("Running", "#d97706")}
                          .get(s, pill("Failure", "#dc2626")), unsafe_allow_html=True)
            c[5].write(f"{run['total_tokens']:,}" if (run.get("total_tokens") or 0) > 0 else "—")
            if c[6].button("Open", key=f"open_{page}_{i}"):
                run_details(run)
        f1, f2, f3 = st.columns([6, 1, 1])
        start = page * PAGE_SIZE + 1
        f1.caption(f"Showing {start}–{min((page + 1) * PAGE_SIZE, len(execs))} of {len(execs)} runs · "
                   f"page {page + 1} of {pages}")
        if f2.button("‹ Prev", disabled=page == 0, key="dash_prev"):
            st.session_state.dash_page = page - 1
            st.rerun(scope="fragment")
        if f3.button("Next ›", disabled=page >= pages - 1, key="dash_next"):
            st.session_state.dash_page = page + 1
            st.rerun(scope="fragment")


@st.dialog("Clear all executions?")
def confirm_clear(n: int):
    st.write(f"This will permanently delete all {n} execution{'s' if n != 1 else ''} from the history. "
             "This action cannot be undone.")
    a, b = st.columns(2)
    if a.button("Cancel", use_container_width=True):
        st.rerun()
    if b.button("Clear all", type="primary", use_container_width=True):
        try:
            get_api().delete_all_executions()
            _load_executions.clear()
            st.session_state.dash_page = 0
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to clear executions: {exc}")


def _node_results(run: dict) -> dict:
    res = run.get("result")
    return res if isinstance(res, dict) else {}


@st.dialog("Execution details", width="large")
def run_details(run: dict):
    # List rows are slim (no input/log blobs); fetch the full record when possible.
    rid = run.get("execution_id") or run.get("id")
    if rid:
        try:
            run = {**run, **(get_api().get_execution(rid) or {})}
        except ApiError:
            pass
    st.subheader((run.get("workflow_name") or "").replace("-", " "))
    res = _node_results(run)
    output = run.get("output") if isinstance(run.get("output"), dict) else {}
    orch_key = next((k for k in res if k.startswith("orchestrator")), None)
    orch = res.get(orch_key) if orch_key else None
    agent_key = next((k for k in res if k.startswith("agent") or (isinstance(res[k], dict)
                                                                    and res[k].get("final_answer") is not None)), None)
    agent = res.get(agent_key) if agent_key else None
    cw_key = next((k for k in res if isinstance(res[k], dict) and res[k].get("analysis_type")), None)
    cw = res.get(cw_key) if cw_key else None
    is_cw = bool(cw) or bool(output.get("analysis_type"))
    is_react = bool(agent) or output.get("type") == "react"
    duration = f"{run['duration']:.2f}s" if run.get("duration") else "N/A"

    m = st.columns(4)
    m[0].metric("Execution ID", str(rid or "—")[:12])
    m[1].metric("Duration", duration)
    if is_cw:
        src = cw or output
        m[2].metric("Log groups", len(src.get("log_groups_analyzed") or src.get("log_groups") or []))
        m[3].metric("Alerts", len(src.get("alerts") or []))
    elif is_react:
        src = agent or output
        m[2].metric("Messages", src.get("message_count") or 0)
        m[3].metric("Tool calls", len(src.get("tool_calls") or []))
    else:
        m[2].metric("Queries", (orch or {}).get("queries_executed") or output.get("queries_executed") or 0)
        m[3].metric("Failures", (orch or {}).get("failures") or output.get("failures") or 0)

    _token_breakdown(run, res)

    if is_cw:
        src = cw or output
        llm_out = clean_llm_text((agent or {}).get("final_answer") or (agent or {}).get("output") or src.get("output"))
        model = (agent or {}).get("model") or src.get("model")
        st.markdown("#### CloudWatch analysis " + pill(src.get("analysis_type") or "", "#2563eb", dot=False)
                    + (" " + pill(model, "#7c3aed", dot=False) if model else ""), unsafe_allow_html=True)
        if llm_out:
            with st.container(border=True):
                st.markdown(llm_out)
        st.markdown(f"**Time range:** {src.get('time_range') or '—'}")
        groups = src.get("log_groups_analyzed") or []
        if groups:
            st.markdown("**Log groups:** " + " ".join(f"`{g}`" for g in groups))
        for alert in src.get("alerts") or []:
            sev = (alert.get("severity") or "").lower()
            fn = st.error if sev == "high" else st.warning if sev == "medium" else st.info
            fn(f"**{sev.upper()}**: {alert.get('message')}")
        if not llm_out:
            for k in ("patterns", "anomalies", "timeline", "log_groups", "summary"):
                if (src.get("results") or {}).get(k):
                    with st.expander(k.replace("_", " ").title()):
                        st.json(src["results"][k])
    elif (orch or {}).get("results") or output.get("results"):
        st.markdown("#### Query results")
        for i, r in enumerate((orch or {}).get("results") or output.get("results") or []):
            label = r.get("label") or r.get("query_id") or f"Query {i + 1}"
            has_err = not r.get("success") or r.get("error")
            val = r.get("error") if has_err else r.get("result")
            if isinstance(val, list) and len(val) == 1:
                val = val[0]
            if isinstance(val, dict) and len(val) == 1:
                val = next(iter(val.values()))
            c1, c2 = st.columns([1, 2])
            c1.markdown(f"**{esc(label)}**")
            if has_err:
                c2.markdown(f":red[{val}]")
            elif isinstance(val, bool):
                c2.markdown(":green[● TRUE]" if val else ":red[● FALSE]")
            elif isinstance(val, (dict, list)):
                c2.json(val)
            elif isinstance(val, str) and UUID_RE.match(val):
                c2.code(val)
            else:
                c2.markdown(f"**{val}**")
    elif is_react:
        src = agent or output
        st.markdown("#### Agent response " + pill(f"{src.get('provider') or ''} / {src.get('model') or ''}",
                                                    "#7c3aed", dot=False), unsafe_allow_html=True)
        if src.get("user_query"):
            st.caption("Query")
            st.write(src["user_query"])
        st.caption("Final answer")
        ans = clean_llm_text(src.get("final_answer") or src.get("output") or "")
        with st.container(border=True):
            st.markdown(ans or "_(no answer)_")
        calls = src.get("tool_calls") or []
        if calls:
            st.caption(f"Tool calls ({len(calls)})")
            for i, tc in enumerate(calls):
                line = f"`{tc.get('name') or tc.get('tool') or f'Tool {i + 1}'}`"
                if tc.get("error"):
                    line += f" — :red[Error: {tc['error']}]"
                st.markdown(line)

    if run.get("error"):
        st.markdown("#### ⚠️ Error details")
        err = run["error"]
        st.code(err if isinstance(err, str) else json.dumps(err, indent=2), language=None)


def _token_breakdown(run: dict, res: dict):
    agk = next((k for k in res if k.startswith("agent") or (isinstance(res[k], dict)
                                                              and (res[k].get("input_tokens") or 0) > 0)), None)
    at = res.get(agk) if agk and isinstance(res.get(agk), dict) else {}
    inp = run.get("input_tokens") or at.get("input_tokens") or 0
    out = run.get("output_tokens") or at.get("output_tokens") or 0
    tot = run.get("total_tokens") or at.get("total_tokens") or (inp + out)
    if not tot:
        return
    # cache_read / cache_creation are a SUBSET of input_tokens (inclusive
    # counters, normalised backend-side); rates mirror
    # app.core.observability.cache_metrics (read 0.1x, write 1.25x).
    cr = run.get("cache_read_tokens") or at.get("cache_read_tokens") or 0
    cw = run.get("cache_creation_tokens") or at.get("cache_creation_tokens") or 0
    prompt = inp + cr + cw if cr > inp else inp  # defensive: exclusive counters
    fresh = max(0, prompt - cr - cw)
    hit = round(cr / prompt * 100) if prompt else 0
    eff = round(fresh + cr * 0.1 + cw * 1.25)
    saved = round(max(0, prompt - eff) / prompt * 100) if prompt else 0
    with st.container(border=True):
        st.markdown("**⚡ Token usage**" + (f" — {hit}% cache hit · ~{saved}% cost saved" if cr else ""))
        a, b, c = st.columns(3)
        a.metric("Input", f"{inp:,}")
        b.metric("Output", f"{out:,}")
        c.metric("Total", f"{tot:,}")
        if cr:
            st.caption(f"Input breakdown — fresh + cache-read + cache-write = {prompt:,} input")
            d = st.columns(4)
            d[0].metric("Fresh (full price)", f"{fresh:,}")
            d[1].metric("Cache-read (~10%)", f"{cr:,}")
            d[2].metric("Cache-write (~125%)", f"{cw:,}")
            d[3].metric("Effective", f"{eff:,}")


overview()
