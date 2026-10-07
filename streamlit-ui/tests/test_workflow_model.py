import json

from oncall_ui import workflow_model as wm


def _node(nid, t, **params):
    return {"id": nid, "type": t, "x": 0, "y": 0, "name": nid, "params": params}


def test_catalog_and_templates_load():
    assert "agent" in wm.NODE_TYPES and "language_model" in wm.NODE_TYPES
    assert {t["id"] for t in wm.WORKFLOW_TEMPLATES} >= {"rca-analyzer"}
    pal = wm.palette()
    assert "postgres_tool" not in [t for items in pal.values() for t, _ in items]  # paletteHidden


def test_make_node_applies_defaults_and_global_tz():
    n = wm.make_node("schedule", global_tz="Asia/Kolkata")
    assert n["params"]["tz"] == "Asia/Kolkata"
    assert n["params"]["frequency"] == "Every 15 min"
    a = wm.make_node("agent")
    assert a["params"]["maxIter"] == "10"


def test_multi_model_expands_ports():
    lm = _node("lm", "language_model", llm="A, B")
    outs = [s["id"] for s in wm.slots_for_node(lm) if s["kind"] == "port-out"]
    assert outs == ["lm::A", "lm::B"]
    single = _node("lm1", "language_model", llm="A")
    assert [s["id"] for s in wm.slots_for_node(single) if s["kind"] == "port-out"] == ["lm"]


def test_validate_connection_rules():
    lm, agent, cw = _node("lm", "language_model", llm="A"), _node("ag", "agent"), _node("cw", "cloudwatch_tool")
    nodes = [lm, agent, cw]
    assert wm.validate_connection("lm", "lm", "ag", "lm", nodes, []) == (True, "")
    assert wm.validate_connection("ag", "lm", "ag", "lm", nodes, [])[1] == wm.REJECT["SELF"]
    assert wm.validate_connection("lm", "lm", "ag", "tools", nodes, [])[1] == wm.REJECT["PORT_TYPE"]
    edges = [{"id": "e1", "source": "lm", "sourceSlot": "lm", "target": "ag", "targetSlot": "lm"}]
    assert wm.validate_connection("lm", "lm", "ag", "lm", nodes, edges)[1] == wm.REJECT["DUPLICATE"]
    lm2 = _node("lm2", "language_model", llm="B")
    assert wm.validate_connection("lm2", "lm", "ag", "lm", nodes + [lm2], edges)[1] == wm.REJECT["SLOT_FULL"]
    # multi slot accepts many
    cw2 = _node("cw2", "cloudwatch_tool")
    e2 = edges + [{"id": "e2", "source": "cw", "sourceSlot": "tool", "target": "ag", "targetSlot": "tools"}]
    assert wm.validate_connection("cw2", "tool", "ag", "tools", nodes + [cw2], e2)[0]


def test_infer_connection_prefers_required_input():
    lm, cw, agent = _node("lm", "language_model", llm="A"), _node("cw", "cloudwatch_tool"), _node("ag", "agent")
    nodes = [lm, cw, agent]
    assert wm.infer_connection(cw, agent, nodes, []) == ("tool", "tools")
    assert wm.infer_connection(lm, agent, nodes, []) == ("lm", "lm")
    # lm → cloudwatch only has the optional model input
    assert wm.infer_connection(lm, cw, nodes, []) == ("lm", "lm")
    assert wm.infer_connection(agent, cw, nodes, []) is None


def test_reconcile_model_edges():
    old = _node("lm", "language_model", llm="A")
    new = _node("lm", "language_model", llm="A,B")
    edges = [{"id": "e", "source": "lm", "sourceSlot": "lm", "target": "ag", "targetSlot": "lm"}]
    assert wm.reconcile_model_edges(edges, "lm", old, new)[0]["sourceSlot"] == "lm::A"
    newer = _node("lm", "language_model", llm="B,C")
    multi = [{**edges[0], "sourceSlot": "lm::A"}]
    assert wm.reconcile_model_edges(multi, "lm", new, newer) == []
    back = wm.reconcile_model_edges([{**edges[0], "sourceSlot": "lm::B"}], "lm", newer, _node("lm", "language_model", llm="B"))
    assert back[0]["sourceSlot"] == "lm"


def test_reconcile_mcp_edges():
    old = _node("m", "mcp_server", servers="pg")
    new = _node("m", "mcp_server", servers="pg,ado")
    edges = [{"id": "e", "source": "m", "sourceSlot": "tool", "target": "ag", "targetSlot": "tools"}]
    assert wm.reconcile_mcp_edges(edges, "m", old, new)[0]["sourceSlot"] == "mcp::pg"


def test_warnings_auto_learn_and_memory():
    agent = _node("ag", "agent", autoLearn="true")
    mem = _node("mem", "vector_memory", memoryTypes="")
    w = wm.workflow_warnings([agent, mem], [])
    msgs = " ".join(x["message"] for x in w)
    assert "Auto-learn requires a Memory node" in msgs and "select at least one memory type" in msgs
    mem_ok = _node("mem", "vector_memory", memoryTypes="semantic")
    edges = [{"id": "e", "source": "mem", "sourceSlot": "mem", "target": "ag", "targetSlot": "memory"}]
    assert wm.workflow_warnings([agent, mem_ok], edges) == []


def test_is_agent_workflow_valid():
    nodes = [_node("lm", "language_model"), _node("ag", "agent"), _node("cw", "cloudwatch_tool"),
             _node("s", "schedule")]
    edges = [{"source": "lm", "target": "ag"}, {"source": "cw", "target": "ag"}]
    assert wm.is_agent_workflow_valid({"nodes": nodes, "edges": edges})
    assert not wm.is_agent_workflow_valid({"nodes": nodes, "edges": edges[:1]})
    assert wm.agent_tools({"nodes": nodes, "edges": edges}) == ["cw"]


def test_normalize_legacy_node_and_edge():
    old = {"id": "a", "type": "agent", "position": {"x": 5, "y": 6},
           "data": {"label": "Bot", "instructions": "hi", "maxIterations": 7}}
    n = wm.normalize_node(old)
    assert (n["x"], n["y"], n["name"]) == (5, 6, "Bot")
    assert n["params"] == {"system": "hi", "maxIter": "7"}
    e = wm.normalize_edge({"source": "a", "target": "b", "sourceHandle": "llm-output", "targetHandle": "model"})
    assert (e["sourceSlot"], e["targetSlot"]) == ("lm", "lm")
    new = {"id": "b", "type": "agent", "x": 1, "y": 2, "params": {"a": "1"}}
    assert wm.normalize_node(new)["params"] == {"a": "1"}


def test_subagent_membership_and_export():
    lm = _node("lm", "language_model", llm="Sonnet,Haiku")
    agent = _node("ag", "agent")
    win = {"id": "w", "type": "subagent_window", "x": 0, "y": 0, "name": "w",
           "params": {"name": "code-db", "description": "d", "w": "300", "h": "360"}}
    cw = _node("cw", "cloudwatch_tool")
    code = _node("code", "code_search_tool")
    nodes = [lm, agent, win, cw, code]
    edges = [
        {"id": "e1", "source": "cw", "sourceSlot": "tool", "target": "ag", "targetSlot": "tools"},
        {"id": "e2", "source": "lm", "sourceSlot": "lm::Sonnet", "target": "cw", "targetSlot": "lm"},
        {"id": "e3", "source": "lm", "sourceSlot": "lm::Haiku", "target": "code", "targetSlot": "lm"},
    ]
    nodes, edges = wm.set_membership(nodes, edges, "cw", "w")
    nodes, edges = wm.set_membership(nodes, edges, "code", "w")
    assert all(e["id"] != "e1" for e in edges)  # tool→agent edge stripped on join
    member = next(n for n in nodes if n["id"] == "cw")
    assert member["parentId"] == "w"
    assert all(s["id"] != "tool" for s in wm.visible_slots(member))
    out = wm.build_exported_workflow(nodes, edges, True)
    ag = next(n for n in out["nodes"] if n["id"] == "ag")
    defs = json.loads(ag["params"]["subagents"])
    assert {d["name"] for d in defs} == {"code-db (Sonnet)", "code-db (Haiku)"}
    assert next(d for d in defs if d["model"] == "Haiku")["tools"] == ["codegraph__*", "repo_*"]
    # leaving the window clears parentId
    nodes2, _ = wm.set_membership(nodes, edges, "cw", None)
    assert "parentId" not in next(n for n in nodes2 if n["id"] == "cw")


def test_window_layout_grows_and_delete_frees_members():
    win = {"id": "w", "type": "subagent_window", "x": 100, "y": 100, "params": {"w": "100", "h": "100"}}
    a = {**_node("a", "cloudwatch_tool"), "parentId": "w"}
    b = {**_node("b", "mcp_server"), "parentId": "w"}
    nodes = wm.layout_window_members([win, a, b], "w")
    w = next(n for n in nodes if n["id"] == "w")
    x, y, ww, hh = wm.window_rect(w)
    for m in (n for n in nodes if n.get("parentId") == "w"):
        assert x <= m["x"] <= x + ww and y <= m["y"] <= y + hh
    remaining, _ = wm.delete_node(nodes, [], "w")
    assert all("parentId" not in n for n in remaining)


def test_window_at():
    win = {"id": "w", "type": "subagent_window", "x": 0, "y": 0, "params": {"w": "300", "h": "360"}}
    assert wm.window_at([win], 150, 150)["id"] == "w"
    assert wm.window_at([win], 400, 150) is None
