"""Workflow graph model — node catalog, port rules and save-time export.

Pure Python (no Streamlit) port of the React editor's
``nodeDefinitions.js``, ``portValidation.js``, ``workflowValidation.js`` and
the model helpers inside ``LangflowEditor.jsx``. The saved format is
unchanged, so workflows round-trip between both UIs:

* node: ``{id, type, x, y, name, status, params: {slot_id: str}, parentId?}``
* edge: ``{id, source, sourceSlot, target, targetSlot}``
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DATA = Path(__file__).parent / "data"
_CATALOG = json.loads((_DATA / "node_catalog.json").read_text(encoding="utf-8"))

NODE_TYPES: Dict[str, dict] = _CATALOG["NODE_TYPES"]
NODE_DEFAULTS: Dict[str, dict] = _CATALOG["NODE_DEFAULTS"]
CAT_TINT: Dict[str, dict] = _CATALOG["CAT_TINT"]
PORT_TYPE: Dict[str, dict] = _CATALOG["PORT_TYPE"]
CATEGORY_ORDER = ["Inputs", "Models", "Memory", "Tools", "Agents", "Logic", "Outputs"]

WORKFLOW_TEMPLATES: List[dict] = json.loads(
    (_DATA / "workflow_templates.json").read_text(encoding="utf-8"))

DEFAULT_SUBAGENT_WIN_W = 300
DEFAULT_SUBAGENT_WIN_H = 360
NODE_W = 244

REJECT = {
    "MISSING": "Node or slot not found.",
    "SELF": "Cannot connect a node to itself.",
    "DIRECTION": "Connect an output port to an input port.",
    "PORT_TYPE": "Port types do not match.",
    "SLOT_FULL": "This input already has a connection.",
    "DUPLICATE": "This connection already exists.",
}

# Legacy ReactFlow handle ids → slot ids (pre-rewrite saved workflows).
OLD_HANDLE_TO_SLOT = {
    "scheduler-output": "trigger", "llm-output": "lm", "source-left": "trigger",
    "output": "response", "response": "response", "agent-input": "input",
    "model": "lm", "memory": "memory", "tool": "tool", "tools": "tools",
    "trigger": "trigger", "input": "input", "msg": "msg", "body": "body",
}

LLM_TYPES = {"llm", "language_model"}
NON_TOOL_TYPES = {"agent", "llm", "language_model", "schedule", "scheduler", "trigger", "memory"}


def split_csv(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


# ── palette ───────────────────────────────────────────────────────────────
def palette() -> Dict[str, List[Tuple[str, dict]]]:
    """Visible node types grouped by category, in display order."""
    groups: Dict[str, List[Tuple[str, dict]]] = {c: [] for c in CATEGORY_ORDER}
    for t, d in NODE_TYPES.items():
        if d.get("paletteHidden"):
            continue
        groups.setdefault(d["category"], []).append((t, d))
    return {c: items for c, items in groups.items() if items}


def make_node(node_type: str, x: float = 0, y: float = 0, *, global_tz: str = "UTC") -> dict:
    d = NODE_TYPES[node_type]
    params = copy.deepcopy(NODE_DEFAULTS.get(node_type, {}))
    # New schedule nodes default to the global timezone, not a hardcoded UTC.
    if node_type == "schedule" and (not params.get("tz") or params.get("tz") == "UTC"):
        params["tz"] = global_tz
    return {"id": new_id(node_type), "type": node_type, "x": round(x), "y": round(y),
            "name": d["label"], "status": "idle", "params": params}


# ── legacy normalisation ──────────────────────────────────────────────────
def _data_to_params(node_type: str, data: dict) -> dict:
    if node_type == "agent":
        return {"system": data.get("instructions") or data.get("system") or "",
                "maxIter": str(data.get("maxIterations") or data.get("maxIter") or "10")}
    if node_type == "schedule":
        return {"cron": data.get("cron") or "*/15 * * * *", "tz": data.get("tz") or "UTC"}
    if node_type == "vector_memory":
        return {"collection": data.get("collection") or "", "topK": str(data.get("topK") or "5")}
    if node_type == "cloudwatch_tool":
        groups = data.get("logGroups", data.get("groups", ""))
        groups = ",".join(groups) if isinstance(groups, list) else str(groups or "")
        return {
            "region": data.get("awsRegion") or data.get("region") or "us-east-1",
            "profile": data.get("awsProfile") or data.get("profile") or "",
            "groups": groups,
            "analysis": data.get("analysisType") or data.get("analysis") or "error-patterns",
            "analysis_depth": data.get("analysisDepth") or data.get("analysis_depth") or "auto",
            "range": data.get("timeRange") or data.get("range") or "15m",
            "threshold": str(data.get("errorThreshold", data.get("threshold", "10"))),
            "alerts": str(data.get("enableAlerts", data.get("alerts", "false"))).lower(),
        }
    if node_type == "postgres_tool":
        return {"connection": data.get("connection") or data.get("connectionString") or ""}
    if node_type == "wiki":
        return {k: data.get(k) or dflt for k, dflt in (
            ("format", "Summary"), ("platform", "Azure DevOps Wiki"), ("wikiUrl", ""),
            ("pagePath", ""), ("project", ""), ("pat", ""), ("tokenVar", "ADO_WIKI_PAT"))}
    if node_type == "if":
        return {"condition": data.get("condition") or ""}
    if node_type == "orchestrator":
        return {"sqlFile": data.get("sqlFile") or data.get("fileName") or ""}
    return {k: v for k, v in data.items() if not isinstance(v, (dict, list)) and not callable(v)}


def normalize_node(n: dict) -> dict:
    """Convert an old ReactFlow node (``position``/``data``) to the new schema."""
    if n.get("position") is None and n.get("x") is not None:
        out = dict(n)
        out.setdefault("params", {})
        out.setdefault("name", NODE_TYPES.get(n.get("type"), {}).get("label", n.get("type")))
        return out
    data = n.get("data") or {}
    pos = n.get("position") or {}
    return {
        **n,
        "x": pos.get("x", 0) or 0,
        "y": pos.get("y", 0) or 0,
        "name": n.get("name") or data.get("label") or data.get("name") or n.get("type"),
        "status": n.get("status") or "idle",
        "params": n.get("params") or _data_to_params(n.get("type"), data),
    }


def normalize_edge(e: dict) -> dict:
    src = e.get("sourceSlot") or OLD_HANDLE_TO_SLOT.get(e.get("sourceHandle")) or e.get("sourceHandle") or "output"
    tgt = e.get("targetSlot") or OLD_HANDLE_TO_SLOT.get(e.get("targetHandle")) or e.get("targetHandle") or "input"
    return {**e, "sourceSlot": src, "targetSlot": tgt}


# ── slots / ports ─────────────────────────────────────────────────────────
def model_names_of(node: Optional[dict]) -> List[str]:
    p = (node or {}).get("params") or {}
    models = p.get("models")
    if isinstance(models, list) and models:
        return [str(m if isinstance(m, str) else (m or {}).get("name")) for m in models
                if (m if isinstance(m, str) else (m or {}).get("name"))]
    return split_csv(p.get("llm"))


def mcp_server_names_of(node: Optional[dict]) -> List[str]:
    return split_csv(((node or {}).get("params") or {}).get("servers"))


def slots_for_node(node: Optional[dict]) -> List[dict]:
    """Effective slots: a Language Model with 2+ models exposes one ``lm::<name>``
    output per model; an MCP node with 2+ servers one ``mcp::<name>`` each."""
    d = NODE_TYPES.get((node or {}).get("type"))
    if not d:
        return []
    base = d.get("slots", [])
    if node["type"] == "language_model":
        names = model_names_of(node)
        if len(names) > 1:
            out = []
            for s in base:
                if s["kind"] == "port-out" and s["id"] == "lm":
                    out += [{"kind": "port-out", "id": f"lm::{n}", "label": n, "portType": "model"} for n in names]
                else:
                    out.append(s)
            return out
    if node["type"] == "mcp_server":
        names = mcp_server_names_of(node)
        if len(names) > 1:
            out = []
            for s in base:
                if s["kind"] == "port-out" and s["id"] == "tool":
                    out += [{"kind": "port-out", "id": f"mcp::{n}", "label": n, "portType": "tool"} for n in names]
                else:
                    out.append(s)
            return out
    return base


def visible_slots(node: dict) -> List[dict]:
    """Slots shown for a node: a subagent-window member has no tool output."""
    slots = [s for s in slots_for_node(node) if not s.get("advanced")]
    if not node.get("parentId"):
        return slots
    return [s for s in slots if not (s["kind"] == "port-out" and s.get("portType") == "tool")]


def ports(node: dict, direction: str) -> List[dict]:
    kind = "port-in" if direction == "in" else "port-out"
    return [s for s in visible_slots(node) if s["kind"] == kind]


def find_slot(node: Optional[dict], slot_id: str) -> Optional[dict]:
    if not node:
        return None
    return next((s for s in slots_for_node(node) if s["id"] == slot_id), None)


def validate_connection(source_id: str, source_slot: str, target_id: str, target_slot: str,
                        nodes: List[dict], edges: List[dict]) -> Tuple[bool, str]:
    if source_id == target_id:
        return False, REJECT["SELF"]
    src = next((n for n in nodes if n["id"] == source_id), None)
    tgt = next((n for n in nodes if n["id"] == target_id), None)
    if not src or not tgt:
        return False, REJECT["MISSING"]
    ss, ts = find_slot(src, source_slot), find_slot(tgt, target_slot)
    if not ss or not ts:
        return False, REJECT["MISSING"]
    if ss["kind"] != "port-out" or ts["kind"] != "port-in":
        return False, REJECT["DIRECTION"]
    st, tt = ss.get("portType", "message"), ts.get("portType", "message")
    if not (st == tt or (st == "data" and tt == "message")):
        return False, REJECT["PORT_TYPE"]
    incoming = [e for e in edges if e["target"] == target_id and e["targetSlot"] == target_slot]
    if any(e["source"] == source_id and e["sourceSlot"] == source_slot for e in incoming):
        return False, REJECT["DUPLICATE"]
    if not ts.get("multi") and incoming:
        return False, REJECT["SLOT_FULL"]
    return True, ""


def compatible_port_pairs(source: dict, target: dict, nodes: List[dict],
                          edges: List[dict]) -> List[Tuple[str, str]]:
    """Every (source out-slot, target in-slot) pair that would validate."""
    pairs = []
    for so in ports(source, "out"):
        for ti in ports(target, "in"):
            ok, _ = validate_connection(source["id"], so["id"], target["id"], ti["id"], nodes, edges)
            if ok:
                pairs.append((so["id"], ti["id"]))
    return pairs


def infer_connection(source: dict, target: dict, nodes: List[dict],
                     edges: List[dict]) -> Optional[Tuple[str, str]]:
    """Port pair for an edge drawn node-to-node on the canvas (which carries no
    handle ids): the first valid pair, preferring required inputs."""
    pairs = compatible_port_pairs(source, target, nodes, edges)
    if not pairs:
        return None
    def rank(pair):
        ts = find_slot(target, pair[1]) or {}
        return (1 if ts.get("optional") else 0)
    return sorted(pairs, key=rank)[0]


def reconcile_model_edges(edges: List[dict], node_id: str, old: dict, new: dict) -> List[dict]:
    """Re-point/prune edges out of a Language Model after its model set changes."""
    old_names, new_names = model_names_of(old), model_names_of(new)
    multi = len(new_names) >= 2
    out = []
    for e in edges:
        ss = e.get("sourceSlot") or ""
        if e["source"] != node_id or not (ss == "lm" or ss.startswith("lm::")):
            out.append(e)
            continue
        model = ss[4:] if ss.startswith("lm::") else (old_names[0] if old_names else None)
        if not multi:
            out.append({**e, "sourceSlot": "lm"})
        elif model and model in new_names:
            out.append({**e, "sourceSlot": f"lm::{model}"})
    return out


def reconcile_mcp_edges(edges: List[dict], node_id: str, old: dict, new: dict) -> List[dict]:
    """Same as :func:`reconcile_model_edges` for an MCP node's server set."""
    old_names, new_names = mcp_server_names_of(old), mcp_server_names_of(new)
    multi = len(new_names) >= 2
    out = []
    for e in edges:
        ss = e.get("sourceSlot") or ""
        if e["source"] != node_id or not (ss == "tool" or ss.startswith("mcp::")):
            out.append(e)
            continue
        server = ss[5:] if ss.startswith("mcp::") else (old_names[0] if old_names else None)
        if not multi:
            out.append({**e, "sourceSlot": "tool"})
        elif server and server in new_names:
            out.append({**e, "sourceSlot": f"mcp::{server}"})
    return out


# ── lint ──────────────────────────────────────────────────────────────────
_VALID_MEMORY_TYPES = {"semantic", "pinned", "kb", "session"}


def _read_memory_types(node: dict) -> Tuple[bool, set]:
    for src in (node.get("params") or {}, node):
        for k in ("memoryTypes", "memory_types"):
            if k in src:
                parts = {p.lower() for p in split_csv(src[k])} & _VALID_MEMORY_TYPES
                return True, parts
    return False, set(_VALID_MEMORY_TYPES)


def _auto_learn_on(node: dict) -> bool:
    for k in ("autoLearn", "auto_learn"):
        v = (node.get("params") or {}).get(k, node.get(k))
        if v is not None:
            return v is True or str(v).strip().lower() == "true"
    return False


def agent_has_memory_node(agent: dict, nodes: List[dict], edges: List[dict]) -> bool:
    mem_ids = {n["id"] for n in nodes if n.get("type") in ("vector_memory", "memory")}
    return any((e["source"] == agent["id"] and e["target"] in mem_ids)
               or (e["target"] == agent["id"] and e["source"] in mem_ids) for e in edges)


def workflow_warnings(nodes: List[dict], edges: List[dict]) -> List[dict]:
    """Non-blocking lint mirroring the backend's save-time 400s."""
    out = []
    for n in nodes:
        if n.get("type") == "vector_memory":
            present, types = _read_memory_types(n)
            if present and not types:
                out.append({"nodeId": n["id"], "message":
                            "Memory node: select at least one memory type (semantic, pinned, kb, or session)."})
        if n.get("type") == "agent" and _auto_learn_on(n) and not agent_has_memory_node(n, nodes, edges):
            out.append({"nodeId": n["id"], "message":
                        "Auto-learn requires a Memory node — connect one to this agent so learned "
                        "findings persist and are recalled."})
    return out


# ── agent validity (Chat agent list) ──────────────────────────────────────
def _neighbor_types(node_id: str, nodes: List[dict], edges: List[dict]) -> List[str]:
    by_id = {n["id"]: n for n in nodes}
    out = []
    for e in edges or []:
        if e.get("source") == node_id or e.get("target") == node_id:
            other = e["target"] if e.get("source") == node_id else e.get("source")
            t = (by_id.get(other) or {}).get("type")
            if t:
                out.append(t)
    return out


def is_agent_workflow_valid(workflow: Optional[dict]) -> bool:
    """Chat-ready: an ``agent`` node wired to an LLM and at least one tool."""
    if not workflow:
        return False
    nodes, edges = workflow.get("nodes") or [], workflow.get("edges") or []
    agent = next((n for n in nodes if n.get("type") == "agent"), None)
    if not agent:
        return False
    neigh = _neighbor_types(agent["id"], nodes, edges)
    return any(t in LLM_TYPES for t in neigh) and any(t and t not in NON_TOOL_TYPES for t in neigh)


def agent_tools(workflow: dict) -> List[str]:
    nodes, edges = workflow.get("nodes") or [], workflow.get("edges") or []
    agent = next((n for n in nodes if n.get("type") == "agent"), None)
    if not agent:
        return []
    ids = {e["target"] if e["source"] == agent["id"] else e["source"]
           for e in edges if agent["id"] in (e.get("source"), e.get("target"))}
    return [(n.get("data") or {}).get("label") or n.get("name") or n["type"]
            for n in nodes if n["id"] in ids and n.get("type") not in NON_TOOL_TYPES]


def agent_model(workflow: dict) -> str:
    for n in workflow.get("nodes") or []:
        if n.get("type") in LLM_TYPES:
            p, d = n.get("params") or {}, n.get("data") or {}
            raw = p.get("llm") or p.get("model") or p.get("modelId") or d.get("model") or d.get("label") or ""
            return ", ".join(split_csv(raw)) if raw else "—"
    return "—"


def has_cloudwatch(workflow: dict) -> bool:
    return any(n.get("type") in ("cloudwatch_tool", "cloudwatchAnalyzer") for n in workflow.get("nodes") or [])


# ── subagent windows ──────────────────────────────────────────────────────
def is_tool_node_type(node_type: str) -> bool:
    return any(s["kind"] == "port-out" and s.get("portType") == "tool"
               for s in NODE_TYPES.get(node_type, {}).get("slots", []))


def window_rect(win: dict) -> Tuple[float, float, float, float]:
    p = win.get("params") or {}
    try:
        w = float(p.get("w") or DEFAULT_SUBAGENT_WIN_W)
        h = float(p.get("h") or DEFAULT_SUBAGENT_WIN_H)
    except ValueError:
        w, h = DEFAULT_SUBAGENT_WIN_W, DEFAULT_SUBAGENT_WIN_H
    return win.get("x", 0), win.get("y", 0), w, h


def window_at(nodes: List[dict], px: float, py: float) -> Optional[dict]:
    for win in nodes:
        if win.get("type") == "subagent_window":
            x, y, w, h = window_rect(win)
            if x <= px <= x + w and y <= py <= y + h:
                return win
    return None


def set_membership(nodes: List[dict], edges: List[dict], node_id: str,
                   window_id: Optional[str]) -> Tuple[List[dict], List[dict]]:
    """Assign (or clear) a tool node's subagent window. Joining strips its
    direct tool→agent edge — the window already scopes it."""
    nodes = [{**n, "parentId": window_id} if n["id"] == node_id else n for n in nodes]
    if window_id is None:
        nodes = [{k: v for k, v in n.items() if k != "parentId"} if n["id"] == node_id else n
                 for n in nodes]
    else:
        edges = [e for e in edges if not (e["source"] == node_id and e["sourceSlot"] == "tool"
                                          and e["targetSlot"] == "tools")]
        nodes = layout_window_members(nodes, window_id)
    return nodes, edges


def layout_window_members(nodes: List[dict], win_id: str, *, shrink: bool = False,
                          member_h: int = 110, pad: int = 14, header: int = 40,
                          max_col_h: int = 650) -> List[dict]:
    """Stack a window's members in columns and grow the box to enclose them."""
    win = next((n for n in nodes if n["id"] == win_id), None)
    if not win:
        return nodes
    members = [n for n in nodes if n.get("parentId") == win_id]
    if not members:
        return nodes
    x, y, w, h = window_rect(win)
    pos, col_x, cur_y, max_right, max_bottom = {}, x + pad, y + header, x, y + header
    for m in members:
        mw = NODE_TYPES.get(m["type"], {}).get("width") or NODE_W
        if cur_y > y + header and (cur_y - y) + member_h > max_col_h:
            col_x, cur_y = max_right + pad, y + header
        pos[m["id"]] = (col_x, cur_y)
        cur_y += member_h + pad
        max_right = max(max_right, col_x + mw)
        max_bottom = max(max_bottom, cur_y - pad)
    fit_w, fit_h = (max_right - x) + pad, (max_bottom - y) + pad
    new_w, new_h = (fit_w, fit_h) if shrink else (max(w, fit_w), max(h, fit_h))
    out = []
    for n in nodes:
        if n["id"] in pos:
            n = {**n, "x": pos[n["id"]][0], "y": pos[n["id"]][1]}
        elif n["id"] == win_id:
            n = {**n, "params": {**(n.get("params") or {}), "w": str(int(new_w)), "h": str(int(new_h))}}
        out.append(n)
    return out


def delete_node(nodes: List[dict], edges: List[dict], node_id: str) -> Tuple[List[dict], List[dict]]:
    deleted = next((n for n in nodes if n["id"] == node_id), None)
    remaining = []
    for n in nodes:
        if n["id"] == node_id:
            continue
        if n.get("parentId") == node_id:  # deleting a window frees its members
            n = {k: v for k, v in n.items() if k != "parentId"}
        remaining.append(n)
    if deleted and deleted.get("parentId"):
        remaining = layout_window_members(remaining, deleted["parentId"], shrink=True)
    return remaining, [e for e in edges if node_id not in (e["source"], e["target"])]


def tool_globs_for_node(node: dict) -> List[str]:
    t = node.get("type")
    if t == "cloudwatch_tool":
        return ["cloudwatch_*"]
    if t == "code_search_tool":
        return ["codegraph__*", "repo_*"]
    if t == "mcp_server":
        servers = mcp_server_names_of(node)
        return [f"{s}__*" for s in servers] if servers else ["*"]
    return ["*"]


def resolve_model_for_node(node_id: str, nodes: List[dict], edges: List[dict]) -> Optional[str]:
    edge = next((e for e in edges if e["target"] == node_id and e.get("targetSlot") == "lm"), None)
    if not edge:
        return None
    ss = edge.get("sourceSlot") or ""
    if ss.startswith("lm::"):
        return ss[4:].strip() or None
    src = next((n for n in nodes if n["id"] == edge["source"]), None) or {}
    raw = (src.get("params") or {}).get("llm") or (src.get("params") or {}).get("model")
    names = split_csv(raw)
    return names[0] if names else None


def build_exported_workflow(nodes: List[dict], edges: List[dict], enabled: bool) -> dict:
    """Derive ``agent.params.subagents`` from subagent windows at save time.

    One specialist per (window, wired model) group, each scoped to the tool
    globs of the members wired to that model — a subagent runs one model.
    """
    windows = [n for n in nodes if n.get("type") == "subagent_window"]
    if not windows:
        return {"nodes": nodes, "edges": edges, "enabled": enabled}
    defs = []
    for win in windows:
        members = [n for n in nodes if n.get("parentId") == win["id"]]
        base = ((win.get("params") or {}).get("name") or win.get("name") or "").strip()
        desc = (win.get("params") or {}).get("description") or ""
        by_model: Dict[str, List[str]] = {}
        for m in members:
            model = resolve_model_for_node(m["id"], nodes, edges) or ""
            globs = by_model.setdefault(model, [])
            for g in tool_globs_for_node(m):
                if g not in globs:
                    globs.append(g)
        if not by_model:
            defs.append({"name": base, "description": desc, "tools": ["*"]})
            continue
        split = len(by_model) > 1
        for model, globs in by_model.items():
            d = {"name": f"{base} ({model})" if split and model else base,
                 "description": desc, "tools": globs or ["*"]}
            if model:
                d["model"] = model
            defs.append(d)
    defs = [d for d in defs if d["name"]]
    agent = next((n for n in nodes if n.get("type") == "agent"), None)
    if not defs or not agent:
        return {"nodes": nodes, "edges": edges, "enabled": enabled}
    payload = json.dumps(defs)
    out = [{**n, "params": {**(n.get("params") or {}), "subagents": payload}} if n["id"] == agent["id"] else n
           for n in nodes]
    return {"nodes": out, "edges": edges, "enabled": enabled}


def clean_orphaned_edges(nodes: List[dict], edges: List[dict]) -> List[dict]:
    ids = {n["id"] for n in nodes}
    return [e for e in edges if e.get("source") in ids and e.get("target") in ids]
