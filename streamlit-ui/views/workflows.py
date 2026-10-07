"""Workflows — list, create from templates, and the visual workflow editor."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import List, Optional

import streamlit as st
from streamlit_flow import streamlit_flow

from oncall_ui import canvas
from oncall_ui import workflow_model as wm
from oncall_ui.api import ApiError, get_api
from oncall_ui.schedules import from_api
from oncall_ui.timeutils import fmt_datetime, relative_time
from oncall_ui.ui import (
    flash, guarded, invalidate_workflows, load_aws_profiles, load_code_repos, load_llms,
    load_mcp_servers, load_settings, load_skills, load_workflows, pill, running_set, setup_page,
    trigger_run, viewer_tz,
)

setup_page()
ss = st.session_state
api = get_api()
tz = viewer_tz()
ss.setdefault("wf_editor", None)


# ═════════════════════════════ editor state ═══════════════════════════════
WIDGET_PREFIXES = ("p_", "name_", "scope_", "conn_", "eports_", "ado_")


def open_editor(name: str, wtype: str, wf: Optional[dict], template: Optional[dict] = None) -> None:
    # Drop per-node widget state from any earlier editing session, or unsaved
    # values would silently re-apply to the freshly loaded nodes.
    for k in [k for k in ss.keys() if isinstance(k, str) and k.startswith(WIDGET_PREFIXES)]:
        del ss[k]
    src = wf or template or {}
    nodes = [wm.normalize_node(n) for n in copy.deepcopy(src.get("nodes") or [])]
    edges = [wm.normalize_edge(e) for e in copy.deepcopy(src.get("edges") or [])]
    # Self-heal on open: re-stack every subagent window's members.
    for n in list(nodes):
        if n["type"] == "subagent_window":
            nodes = wm.layout_window_members(nodes, n["id"])
    prev = ss.get("wf_editor") or {}
    ss.wf_editor = {
        "name": name, "orig_name": wf.get("name") if wf else None, "type": wtype,
        "enabled": (wf or {}).get("enabled", False) if wf else False,
        "base": wf or {}, "nodes": nodes, "edges": edges, "selected": None,
        # Newest timestamp the canvas has emitted (browser clock) — see rebuild_flow.
        "last_ts": prev.get("last_ts", 0),
        "flow": None,
    }
    rebuild_flow()
    ss.wf_name_input = name
    ss.wf_active = ss.wf_editor["enabled"]


def ed() -> dict:
    return ss.wf_editor


def rebuild_flow() -> None:
    """Push the model to the canvas.

    The canvas only adopts a state whose timestamp is >= the last one *it*
    emitted (browser clock), so stamp past that to survive clock skew.
    """
    e = ed()
    state = canvas.to_flow_state(e["nodes"], e["edges"], e["selected"])
    state.timestamp = max(state.timestamp, e["last_ts"] + 1)
    e["flow"] = state


def update_node(node_id: str, patch: dict) -> None:
    e = ed()
    old = next(n for n in e["nodes"] if n["id"] == node_id)
    new = {**old, **{k: v for k, v in patch.items() if k != "params"}}
    if "params" in patch:
        new["params"] = {**(old.get("params") or {}), **patch["params"]}
    e["nodes"] = [new if n["id"] == node_id else n for n in e["nodes"]]
    if old["type"] == "language_model" and "params" in patch and ("llm" in patch["params"] or "models" in patch["params"]):
        e["edges"] = wm.reconcile_model_edges(e["edges"], node_id, old, new)
    if old["type"] == "mcp_server" and "params" in patch and "servers" in patch["params"]:
        e["edges"] = wm.reconcile_mcp_edges(e["edges"], node_id, old, new)
    if old["type"] == "subagent_window":
        e["nodes"] = wm.layout_window_members(e["nodes"], node_id)
    rebuild_flow()


def add_node(node_type: str) -> None:
    e = ed()
    gtz = (load_settings() or {}).get("global_timezone") or "UTC"
    sel = next((n for n in e["nodes"] if n["id"] == e["selected"]), None)
    xs = [n.get("x", 0) for n in e["nodes"]] or [0]
    node = wm.make_node(node_type, max(xs) + 300 if e["nodes"] else 40, 40 + 30 * (len(e["nodes"]) % 6),
                        global_tz=gtz)
    e["nodes"].append(node)
    # Adding a tool while a subagent window is selected drops it into that window.
    if sel and sel["type"] == "subagent_window" and wm.is_tool_node_type(node_type):
        e["nodes"], e["edges"] = wm.set_membership(e["nodes"], e["edges"], node["id"], sel["id"])
    e["selected"] = node["id"]
    rebuild_flow()


def remove_node(node_id: str) -> None:
    e = ed()
    e["nodes"], e["edges"] = wm.delete_node(e["nodes"], e["edges"], node_id)
    e["selected"] = None
    rebuild_flow()


def connect(src: str, src_slot: str, tgt: str, tgt_slot: str) -> Optional[str]:
    e = ed()
    ok, reason = wm.validate_connection(src, src_slot, tgt, tgt_slot, e["nodes"], e["edges"])
    if not ok:
        return reason
    e["edges"].append({"id": wm.new_id("e"), "source": src, "sourceSlot": src_slot,
                       "target": tgt, "targetSlot": tgt_slot})
    rebuild_flow()
    return None


def save_editor() -> bool:
    e = ed()
    if not e["name"].strip():
        flash("Enter a workflow name", "error")
        return False
    if not e["nodes"]:
        flash("Add at least one node", "error")
        return False
    exported = wm.build_exported_workflow(e["nodes"], wm.clean_orphaned_edges(e["nodes"], e["edges"]),
                                          e["enabled"])
    base = {k: v for k, v in e["base"].items()
            if k not in ("schedule", "startTime", "recurrence", "createdAt", "updatedAt")}
    payload = {**base, "name": e["name"].strip(), "type": e["type"], **exported,
               "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    try:
        if e["orig_name"]:
            saved = api.update_workflow(e["orig_name"], payload)
        else:
            saved = api.create_workflow(payload)
    except ApiError as exc:
        flash(f"Save failed: {exc}", "error")
        return False
    invalidate_workflows()
    flash("Workflow saved", "success")
    if isinstance(saved, dict) and saved.get("name"):
        e["orig_name"], e["base"] = saved["name"], saved
    return True


# ═════════════════════════════ list view ══════════════════════════════════
@st.dialog("Create workflow", width="large")
def create_dialog():
    name = st.text_input("Name", placeholder="e.g. Daily Log Analyzer")
    wtype = st.selectbox("Type", ["workflow", "agent"],
                         format_func=lambda t: {"workflow": "Standard Workflow", "agent": "Agentic Process"}[t])
    options = [None] + wm.WORKFLOW_TEMPLATES
    tpl = st.radio("Start from", options, format_func=lambda t: "Blank — start from an empty canvas" if t is None
                   else f"{t['label']} — {t['description'][:140]}{'…' if len(t['description']) > 140 else ''}")
    if st.button("Create & design", type="primary", use_container_width=True):
        if not name.strip():
            st.error("Enter a workflow name")
            return
        open_editor(name.strip(), (tpl or {}).get("type", wtype) if tpl else wtype, None, tpl)
        st.rerun()


@st.dialog("Delete workflow?")
def delete_dialog(name: str):
    st.write(f'This permanently deletes "{name}" and its SQL scripts.')
    a, b = st.columns(2)
    if a.button("Cancel", use_container_width=True):
        st.rerun()
    if b.button("Delete", type="primary", use_container_width=True):
        try:
            api.delete_workflow(name)
            invalidate_workflows()
            if ed() and ed()["orig_name"] == name:
                ss.wf_editor = None
            flash("Workflow deleted", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to delete: {exc}")


def edit_workflow(name: str) -> None:
    try:
        fresh = api.get_workflow(name)
    except ApiError as exc:
        flash(f"Failed to load workflow: {exc}", "error")
        return
    open_editor(fresh["name"], fresh.get("type") or "workflow", fresh)


def status_meta(w: dict):
    s = w.get("indexing_status")
    if s == "indexing":
        return "Indexing", "#d97706"
    if isinstance(s, str) and s.startswith("indexing_failed"):
        return "Index failed", "#dc2626"
    return ("Active", "#059669") if w["enabled"] else ("Disabled", "#94a3b8")


def list_view():
    head, btn = st.columns([5, 1], vertical_alignment="bottom")
    head.title("Workflows")
    head.caption("Orchestrate AI agents, schedules, and tools into repeatable automated processes.")
    if btn.button("New workflow", icon=":material/add:", type="primary", use_container_width=True):
        create_dialog()
    raw = guarded(load_workflows, "Failed to connect to agent-api: ") or []
    wfs = [from_api(w, tz) for w in raw]
    stats = {"all": len(wfs), "active": sum(w["enabled"] for w in wfs),
             "scheduled": sum(bool(w["schedule"] and w["schedule"] != "Manual") for w in wfs),
             "indexing": sum(w["indexing_status"] == "indexing" for w in wfs)}
    c = st.columns(4)
    for col, (k, label) in zip(c, [("all", "Total"), ("active", "Active"), ("scheduled", "Scheduled"),
                                   ("indexing", "Indexing")]):
        col.metric(label, stats[k])
    s1, s2 = st.columns([3, 2])
    term = s1.text_input("Search", placeholder="Search workflows…", label_visibility="collapsed")
    flt = s2.segmented_control("Filter", ["all", "active", "scheduled", "indexing"], default="all",
                               format_func=lambda k: f"{k.title()} ({stats[k]})", label_visibility="collapsed")
    shown = [w for w in wfs if term.lower() in (w["name"] or "").lower() and (
        flt in (None, "all") or (flt == "active" and w["enabled"])
        or (flt == "scheduled" and w["schedule"] and w["schedule"] != "Manual")
        or (flt == "indexing" and w["indexing_status"] == "indexing"))]
    if not shown:
        st.info("No workflows match — try a different search or filter." if wfs else
                "No workflows yet. Create your first one with **New workflow**.")
        return
    cards(shown, any(w["indexing_status"] == "indexing" for w in wfs))


def cards(shown: List[dict], any_indexing: bool):
    @st.fragment(run_every=3 if any_indexing else 10)
    def _grid():
        running = running_set()
        progress = {}
        if any_indexing:
            res = guarded(api.indexing_status, quiet=True) or {}
            progress = {j.get("target"): j for j in res.get("jobs") or []}
            if not res.get("indexing"):
                invalidate_workflows()
        cols = st.columns(3)
        for i, w in enumerate(shown):
            with cols[i % 3].container(border=True):
                label, color = status_meta(w)
                is_run = w["name"] in running
                badge = pill("Live", "#d97706") if is_run else pill(label, color)
                kind = pill("Agent" if w["type"] == "agent" else "Workflow",
                            "#7c3aed" if w["type"] == "agent" else "#2563eb", dot=False)
                st.markdown(f"**{w['name']}**  \n{kind} {badge}", unsafe_allow_html=True)
                n = len(w["nodes"])
                st.caption(f"🗓 {w['schedule'] or 'Manual'} · 🕑 {relative_time(w['updated_at']) or '—'} · "
                           f"{n} node{'s' if n != 1 else ''} · {'Enabled' if w['enabled'] else 'Off'}")
                a, b, c = st.columns([2, 2, 1])
                if a.button("Edit", key=f"e_{w['name']}", icon=":material/edit:", use_container_width=True):
                    edit_workflow(w["name"])
                    st.rerun()
                indexing = w["indexing_status"] == "indexing"
                job = progress.get(w["name"]) or {}
                run_label = (f"Indexing… ({job.get('progress')}/{job.get('total')})" if indexing and job.get("total")
                             else "Indexing…" if indexing else "Running…" if is_run else "Run")
                if b.button(run_label, key=f"r_{w['name']}", type="primary", use_container_width=True,
                            disabled=is_run or indexing):
                    trigger_run(w["name"])
                    st.rerun()
                if c.button("🗑️", key=f"d_{w['name']}", help="Delete"):
                    delete_dialog(w["name"])

    _grid()


# ═════════════════════════════ editor view ════════════════════════════════
def _csv(v) -> List[str]:
    return wm.split_csv(v)


def slot_widget(node: dict, slot: dict) -> None:
    """Render one editable slot and write changes back into the model."""
    nid, sid, kind = node["id"], slot["id"], slot["kind"]
    p = node.get("params") or {}
    val = p.get(sid, "")
    key = f"p_{nid}_{sid}"
    label, hint = slot.get("label", sid), slot.get("hint")
    new = val

    if kind == "field":
        new = st.text_input(label + (f" ({slot['suffix']})" if slot.get("suffix") else ""), value=str(val or ""),
                            key=key, help=hint, placeholder=slot.get("placeholder"),
                            type="password" if sid == "pat" else "default")
    elif kind == "textarea":
        new = st.text_area(label, value=str(val or ""), key=key, help=hint, height=160 if sid == "system" else 90)
    elif kind == "select":
        opts = slot.get("options") or []
        cur = val if val in opts else (opts[0] if opts else "")
        new = st.selectbox(label, opts, index=opts.index(cur) if cur in opts else 0, key=key, help=hint)
    elif kind == "toggle":
        new = "true" if st.toggle(label, value=str(val).lower() == "true", key=key, help=hint) else "false"
    elif kind == "weekday-select":
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        picked = st.pills(label, days, selection_mode="multi", default=[d for d in _csv(val) if d in days], key=key)
        new = ",".join(d for d in days if d in (picked or []))
    elif kind == "tz-info":
        st.text_input(label, value=(load_settings() or {}).get("global_timezone") or "UTC", disabled=True,
                      key=key, help="Global timezone — change it in Settings → General.")
        return
    elif kind == "llm-select":
        opts = list(guarded(load_llms, quiet=True) or {})
        cur = _csv(val)
        picked = st.multiselect(label, sorted(set(opts) | set(cur)), default=cur, key=key,
                                help="One output port per selected model.")
        new = ",".join(picked)
        if not opts:
            st.caption("No models configured — add one in Settings → Models.")
    elif kind == "memory-select":
        opts = [o["value"] for o in slot.get("options") or []]
        names = {o["value"]: o["label"] for o in slot.get("options") or []}
        picked = st.multiselect(label, opts, default=[v for v in _csv(val) if v in opts], key=key,
                                format_func=lambda v: names.get(v, v), help=hint)
        new = ",".join(picked)
    elif kind in ("mcp-select", "db-select"):
        servers = list(guarded(load_mcp_servers, quiet=True) or {})
        cur = _csv(val)
        if kind == "db-select":
            opts = [""] + sorted(set(servers) | set(cur))
            new = st.selectbox(label, opts, index=opts.index(val) if val in opts else 0, key=key)
        else:
            picked = st.multiselect(label, sorted(set(servers) | set(cur)), default=cur, key=key,
                                    help="One output port per selected server.")
            new = ",".join(picked)
        if not servers:
            st.caption("No MCP servers configured — add one in Settings → MCP servers.")
    elif kind == "repo-select":
        data = guarded(load_code_repos, quiet=True) or {}
        repos = [r["name"] for r in data.get("repos") or []]
        cur = _csv(val)
        picked = st.multiselect(label, sorted(set(repos) | set(cur)), default=cur, key=key)
        new = ",".join(picked)
        if data and not data.get("base_exists", True):
            st.caption(f"Repo base path `{data.get('base_path')}` not found — set REPOS_HOST_PATH.")
    elif kind == "aws-profile-select":
        profiles = guarded(load_aws_profiles, quiet=True) or []
        names = [""] + [pr["name"] for pr in profiles]
        if val and val not in names:
            names.append(val)
        info = {pr["name"]: pr for pr in profiles}

        def fmt(n):
            if not n:
                return "(default credentials)"
            pr = info.get(n) or {}
            mark = " ⛔ expired" if pr.get("expired") else (" 🔑 needs login" if pr.get("has_credentials") is False else "")
            return f"{n}{' · ' + pr['region'] if pr.get('region') else ''}{mark}"

        new = st.selectbox(label, names, index=names.index(val) if val in names else 0, key=key, format_func=fmt)
    elif kind == "chips":
        chips = _csv(val)
        picked = st.multiselect(label, chips, default=chips, key=f"{key}_chips")
        add = st.text_input("Add log group", key=f"{key}_add", placeholder="/aws/lambda/my-fn")
        prefix_col, btn_col = st.columns([3, 1], vertical_alignment="bottom")
        prefix = prefix_col.text_input("Discover by prefix", key=f"{key}_prefix", placeholder="/aws/lambda/")
        if btn_col.button("Discover", key=f"{key}_disc"):
            try:
                res = api.discover_log_groups(prefix or None, p.get("region") or "us-east-1", 200,
                                              p.get("profile") or None)
                ss[f"{key}_found"] = [g["name"] for g in res.get("log_groups") or [] if g.get("name")]
                if not ss[f"{key}_found"]:
                    st.caption("No log groups found.")
            except ApiError as exc:
                st.error(f"Could not reach AWS: {exc}")
        found = ss.get(f"{key}_found") or []
        extra = st.multiselect("Discovered — pick to add", [g for g in found if g not in picked],
                               key=f"{key}_pick") if found else []
        merged = list(dict.fromkeys(picked + ([add.strip()] if add.strip() else []) + extra))
        new = ", ".join(merged)
    elif kind == "file-select":
        if val:
            st.markdown(f"📄 `{val}`")
        up = st.file_uploader(label, type=["sql"], key=f"{key}_up")
        if up is not None and up.name != val:
            update_node(nid, {"params": {sid: up.name, "sqlContent": up.getvalue().decode("utf-8", "replace")}})
            st.rerun()
        if val and st.button("Remove file", key=f"{key}_rm"):
            update_node(nid, {"params": {sid: "", "sqlContent": ""}})
            st.rerun()
        return
    elif kind == "skills-picker":
        skills = guarded(load_skills, quiet=True) or []
        names = [s["name"] for s in skills]
        cur = _csv(val)
        picked = st.multiselect(label, sorted(set(names) | set(cur)), default=cur, key=key,
                                help="Optional — none selected = auto-select from all your skills.")
        new = ", ".join(picked)
        if not names:
            st.caption("No skills yet — create them on the Skills page.")
    elif kind == "subagents-editor":
        new = st.text_area(label + " (JSON)", value=str(val or "[]"), key=key, height=160)
        try:
            json.loads(new or "[]")
        except ValueError:
            st.error("Invalid JSON")
            return
    else:
        return
    if str(new) != str(val if val is not None else ""):
        update_node(nid, {"params": {sid: new}})
        st.rerun()


def set_param(node_id: str, slot_id: str, value: str) -> None:
    """Set a param from code, keeping its widget in step (else the widget's
    stale value would be written back on the next render)."""
    update_node(node_id, {"params": {slot_id: value}})
    ss.pop(f"p_{node_id}_{slot_id}", None)  # re-created from the param next run


def ado_picker(node: dict) -> None:
    """Fill Wiki organization / project / URL from Azure DevOps."""
    p = node.get("params") or {}
    with st.expander("Pick from Azure DevOps"):
        pat, tv = p.get("pat") or "", p.get("tokenVar") or "ADO_WIKI_PAT"
        k = f"ado_{node['id']}"
        if st.button("Load organizations", key=f"{k}_o"):
            ss[f"{k}_orgs"] = (guarded(lambda: api.ado_organizations(pat, tv)) or {}).get("organizations", [])
        orgs = ss.get(f"{k}_orgs") or []
        if orgs:
            org = st.selectbox("Organization", [o["name"] for o in orgs], key=f"{k}_os")
            if st.button("Use organization", key=f"{k}_ou"):
                set_param(node["id"], "organization", org)
                st.rerun()
        if p.get("organization") and st.button("Load projects", key=f"{k}_p"):
            ss[f"{k}_projs"] = (guarded(lambda: api.ado_projects(p["organization"], pat, tv)) or {}).get("projects", [])
        projs = ss.get(f"{k}_projs") or []
        if projs:
            proj = st.selectbox("Project", [o["name"] for o in projs], key=f"{k}_ps")
            if st.button("Use project", key=f"{k}_pu"):
                set_param(node["id"], "project", proj)
                st.rerun()
        if p.get("organization") and p.get("project") and st.button("Load wikis", key=f"{k}_w"):
            ss[f"{k}_wikis"] = (guarded(lambda: api.ado_wikis(p["organization"], p["project"], pat, tv)) or {}).get("wikis", [])
        wikis = ss.get(f"{k}_wikis") or []
        if wikis:
            wk = st.selectbox("Wiki", wikis, key=f"{k}_ws", format_func=lambda w: f"{w.get('name')} ({w.get('type')})")
            if st.button("Use wiki", key=f"{k}_wu"):
                set_param(node["id"], "wikiUrl", wk.get("url") or "")
                st.rerun()


def node_panel(node: dict) -> None:
    e = ed()
    d = wm.NODE_TYPES.get(node["type"], {})
    st.markdown(f"#### {canvas.node_icon(node['type'])} {d.get('label', node['type'])}")
    st.caption(d.get("desc", ""))
    name = st.text_input("Name", value=node.get("name") or "", key=f"name_{node['id']}")
    if name != (node.get("name") or ""):
        update_node(node["id"], {"name": name})
        st.rerun()
    slots = wm.slots_for_node(node)
    editable = [s for s in slots if not s["kind"].startswith("port")]
    for s in [s for s in editable if not s.get("advanced")]:
        slot_widget(node, s)
    adv = [s for s in editable if s.get("advanced")]
    if adv:
        with st.expander("Advanced"):
            for s in adv:
                slot_widget(node, s)
    if node["type"] == "wiki":
        ado_picker(node)

    # Subagent window membership (tool nodes).
    windows = [n for n in e["nodes"] if n["type"] == "subagent_window"]
    if windows and wm.is_tool_node_type(node["type"]):
        opts = [None] + [w["id"] for w in windows]
        names = {w["id"]: (w.get("params") or {}).get("name") or w.get("name") for w in windows}
        cur = node.get("parentId") if node.get("parentId") in opts else None
        pick = st.selectbox("Scope", opts, index=opts.index(cur), key=f"scope_{node['id']}",
                            format_func=lambda i: "Main agent" if i is None else f"Subagent: {names[i]}",
                            help="Tools inside a subagent window are delegated to that subagent.")
        if pick != cur:
            old = node.get("parentId")
            e["nodes"], e["edges"] = wm.set_membership(e["nodes"], e["edges"], node["id"], pick)
            if old:
                e["nodes"] = wm.layout_window_members(e["nodes"], old, shrink=True)
            rebuild_flow()
            st.rerun()

    ports_panel(node)
    if st.button("Delete node", icon=":material/delete:", key=f"del_{node['id']}", use_container_width=True):
        remove_node(node["id"])
        st.rerun()


def ports_panel(node: dict) -> None:
    e = ed()
    by_id = {n["id"]: n for n in e["nodes"]}
    ins, outs = wm.ports(node, "in"), wm.ports(node, "out")
    if not ins and not outs:
        return
    st.markdown("**Connections**")
    for s in ins + outs:
        incoming = s["kind"] == "port-in"
        conns = [x for x in e["edges"] if (x["target"] == node["id"] and x["targetSlot"] == s["id"]) if incoming] + \
                [x for x in e["edges"] if (x["source"] == node["id"] and x["sourceSlot"] == s["id"]) if not incoming]
        ptype = wm.PORT_TYPE.get(s.get("portType") or "message", {})
        tag = ("in" if incoming else "out") + (" · multi" if s.get("multi") else "") + \
              (" · optional" if s.get("optional") else "")
        st.markdown(f"<span style='color:{ptype.get('color')}'>●</span> **{s['label']}** "
                    f"<span class='oc-muted'>({ptype.get('label')}, {tag})</span>", unsafe_allow_html=True)
        for x in conns:
            other = by_id.get(x["source"] if incoming else x["target"]) or {}
            other_slot = x["sourceSlot"] if incoming else x["targetSlot"]
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.caption(("← " if incoming else "→ ") + f"{other.get('name')} · {other_slot}")
            if c2.button("✕", key=f"rm_{x['id']}", help="Disconnect"):
                e["edges"] = [y for y in e["edges"] if y["id"] != x["id"]]
                rebuild_flow()
                st.rerun()
    # Explicit connect — precise port choice (e.g. one model of several).
    with st.expander("Connect to…"):
        targets = []
        for other in e["nodes"]:
            if other["id"] == node["id"]:
                continue
            for so, ti in wm.compatible_port_pairs(node, other, e["nodes"], e["edges"]):
                targets.append((node["id"], so, other["id"], ti))
            for so, ti in wm.compatible_port_pairs(other, node, e["nodes"], e["edges"]):
                targets.append((other["id"], so, node["id"], ti))
        if not targets:
            st.caption("No compatible free ports on other nodes.")
        else:
            pick = st.selectbox("Connection", targets, key=f"conn_{node['id']}",
                                format_func=lambda t: f"{by_id[t[0]].get('name')}.{t[1]} → {by_id[t[2]].get('name')}.{t[3]}")
            if st.button("Connect", key=f"connb_{node['id']}"):
                err = connect(*pick)
                if err:
                    st.error(err)
                else:
                    st.rerun()


def edge_panel(edge: dict) -> None:
    e = ed()
    by_id = {n["id"]: n for n in e["nodes"]}
    src, tgt = by_id.get(edge["source"]), by_id.get(edge["target"])
    st.markdown("#### Connection")
    st.markdown(f"**{(src or {}).get('name')}** `{edge['sourceSlot']}` → **{(tgt or {}).get('name')}** "
                f"`{edge['targetSlot']}`")
    if src and tgt:
        others = [x for x in e["edges"] if x["id"] != edge["id"]]
        pairs = wm.compatible_port_pairs(src, tgt, e["nodes"], others)
        cur = (edge["sourceSlot"], edge["targetSlot"])
        if cur not in pairs:
            pairs = [cur] + pairs
        pick = st.selectbox("Ports", pairs, index=pairs.index(cur), format_func=lambda p: f"{p[0]} → {p[1]}",
                            key=f"eports_{edge['id']}")
        if pick != cur:
            e["edges"] = [{**x, "sourceSlot": pick[0], "targetSlot": pick[1]} if x["id"] == edge["id"] else x
                          for x in e["edges"]]
            rebuild_flow()
            st.rerun()
    if st.button("Delete connection", icon=":material/link_off:", use_container_width=True):
        e["edges"] = [x for x in e["edges"] if x["id"] != edge["id"]]
        e["selected"] = None
        rebuild_flow()
        st.rerun()


def runs_panel(name: Optional[str]) -> None:
    if not name:
        st.caption("Save the workflow to see its runs.")
        return
    runs = guarded(lambda: api.workflow_executions(name, 20), quiet=True) or []
    if not runs:
        st.caption("No runs yet.")
    for r in runs:
        s = (r.get("status") or "").lower()
        icon = "✅" if s == "success" else "⏳" if s in ("running", "in_progress", "pending") else "❌"
        dur = f" · {r['duration']:.1f}s" if r.get("duration") else ""
        st.markdown(f"{icon} {fmt_datetime(r.get('start_time'), tz)}{dur}"
                    + (f" · {r['total_tokens']:,} tok" if r.get("total_tokens") else ""))
        if r.get("error"):
            st.caption(f":red[{str(r['error'])[:160]}]")


def editor_view():
    e = ed()
    top = st.columns([1, 3, 1.3, 1, 1, 1], vertical_alignment="bottom")
    if top[0].button("‹ Workflows", use_container_width=True):
        ss.wf_editor = None
        st.rerun()
    ss.setdefault("wf_name_input", e["name"])
    ss.setdefault("wf_active", bool(e["enabled"]))
    e["name"] = top[1].text_input("Workflow name", key="wf_name_input")
    e["enabled"] = top[2].toggle("Active", key="wf_active")
    if top[3].button("Clear canvas", use_container_width=True):
        e["nodes"], e["edges"], e["selected"] = [], [], None
        rebuild_flow()
        st.rerun()
    if top[4].button("Run", icon=":material/play_arrow:", use_container_width=True, disabled=not e["orig_name"]):
        trigger_run(e["orig_name"])
        st.rerun()
    if top[5].button("Save", icon=":material/save:", type="primary", use_container_width=True):
        save_editor()
        st.rerun()

    for w in wm.workflow_warnings(e["nodes"], e["edges"]):
        st.warning(w["message"], icon="⚠️")

    pal, mid, props = st.columns([1.1, 4, 1.8])
    with pal:
        st.markdown("**Components**")
        for cat, items in wm.palette().items():
            with st.expander(cat, expanded=cat in ("Agents", "Models", "Tools")):
                for t, d in items:
                    if st.button(f"{canvas.node_icon(t)} {d['label']}", key=f"add_{t}", help=d.get("desc"),
                                 use_container_width=True):
                        add_node(t)
                        st.rerun()
        st.caption("Click to add · drag between nodes to connect · right-click a node or edge to delete.")

    with mid:
        new_state = streamlit_flow(
            "wf_canvas", e["flow"], height=680, fit_view=True, show_controls=True,
            show_minimap=True, allow_new_edges=True, get_node_on_click=True, get_edge_on_click=True,
            enable_node_menu=True, enable_edge_menu=True, hide_watermark=True, min_zoom=0.2,
        )
        # Streamlit returns the component's *last* value on every rerun, so only
        # act on a genuinely new emission (its timestamp is past the last seen).
        ts = new_state.timestamp or 0
        if new_state is not e["flow"] and ts > e["last_ts"]:
            e["last_ts"] = ts
            nodes, edges, rebuild, msgs = canvas.sync_from_flow(e["nodes"], e["edges"], new_state)
            e["nodes"], e["edges"] = nodes, edges
            for m in msgs:
                st.toast(m)
            # The canvas echoes every adopted state back with no selection, so
            # only a non-empty selection is treated as a click.
            sel = new_state.selected_id
            ids = {n["id"] for n in nodes} | {x["id"] for x in edges}
            sel_changed = bool(sel) and sel in ids and sel != e["selected"]
            if sel_changed:
                e["selected"] = sel
            if e["selected"] not in ids:
                e["selected"] = None
            if rebuild or sel_changed:
                rebuild_flow()
                st.rerun()
            # Keep current positions, stamped just below the canvas's own clock
            # so it does not re-adopt (and re-echo) the same state.
            keep = canvas.to_flow_state(e["nodes"], e["edges"], e["selected"])
            keep.timestamp = ts - 1
            e["flow"] = keep

    with props:
        tab_node, tab_runs = st.tabs(["Properties", "Runs"])
        with tab_node:
            sel = e["selected"]
            node = next((n for n in e["nodes"] if n["id"] == sel), None)
            edge = next((x for x in e["edges"] if x["id"] == sel), None)
            if node or edge:
                if st.button("Deselect", icon=":material/close:", key="wf_deselect"):
                    e["selected"] = None
                    rebuild_flow()
                    st.rerun()
            if node:
                node_panel(node)
            elif edge:
                edge_panel(edge)
            else:
                st.caption("Select a node or connection on the canvas to edit it.")
                st.markdown(f"**{len(e['nodes'])}** nodes · **{len(e['edges'])}** connections")
                if e["nodes"]:
                    pick = st.selectbox("Or pick a node", [None] + [n["id"] for n in e["nodes"]],
                                        format_func=lambda i: "—" if i is None else
                                        next(n.get("name") for n in e["nodes"] if n["id"] == i))
                    if pick:
                        e["selected"] = pick
                        rebuild_flow()
                        st.rerun()
        with tab_runs:
            runs_panel(e["orig_name"])


if ss.wf_editor:
    editor_view()
else:
    list_view()
