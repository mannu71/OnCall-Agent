"""Codebase Explorer — manage codegraph-indexed repos and browse their graph."""
from __future__ import annotations

from collections import Counter, defaultdict

import plotly.graph_objects as go
import streamlit as st

from oncall_ui.api import ApiError, get_api
from oncall_ui.ui import flash, guarded, setup_page

MAX_EDGES_DRAWN = 4000

setup_page()
api = get_api()
ss = st.session_state

st.title("Codebase Explorer")
st.caption("Repositories indexed by the codegraph engine (Code Crawler) — the source behind every "
           "`codegraph__*` tool the agent calls.")


@st.cache_data(ttl=30, show_spinner=False)
def _repos():
    return (get_api().codegraph_repos() or {}).get("repos", []) or []


@st.cache_data(ttl=300, show_spinner="Computing layout…")
def _layout(repo: str, max_nodes: int):
    return get_api().codegraph_layout(repo, level="overview", max_nodes=max_nodes)


repos = guarded(_repos, "Failed to load repositories: ") or []
side, main = st.columns([1, 3])

with side:
    if not repos:
        st.info("No Code Crawler-indexed repos yet. Add a Code Search node to a workflow and run it, "
                "or reindex from here once a repo is listed.")
        st.stop()
    names = [r["repo_name"] for r in repos]
    pref = ss.get("cg_repo") if ss.get("cg_repo") in names else names[0]
    repo = st.selectbox("Repository", names, index=names.index(pref),
                        format_func=lambda n: f"{n} ({next(r for r in repos if r['repo_name'] == n).get('files_indexed', 0)} files)")
    ss.cg_repo = repo
    meta = next(r for r in repos if r["repo_name"] == repo)
    with st.expander("Index details"):
        st.json(meta)

    # Branch picker — the index is built from whatever is checked out on disk.
    info = guarded(lambda: api.repo_branches(repo), quiet=True)
    if ss.get("cg_fetched", {}).get(repo):
        info = ss.cg_fetched[repo]
    if info and info.get("is_git"):
        if info.get("detached"):
            st.caption("Detached HEAD — check out a branch on disk to switch.")
        else:
            local = info.get("local") or []
            remote = info.get("remote") or []
            options = local + [b for b in remote if b not in local]
            cur = info.get("current")
            branch = st.selectbox("Branch", options, index=options.index(cur) if cur in options else 0,
                                  format_func=lambda b: f"{b} (current)" if b == cur else
                                  (f"{b} (remote)" if b not in local else b))
            if info.get("dirty"):
                st.caption(":orange[Working tree has uncommitted changes]")
            b1, b2 = st.columns(2)
            if b1.button("Fetch", icon=":material/cloud_download:", use_container_width=True,
                         help="git fetch --prune from the origin"):
                try:
                    with st.spinner("Fetching…"):
                        fetched = api.repo_fetch(repo)
                    ss.setdefault("cg_fetched", {})[repo] = fetched
                    if fetched.get("ok") is False:
                        flash(f"Fetch could not reach the remote: {fetched.get('error') or 'unknown error'}. "
                              "Showing local branches.", "warning")
                except ApiError as exc:
                    flash(f"Fetch failed: {exc}", "error")
                st.rerun()
            if b2.button("Switch & reindex", use_container_width=True, disabled=not branch or branch == cur):
                try:
                    with st.spinner(f"Checking out {branch} and rebuilding the index…"):
                        api.repo_checkout(repo, branch)
                        api.codegraph_reindex(repo)
                    ss.get("cg_fetched", {}).pop(repo, None)
                    _repos.clear()
                    _layout.clear()
                    flash(f"Switched {repo} to {branch}", "success")
                except ApiError as exc:
                    d = exc.detail if isinstance(exc.detail, dict) else {}
                    if exc.status == 409 and d:
                        files = ", ".join((d.get("dirty_files") or [])[:10])
                        flash(f"{d.get('message') or 'Repo has uncommitted changes.'}"
                              + (f" Modified: {files}" if files else ""), "error")
                    else:
                        flash(f"Switch failed: {exc}", "error")
                st.rerun()
    elif info is not None:
        st.caption("Not a git checkout — branch switching unavailable.")

    st.divider()
    r1, r2 = st.columns(2)
    if r1.button("Reindex", icon=":material/refresh:", use_container_width=True):
        try:
            with st.spinner("Reindexing…"):
                api.codegraph_reindex(repo)
            _repos.clear()
            _layout.clear()
            flash("Reindex complete", "success")
        except ApiError as exc:
            flash(f"Reindex failed: {exc}", "error")
        st.rerun()
    if r2.button("Delete index", icon=":material/delete:", use_container_width=True):
        ss.cg_confirm_delete = repo
    if ss.get("cg_confirm_delete") == repo:
        st.warning(f'Delete the entire index for "{repo}"? This removes its knowledge graph and overview. '
                   "You can rebuild it later by re-indexing.")
        c1, c2 = st.columns(2)
        if c1.button("Cancel", use_container_width=True):
            ss.cg_confirm_delete = None
            st.rerun()
        if c2.button("Delete", type="primary", use_container_width=True):
            try:
                api.codegraph_delete_index(repo)
                _repos.clear()
                _layout.clear()
                flash(f"Deleted index for {repo}", "success")
            except ApiError as exc:
                flash(f"Delete failed: {exc}", "error")
            ss.cg_confirm_delete = None
            st.rerun()

with main:
    max_nodes = st.select_slider("Max nodes", [500, 1000, 2000, 5000, 10000], value=2000)
    try:
        layout = _layout(repo, max_nodes)
    except ApiError as exc:
        st.error(f"Failed to fetch layout: {exc}")
        st.stop()
    nodes, edges = layout.get("nodes") or [], layout.get("edges") or []
    if not nodes:
        st.info("This repository's graph is empty. Try reindexing it.")
        st.stop()
    label_counts = Counter(n.get("label") or "?" for n in nodes)
    edge_counts = Counter(e.get("type") or "?" for e in edges)
    f1, f2, f3 = st.columns([2, 2, 1])
    labels = f1.multiselect("Node types", sorted(label_counts), default=sorted(label_counts),
                            format_func=lambda l: f"{l} ({label_counts[l]})")
    etypes = f2.multiselect("Edge types", sorted(edge_counts), default=sorted(edge_counts),
                            format_func=lambda t: f"{t} ({edge_counts[t]})")
    dims = f3.radio("View", ["3D", "2D"], horizontal=True)
    dirs = sorted({p.rsplit("/", 1)[0] for n in nodes if (p := n.get("file_path")) and "/" in p})
    path = st.selectbox("Focus path", ["(all)"] + dirs, help="Highlight nodes under a directory")

    shown = [n for n in nodes if (n.get("label") or "?") in labels]
    ids = {n["id"] for n in shown}
    by_id = {n["id"]: n for n in shown}
    vis_edges = [e for e in edges if (e.get("type") or "?") in etypes and e["source"] in ids and e["target"] in ids]
    focus = {n["id"] for n in shown if path != "(all)" and str(n.get("file_path") or "").startswith(path + "/")}

    ex, ey, ez = [], [], []
    for e in vis_edges[:MAX_EDGES_DRAWN]:
        a, b = by_id[e["source"]], by_id[e["target"]]
        ex += [a["x"], b["x"], None]
        ey += [a["y"], b["y"], None]
        ez += [a.get("z", 0), b.get("z", 0), None]
    sizes = [max(3, min(16, 3 + 2 * float(n.get("size") or 1))) for n in shown]
    colors = [n.get("color") or "#38bdf8" for n in shown]
    opacity = [1.0 if not focus or n["id"] in focus else 0.15 for n in shown]
    hover = [f"<b>{n.get('name')}</b><br>{n.get('label')}<br>{n.get('file_path') or ''}" for n in shown]
    fig = go.Figure()
    if dims == "3D":
        fig.add_trace(go.Scatter3d(x=ex, y=ey, z=ez, mode="lines", hoverinfo="skip",
                                   line=dict(color="rgba(148,163,184,0.25)", width=1)))
        fig.add_trace(go.Scatter3d(x=[n["x"] for n in shown], y=[n["y"] for n in shown],
                                   z=[n.get("z", 0) for n in shown], mode="markers", text=hover,
                                   hovertemplate="%{text}<extra></extra>",
                                   marker=dict(size=sizes, color=colors, opacity=0.9, line=dict(width=0))))
        fig.update_layout(scene=dict(xaxis=dict(visible=False), yaxis=dict(visible=False),
                                     zaxis=dict(visible=False), bgcolor="#020617"))
    else:
        fig.add_trace(go.Scattergl(x=ex, y=ey, mode="lines", hoverinfo="skip",
                                   line=dict(color="rgba(148,163,184,0.25)", width=1)))
        fig.add_trace(go.Scattergl(x=[n["x"] for n in shown], y=[n["y"] for n in shown], mode="markers",
                                   text=hover, customdata=[n["id"] for n in shown],
                                   hovertemplate="%{text}<extra></extra>",
                                   marker=dict(size=sizes, color=colors, opacity=opacity)))
        fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x"),
                          plot_bgcolor="#020617")
    fig.update_layout(height=620, margin=dict(l=0, r=0, t=0, b=0), showlegend=False, paper_bgcolor="#020617")
    event = st.plotly_chart(fig, use_container_width=True, on_select="rerun" if dims == "2D" else "ignore",
                            key=f"cg_plot_{dims}")
    st.caption(f"{len(shown):,} of {layout.get('total_nodes', len(nodes)):,} nodes · {len(vis_edges):,} edges"
               + (f" (drawing first {MAX_EDGES_DRAWN:,})" if len(vis_edges) > MAX_EDGES_DRAWN else ""))

    picked_id = None
    pts = (event or {}).get("selection", {}).get("points", []) if dims == "2D" else []
    if pts and pts[0].get("customdata") is not None:
        picked_id = pts[0]["customdata"]
    names_sorted = sorted(shown, key=lambda n: (n.get("name") or "").lower())
    choice = st.selectbox("Inspect node", [None] + [n["id"] for n in names_sorted],
                          index=([None] + [n["id"] for n in names_sorted]).index(picked_id) if picked_id in ids else 0,
                          format_func=lambda i: "—" if i is None else
                          f"{by_id[i].get('name')} · {by_id[i].get('label')}")
    if choice is not None:
        node = by_id[choice]
        with st.container(border=True):
            st.markdown(f"### {node.get('name')}  ")
            st.markdown(f"`{node.get('label')}`" + (f" · `{node['file_path']}`" if node.get("file_path") else ""))
            out_e = defaultdict(list)
            in_e = defaultdict(list)
            for e in edges:
                if e["source"] == choice and e["target"] in by_id:
                    out_e[e.get("type") or "?"].append(by_id[e["target"]])
                elif e["target"] == choice and e["source"] in by_id:
                    in_e[e.get("type") or "?"].append(by_id[e["source"]])
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Outgoing**")
                for t, ns in out_e.items():
                    st.caption(f"{t} ({len(ns)})")
                    st.markdown("\n".join(f"- {n.get('name')} · {n.get('label')}" for n in ns[:25]))
            with c2:
                st.markdown("**Incoming**")
                for t, ns in in_e.items():
                    st.caption(f"{t} ({len(ns)})")
                    st.markdown("\n".join(f"- {n.get('name')} · {n.get('label')}" for n in ns[:25]))
            if node.get("file_path"):
                with st.expander("Symbols in this file"):
                    data = guarded(lambda: api.codegraph_file_nodes(repo, node["file_path"]), quiet=True) or {}
                    rows = [{"name": n.get("name"), "label": n.get("label"),
                             "qualified_name": n.get("qualified_name"), "line": n.get("start_line")}
                            for n in data.get("nodes") or []]
                    if rows:
                        st.dataframe(rows, hide_index=True, use_container_width=True)
                    else:
                        st.caption("No symbol detail available.")
