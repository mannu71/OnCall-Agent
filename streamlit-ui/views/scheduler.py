"""Schedule management — cron schedules attached to (non-agent) workflows."""
from __future__ import annotations

import streamlit as st

from oncall_ui.api import ApiError, get_api
from oncall_ui.schedules import apply_schedule, from_api, target_nodes
from oncall_ui.timeutils import local_time_to_cron, parse_hm
from oncall_ui.ui import (
    flash, guarded, invalidate_workflows, load_workflows, running_set, setup_page, trigger_run,
    viewer_tz,
)

RECURRENCES = ["daily", "weekly", "monthly"]

setup_page()
tz = viewer_tz()

head, action = st.columns([5, 1], vertical_alignment="bottom")
head.title("Schedule Management")
head.caption(f"Automate and monitor your on-call routines · times shown in {tz}")

workflows = guarded(load_workflows, "Failed to connect to agent-api: ") or []
schedules = [from_api(w, tz) for w in workflows if w.get("type") != "agent"]


def _save(name: str, payload: dict) -> bool:
    try:
        fresh = get_api().get_workflow(name)
        get_api().update_workflow(name, apply_schedule(fresh, payload))
    except ApiError as exc:
        st.error(f"Failed to save schedule: {exc}")
        return False
    invalidate_workflows()
    return True


@st.dialog("Add new schedule")
def add_dialog():
    if not schedules:
        st.info("No workflows available. Create one on the Workflows page first.")
        return
    title = st.text_input("Title *")
    wf_name = st.selectbox("Workflow *", [s["name"] for s in schedules])
    wf = next(s for s in schedules if s["name"] == wf_name)
    targets = target_nodes(wf)
    target = st.selectbox("Connect to node *", targets, format_func=lambda t: t["label"],
                          disabled=not targets, placeholder="No valid target nodes")
    c1, c2 = st.columns(2)
    recurrence = c1.selectbox("Recurrence", RECURRENCES, format_func=str.title)
    at = c2.time_input("Time", value=parse_hm("09:00"), step=300)
    if st.button("Add schedule", type="primary", use_container_width=True):
        errors = []
        if not title.strip():
            errors.append("Title is required")
        if not target:
            errors.append("Target node is required")
        if errors:
            for e in errors:
                st.error(e)
            return
        hm = at.strftime("%H:%M")
        if _save(wf_name, {"title": title.strip(), "target_node": target["id"], "recurrence": recurrence,
                           "start_time": hm, "schedule": local_time_to_cron(hm, recurrence, tz)}):
            flash("Schedule added", "success")
            st.rerun()


@st.dialog("Edit schedule")
def edit_dialog(name: str):
    try:
        sched = from_api(get_api().get_workflow(name), tz)
    except ApiError as exc:
        st.error(f"Error loading schedule: {exc}")
        return
    st.caption(f"Workflow: `{name}`")
    title = st.text_input("Title", value=sched["title"] or name)
    description = st.text_area("Description", value=sched["description"])
    c1, c2 = st.columns(2)
    rec = sched["recurrence"] if sched["recurrence"] in RECURRENCES else "daily"
    recurrence = c1.selectbox("Recurrence", RECURRENCES, index=RECURRENCES.index(rec), format_func=str.title)
    at = c2.time_input("Time", value=parse_hm(sched["start_time"]) or parse_hm("09:00"), step=300)
    enabled = st.toggle("Enabled", value=sched["enabled"])
    if st.button("Save changes", type="primary", use_container_width=True):
        if not title.strip():
            st.error("Title is required")
            return
        hm = at.strftime("%H:%M")
        if _save(name, {"title": title, "description": description, "recurrence": recurrence,
                        "start_time": hm, "schedule": local_time_to_cron(hm, recurrence, tz),
                        "enabled": enabled}):
            flash("Schedule updated", "success")
            st.rerun()


@st.dialog("Confirm delete")
def delete_dialog(name: str):
    st.write(f'Are you sure you want to delete the schedule "{name}"? '
             "This deletes the underlying workflow.")
    a, b = st.columns(2)
    if a.button("Cancel", use_container_width=True):
        st.rerun()
    if b.button("Delete", type="primary", use_container_width=True):
        try:
            get_api().delete_workflow(name)
            invalidate_workflows()
            flash("Schedule deleted", "success")
            st.rerun()
        except ApiError as exc:
            st.error(f"Failed to delete: {exc}")


if action.button("Add schedule", icon=":material/add:", type="primary", use_container_width=True):
    add_dialog()

search = st.text_input("Search schedules", placeholder="Search schedules…", label_visibility="collapsed")
rows = [s for s in schedules if search.lower() in (s["name"] or "").lower()
        or search.lower() in (s["title"] or "").lower()]


@st.fragment(run_every=10)
def schedule_table():
    if not rows:
        st.info("No schedules found." if not schedules else "No schedules match your search.")
        return
    running = running_set()
    with st.container(border=True):
        widths = [3, 3, 2, 2, 1, 1, 1]
        hdr = st.columns(widths)
        for col, label in zip(hdr, ["Title", "Workflow", "Local time", "Schedule", "Run", "", ""]):
            col.caption(label)
        for s in rows:
            c = st.columns(widths, vertical_alignment="center")
            c[0].markdown(f"**{s['title'] or s['name']}**" + ("" if s["enabled"] else " :gray[(disabled)]"))
            c[1].code(s["name"], language=None)
            c[2].write(s["start_time"] or "-")
            c[3].markdown(f"`{s['schedule'] or 'Manual'}`")
            if s["name"] in running:
                c[4].markdown(":orange[⏳]")
            elif c[4].button("▶", key=f"run_{s['name']}", help="Run now"):
                trigger_run(s["name"])
                st.rerun()
            if c[5].button("✏️", key=f"edit_{s['name']}", help="Edit"):
                edit_dialog(s["name"])
            if c[6].button("🗑️", key=f"del_{s['name']}", help="Delete"):
                delete_dialog(s["name"])


schedule_table()
