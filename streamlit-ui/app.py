"""OnCall Agent — Streamlit UI entry point.

Run locally:   streamlit run app.py   (AGENT_API_URL defaults to http://localhost:48000)
"""
from __future__ import annotations

import streamlit as st

from oncall_ui import config
from oncall_ui.api import ApiError, get_api
from oncall_ui.ui import running_set

st.set_page_config(page_title="OnCall Agent", page_icon="🚨", layout="wide",
                   initial_sidebar_state="expanded")

PAGES = [
    # The default page is served at "/" (Streamlit ignores url_path for it).
    st.Page("views/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("views/workflows.py", title="Workflows", icon=":material/account_tree:", url_path="workflow"),
    st.Page("views/scheduler.py", title="Scheduler", icon=":material/schedule:", url_path="scheduler"),
    st.Page("views/chat.py", title="Chat", icon=":material/chat:", url_path="chat"),
    st.Page("views/explorer.py", title="Codebase Explorer", icon=":material/hub:", url_path="explorer"),
    st.Page("views/skills.py", title="Skills", icon=":material/menu_book:", url_path="skills"),
    st.Page("views/settings.py", title="Settings", icon=":material/settings:", url_path="settings"),
]

nav = st.navigation(PAGES)


@st.fragment(run_every=30)
def _health_badge() -> None:
    try:
        h = get_api().health()
        ok = (h or {}).get("status") == "healthy"
    except ApiError:
        h, ok = None, False
    if ok:
        st.markdown(":green[●] **API healthy**")
        sched = "running" if h.get("scheduler_running") else "stopped"
        st.caption(f"Scheduler {sched} · {len(running_set())} running")
    else:
        st.markdown(":red[●] **API unreachable**")
        st.caption(config.api_base_url())


with st.sidebar:
    st.markdown("### 🚨 OnCall Agent")
    _health_badge()
    st.caption(f"UI v{config.APP_VERSION}")

nav.run()
