"""Bridge between the workflow model and the streamlit-flow (React Flow) canvas.

streamlit-flow nodes have a single input and output handle, so an edge drawn
on the canvas carries no port ids. The editor keeps the canonical graph
(slot-level edges) in session state and uses this module to:

* render it as a :class:`StreamlitFlowState` (:func:`to_flow_state`);
* fold the canvas's returned state back into the model (:func:`sync_from_flow`)
  — positions, deletions, new edges (ports inferred from port types) and
  subagent-window membership by drop position.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from streamlit_flow.elements import StreamlitFlowEdge, StreamlitFlowNode
from streamlit_flow.state import StreamlitFlowState

from . import workflow_model as wm

ICONS = {
    "clock": "⏰", "sparkle": "✨", "db": "🗄️", "cloud": "☁️", "server": "🖥️", "search": "🔎",
    "workflow": "🤖", "funnel": "🔀", "wiki": "📘",
}
NODE_CARD_W = 230
WINDOW_HEADER = 40
# Rough rendered card height, used for drop-point centring.
NODE_CARD_H = 90

SUMMARY_KEYS = {
    "schedule": ["frequency", "time"],
    "language_model": ["llm", "temp"],
    "vector_memory": ["memoryTypes"],
    "cloudwatch_tool": ["region", "groups", "range"],
    "postgres_tool": ["connection"],
    "database": ["server"],
    "mcp_server": ["servers"],
    "code_search_tool": ["repos"],
    "orchestrator": ["sqlFile"],
    "agent": ["maxIter", "skills"],
    "if": ["condition"],
    "wiki": ["platform", "pagePath"],
}


def node_icon(node_type: str) -> str:
    return ICONS.get(wm.NODE_TYPES.get(node_type, {}).get("icon"), "▫️")


def _summary(node: dict) -> str:
    p = node.get("params") or {}
    parts = []
    for k in SUMMARY_KEYS.get(node["type"], []):
        v = str(p.get(k) or "").strip()
        if v:
            parts.append(f"{k}: {v if len(v) <= 28 else v[:26] + '…'}")
    return "  \n".join(parts[:3])


def node_content(node: dict, live_status: Optional[str] = None) -> str:
    d = wm.NODE_TYPES.get(node["type"], {})
    if node["type"] == "subagent_window":
        name = (node.get("params") or {}).get("name") or node.get("name") or "Subagent"
        return f"**🗂️ Subagent: {name}**  \n_drop tool nodes inside_"
    status = {"running": " ⏳", "success": " ✅", "error": " ❌"}.get(live_status or "", "")
    head = f"**{node_icon(node['type'])} {node.get('name') or d.get('label', node['type'])}**{status}"
    sub = f"_{d.get('label', node['type'])}_"
    body = _summary(node)
    return f"{head}  \n{sub}" + (f"  \n{body}" if body else "")


def _flow_node(node: dict, selected: bool, live_status: Optional[str]) -> StreamlitFlowNode:
    d = wm.NODE_TYPES.get(node["type"], {})
    tint = wm.CAT_TINT.get(d.get("category"), wm.CAT_TINT["Logic"])
    ins, outs = wm.ports(node, "in"), wm.ports(node, "out")
    if node["type"] == "subagent_window":
        _, _, w, h = wm.window_rect(node)
        style = {"width": f"{int(w)}px", "height": f"{int(h)}px", "border": f"2px dashed {tint['accent']}",
                 "background": "rgba(124,58,237,0.04)", "borderRadius": "14px", "textAlign": "left",
                 "padding": "8px 10px", "fontSize": "12px", "color": tint["fg"]}
        return StreamlitFlowNode(node["id"], (node.get("x", 0), node.get("y", 0)), {"content": node_content(node)},
                                 node_type="input" if not ins else "default", source_position="right",
                                 target_position="left", selectable=True, connectable=True, deletable=True,
                                 z_index=-1, style=style, selected=selected)
    kind = "input" if not ins else ("output" if not outs else "default")
    border = "#0f172a" if selected else tint["border"]
    style = {"width": f"{NODE_CARD_W}px", "background": tint["bg"], "color": tint["fg"],
             "border": f"{'2px' if selected else '1px'} solid {border}",
             "borderLeft": f"5px solid {tint['accent']}", "borderRadius": "10px", "textAlign": "left",
             "fontSize": "12px", "padding": "8px 10px"}
    return StreamlitFlowNode(node["id"], (node.get("x", 0), node.get("y", 0)),
                             {"content": node_content(node, live_status)}, node_type=kind,
                             source_position="right", target_position="left", selectable=True,
                             connectable=True, deletable=True, z_index=10, style=style, selected=selected)


def edge_label(edge: dict, nodes_by_id: Dict[str, dict]) -> str:
    src = nodes_by_id.get(edge["source"])
    slot = wm.find_slot(src, edge.get("sourceSlot")) if src else None
    port = (slot or {}).get("portType") or "message"
    return wm.PORT_TYPE.get(port, {}).get("label", port)


def _flow_edge(edge: dict, nodes_by_id: Dict[str, dict], selected: bool) -> StreamlitFlowEdge:
    src = nodes_by_id.get(edge["source"])
    slot = wm.find_slot(src, edge.get("sourceSlot")) if src else None
    color = wm.PORT_TYPE.get((slot or {}).get("portType") or "message", {}).get("color", "#475569")
    label = edge.get("sourceSlot", "")
    label = label.split("::", 1)[1] if "::" in label else ""
    return StreamlitFlowEdge(edge["id"], edge["source"], edge["target"], edge_type="default",
                             marker_end={"type": "arrowclosed", "color": color},
                             animated=(slot or {}).get("portType") == "trigger", deletable=True,
                             focusable=True, selected=selected, label=label,
                             style={"stroke": color, "strokeWidth": 3 if selected else 2})


def to_flow_state(nodes: List[dict], edges: List[dict], selected: Optional[str] = None,
                  live: Optional[Dict[str, str]] = None) -> StreamlitFlowState:
    by_id = {n["id"]: n for n in nodes}
    # Windows first so member cards paint on top of the frame.
    ordered = sorted(nodes, key=lambda n: 0 if n["type"] == "subagent_window" else 1)
    return StreamlitFlowState(
        nodes=[_flow_node(n, n["id"] == selected, (live or {}).get(n["id"])) for n in ordered],
        edges=[_flow_edge(e, by_id, e["id"] == selected) for e in edges
               if e["source"] in by_id and e["target"] in by_id],
        selected_id=selected,
    )


def sync_from_flow(nodes: List[dict], edges: List[dict], flow: StreamlitFlowState
                   ) -> Tuple[List[dict], List[dict], bool, List[str]]:
    """Fold the canvas state into the model.

    Returns ``(nodes, edges, rebuild, messages)`` — ``rebuild`` is True when
    the model diverged from what the canvas shows (rejected edge, membership
    re-layout, …) so the caller must push a fresh state to the canvas.
    """
    msgs: List[str] = []
    rebuild = False
    flow_nodes = {fn.id: fn for fn in flow.nodes}
    flow_edge_ids = {fe.id for fe in flow.edges}

    # Deleted on the canvas (node menu / Delete key).
    for n in list(nodes):
        if n["id"] not in flow_nodes:
            nodes, edges = wm.delete_node(nodes, edges, n["id"])
            rebuild = True

    # Positions.
    moved_windows = []
    moved_nodes = []
    updated = []
    for n in nodes:
        fn = flow_nodes.get(n["id"])
        if fn is None:
            updated.append(n)
            continue
        x, y = round(fn.position.get("x", 0)), round(fn.position.get("y", 0))
        if (x, y) != (round(n.get("x", 0)), round(n.get("y", 0))):
            (moved_windows if n["type"] == "subagent_window" else moved_nodes).append(n["id"])
            n = {**n, "x": x, "y": y}
        updated.append(n)
    nodes = updated

    # Subagent membership by drop position (tool nodes only).
    for nid in moved_nodes:
        n = next(m for m in nodes if m["id"] == nid)
        if not wm.is_tool_node_type(n["type"]):
            continue
        win = wm.window_at(nodes, n["x"] + NODE_CARD_W / 2, n["y"] + NODE_CARD_H / 2)
        new_parent = win["id"] if win else None
        if n.get("parentId") != new_parent:
            old_parent = n.get("parentId")
            nodes, edges = wm.set_membership(nodes, edges, nid, new_parent)
            if old_parent:
                nodes = wm.layout_window_members(nodes, old_parent, shrink=True)
            msgs.append(f"{n.get('name')} → " + (f"subagent '{(win.get('params') or {}).get('name')}'"
                                                   if win else "main agent"))
            rebuild = True
        elif new_parent:
            nodes = wm.layout_window_members(nodes, new_parent)  # snap back into its slot
            rebuild = True
    # A window drags its members along.
    for wid in moved_windows:
        nodes = wm.layout_window_members(nodes, wid)
        rebuild = True

    # Edges deleted on the canvas.
    before = len(edges)
    edges = [e for e in edges if e["id"] in flow_edge_ids or not _shown(e, nodes)]
    rebuild = rebuild or len(edges) != before

    # Edges drawn on the canvas — infer the port pair from port types.
    known = {e["id"] for e in edges}
    by_id = {n["id"]: n for n in nodes}
    for fe in flow.edges:
        if fe.id in known:
            continue
        src, tgt = by_id.get(fe.source), by_id.get(fe.target)
        rebuild = True  # replace the canvas's provisional edge with ours (or drop it)
        if not src or not tgt:
            continue
        pair = wm.infer_connection(src, tgt, nodes, edges)
        if not pair:
            msgs.append(f"Can't connect {src.get('name')} → {tgt.get('name')}: no compatible free ports.")
            continue
        edges.append({"id": wm.new_id("e"), "source": src["id"], "sourceSlot": pair[0],
                      "target": tgt["id"], "targetSlot": pair[1]})
        msgs.append(f"Connected {src.get('name')}.{pair[0]} → {tgt.get('name')}.{pair[1]}")
    return nodes, edges, rebuild, msgs


def _shown(edge: dict, nodes: List[dict]) -> bool:
    ids = {n["id"] for n in nodes}
    return edge["source"] in ids and edge["target"] in ids
