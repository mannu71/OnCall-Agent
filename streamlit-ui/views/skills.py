"""Skills — file-based markdown (SKILL.md) runbooks the agent loads on demand."""
from __future__ import annotations

import streamlit as st

from oncall_ui.api import ApiError, get_api
from oncall_ui.ui import flash, load_skills, setup_page

TEMPLATE = """---
name: my_new_skill
description: One sentence describing what this skill does and when to use it.
when_to_use: The trigger conditions, in the words a user would phrase the request.
---

## Protocol
1. Step one.
2. Step two.

## Rules
- Rule one.
"""

setup_page()
api = get_api()

h1, h2, h3 = st.columns([5, 1, 1], vertical_alignment="bottom")
h1.title("Skills")
h1.caption("File-based markdown (SKILL.md) skills — reusable runbooks the agent loads on demand via the "
           "skill tool, or that you invoke by typing /skill-name in chat.")


@st.dialog("Skill editor", width="large")
def edit_dialog(name: str | None):
    editing = name is not None
    content = TEMPLATE
    if editing:
        try:
            content = api.get_skill(name)["skill"]["content"]
        except (ApiError, KeyError, TypeError) as exc:
            st.error(f"Failed to load skill: {exc}")
            return
    upload = st.file_uploader("Load from a .md / .mdx file", type=["md", "mdx"])
    default_name = name or ""
    if upload is not None:
        content = upload.getvalue().decode("utf-8", "replace")
        if not editing:
            default_name = upload.name.rsplit(".", 1)[0]
    skill_name = st.text_input("Name", value=default_name, disabled=editing,
                               key=f"skill_name_{upload.name if upload else ''}")
    body = st.text_area("SKILL.md content", value=content, height=420,
                        key=f"skill_body_{name}_{upload.name if upload else ''}")
    if st.button("Save", type="primary", use_container_width=True):
        if not editing and not skill_name.strip():
            st.error("Name is required.")
            return
        try:
            if editing:
                api.update_skill(name, body)
            else:
                api.create_skill(skill_name.strip(), body)
        except ApiError as exc:
            st.error(str(exc))
            return
        load_skills.clear()
        flash("Skill saved", "success")
        st.rerun()


@st.dialog("Skill", width="large")
def view_dialog(name: str):
    try:
        skill = api.get_skill(name)["skill"]
    except (ApiError, KeyError, TypeError) as exc:
        st.error(str(exc))
        return
    st.subheader(skill.get("name") or name)
    st.code(skill.get("content") or "", language="markdown")
    if skill.get("editable", True) and st.button("Edit", icon=":material/edit:"):
        # Only one dialog can be open at a time — reopen as the editor.
        st.session_state["_skill_edit"] = name
        st.rerun()


@st.dialog("Delete skill?")
def delete_dialog(name: str):
    st.write(f'Delete skill "{name}"? A custom skill is removed from disk; a bundled skill is hidden. '
             "You can recreate it later with the same name.")
    a, b = st.columns(2)
    if a.button("Cancel", use_container_width=True):
        st.rerun()
    if b.button("Delete", type="primary", use_container_width=True):
        try:
            api.delete_skill(name)
        except ApiError as exc:
            st.error(str(exc))
            return
        load_skills.clear()
        flash(f"Deleted {name}", "success")
        st.rerun()


if h2.button("Refresh", icon=":material/refresh:", use_container_width=True):
    load_skills.clear()
    st.rerun()
if h3.button("New skill", icon=":material/add:", type="primary", use_container_width=True):
    edit_dialog(None)
elif st.session_state.get("_skill_edit"):
    edit_dialog(st.session_state.pop("_skill_edit"))

try:
    skills = load_skills()
except ApiError as exc:
    st.error(f"Failed to load skills: {exc}")
    skills = []

st.subheader(f"Skills ({len(skills)})")
if not skills:
    st.info("No skills yet. Create one, or upload a SKILL.md file.")
cols = st.columns(2)
for i, s in enumerate(skills):
    with cols[i % 2].container(border=True):
        st.markdown(f"**📄 {s.get('name')}**")
        if s.get("description"):
            st.caption(s["description"])
        if s.get("when_to_use"):
            st.caption(f"*When to use:* {s['when_to_use']}")
        a, b, c = st.columns(3)
        if a.button("View", key=f"view_{s['name']}", use_container_width=True):
            view_dialog(s["name"])
        if b.button("Edit", key=f"edit_{s['name']}", use_container_width=True,
                    disabled=s.get("editable") is False):
            edit_dialog(s["name"])
        if c.button("Delete", key=f"del_{s['name']}", use_container_width=True):
            delete_dialog(s["name"])
