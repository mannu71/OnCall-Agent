"""Harness self-test — deterministic regression coverage for app/harness/.

The accuracy harness (``evals.accuracy``) exercises the crawler/CloudWatch tools
directly but NOT ``ReactStrategy.execute`` / the harness runtime. This module
fills that blind spot: it unit-tests the harness primitives the strategy depends
on, so a regression in the agent-path logic is caught WITHOUT needing a live
Bedrock run.

Run inside the agent-api container:

    docker exec agent-api-agent-api-1 sh -c "cd /app && python -m evals.harness_selftest"

Exits non-zero on the first failure (CI-gate friendly).
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.disable(logging.CRITICAL)  # keep output to the PASS/FAIL lines

_FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    print(f"{status} {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        _FAILURES.append(name)


# ── envelopes ────────────────────────────────────────────────────────────────
def test_envelopes() -> None:
    from app.harness import ToolResult, NodeOutput, cap_text

    tr = ToolResult.ok("found 3 rows", data={"rows": [1, 2, 3]})
    rendered = tr.render()
    check("envelopes.ToolResult.render summary-first", rendered.startswith("found 3 rows"))

    capped, trunc = cap_text("x" * 50_000, 12_000, "db")
    check("envelopes.cap_text truncates", trunc and len(capped) < 13_000, f"len={len(capped)}")
    capped2, trunc2 = cap_text("short", 12_000, "db")
    check("envelopes.cap_text passthrough", (not trunc2) and capped2 == "short")

    no = NodeOutput(status="error", output="boom", error="bad").to_dict()
    check("envelopes.NodeOutput error→failed", no["status"] == "failed")
    ok = NodeOutput(status="ok", output="hi").to_dict()
    check("envelopes.NodeOutput ok passthrough", ok["status"] == "ok")


# ── tool_router ──────────────────────────────────────────────────────────────
def test_tool_router() -> None:
    import os
    from app.harness.tool_router import filter_tools

    class T:
        def __init__(self, n, d=""):
            self.name = n
            self.description = d

    os.environ["TOOL_ROUTER_TOP_K"] = "2"
    specials = [T("cloudwatch_search_logs"), T("code_find"), T("db_list_tables")]
    mcps = [T("alpha_query", "alpha"), T("beta_thing", "beta"), T("gamma_io", "gamma")]
    out = filter_tools(specials + mcps, query="alpha")
    names = [t.name for t in out]
    check("tool_router keeps special prefixes", all(s.name in names for s in specials))
    kept_mcp = [n for n in names if n in {"alpha_query", "beta_thing", "gamma_io"}]
    check("tool_router caps MCP at top_k", len(kept_mcp) <= 2, f"kept={kept_mcp}")
    check("tool_router passthrough when no MCP", filter_tools(specials, "x") == specials)


# ── spec_factory + harness.build_agent ───────────────────────────────────────
def test_spec_and_facade() -> None:
    from app.harness import harness, AgentSpec
    from app.harness.spec_factory import build_agent_spec, resolve_permission_mode

    check("spec_factory context overrides node",
          resolve_permission_mode({"permission_mode": "plan"}, {"permissionMode": "default"}) == "plan")
    spec = build_agent_spec(agent_config={"permissionMode": "AUTO_ALLOW"}, context={},
                            has_cloudwatch=True, has_code_analyzer=False, session_id="x")
    check("spec_factory builds spec", spec.permission_mode == "auto_allow" and spec.has_cloudwatch and spec.session_id == "x")

    import app.workflow.strategies.react.agent_builder as ab
    captured: dict = {}

    def fake_build_agent(llm, tools, agent_config, **kw):
        captured.update({"llm": llm, "tools": tools, "agent_config": agent_config, **kw})
        return "AGENT"

    orig = ab.build_agent
    ab.build_agent = fake_build_agent
    try:
        s = AgentSpec(agent_config={"x": 1}, has_cloudwatch=True, has_code_analyzer=False,
                      permission_mode="auto_allow", session_id="exec-9")
        out = harness.build_agent(s, llm="LLM", tools=["t1"], checkpointer="CP", execution_port="PORT")
        ok = (out == "AGENT" and captured["has_cloudwatch"] and not captured["has_code_analyzer"]
              and captured["permission_mode"] == "auto_allow" and captured["session_id"] == "exec-9"
              and captured["checkpointer"] == "CP" and captured["execution_port"] == "PORT"
              and captured["llm"] == "LLM" and captured["tools"] == ["t1"])
        check("harness.build_agent maps AgentSpec→kwargs", ok, str(captured))
    finally:
        ab.build_agent = orig


# ── supervisor_loop ──────────────────────────────────────────────────────────
async def test_supervisor_loop() -> None:
    from app.harness.supervisor_loop import run_supervised
    from app.core.supervisor import SupervisorAction
    log = logging.getLogger("t")

    class V:
        def __init__(self, a):
            self.action = a; self.score = 0.5; self.reason = "r"; self.retry_guidance = "fix"

    class Cfg:
        max_retries = 2; token_budget = 100_000

    class FakeSup:
        def __init__(self, v):
            self._v = v; self._i = 0; self._cfg = Cfg()

        def evaluate(self, **k):
            x = self._v[min(self._i, len(self._v) - 1)]; self._i += 1; return x

    # PASS first
    runs = {"n": 0}
    async def ra(a, q):
        runs["n"] += 1; return {"final_answer": "done", "input_tokens": 10, "output_tokens": 5, "tool_calls": []}
    res, ti, to = await run_supervised(agent="a0", run_agent=ra, rebuild_agent=lambda: "a",
        supervisor=FakeSup([V(SupervisorAction.PASS)]), base_query="Q", execution_id="e",
        logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop PASS = 1 run", runs["n"] == 1 and ti == 10 and to == 5)

    # RETRY then PASS
    runs = {"n": 0}; rebuilds = {"n": 0}
    async def ra2(a, q):
        runs["n"] += 1; return {"final_answer": "x", "input_tokens": 7, "output_tokens": 3, "tool_calls": []}
    res, ti, to = await run_supervised(agent="a0", run_agent=ra2, rebuild_agent=lambda: (rebuilds.__setitem__("n", rebuilds["n"] + 1) or "a2"),
        supervisor=FakeSup([V(SupervisorAction.RETRY), V(SupervisorAction.PASS)]), base_query="Q",
        execution_id="e", logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop RETRY→PASS rebuilds once", runs["n"] == 2 and rebuilds["n"] == 1 and ti == 14 and to == 6)

    # ESCALATE sets flag
    res, _, _ = await run_supervised(agent="a0",
        run_agent=lambda a, q: _coro({"final_answer": "weak", "input_tokens": 1, "output_tokens": 1, "tool_calls": []}),
        rebuild_agent=lambda: "a", supervisor=FakeSup([V(SupervisorAction.ESCALATE)]), base_query="Q",
        execution_id="e", logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop ESCALATE flag", res.get("supervisor_escalated") is True)

    # no supervisor = 1 run
    runs = {"n": 0}
    async def ra3(a, q):
        runs["n"] += 1; return {"final_answer": "y", "input_tokens": 1, "output_tokens": 1, "tool_calls": []}
    await run_supervised(agent="a0", run_agent=ra3, rebuild_agent=lambda: "a", supervisor=None,
        base_query="Q", execution_id="e", logger_instance=log, wall_clock_budget=900)
    check("supervisor_loop no-supervisor = 1 run", runs["n"] == 1)


async def _coro(v):
    return v


# ── context_builder ──────────────────────────────────────────────────────────
def test_context_builder() -> None:
    from app.harness.context_builder import seed_context_blocks, apply_synthesis_floor
    log = logging.getLogger("t")

    aq, cw = seed_context_blocks(augmented_query="Q",
        context={"cloudwatch_context": {"n": {"output": "SYNTH " * 10}}})
    check("context_builder.seed extracts cw_synthesis", len(cw) > 0 and aq != "Q")

    r = apply_synthesis_floor(result={"final_answer": ""}, cw_synthesis="FLOOR", logger_instance=log, execution_id="e")
    check("context_builder.floor fires on empty", r["final_answer"] == "FLOOR" and r["cloudwatch_synthesis_fallback"])

    good = "x" * 600
    r2 = apply_synthesis_floor(result={"final_answer": good}, cw_synthesis="FLOOR", logger_instance=log, execution_id="e")
    check("context_builder.floor keeps long answer", r2["final_answer"] == good)


# ── tool_assembler.add_extension_tools ───────────────────────────────────────
def test_extension_tools() -> None:
    from app.harness.tool_assembler import add_extension_tools
    log = logging.getLogger("t")
    # no code_analyzer_config → no extensions
    out = add_extension_tools(tools=["base"], llm="LLM", agent_config={}, code_analyzer_config=None,
                              execution_id="e", logger_instance=log)
    check("add_extension_tools noop without code config", out == ["base"])
    # with code config → appends delegate (+ edit, best-effort)
    out2 = add_extension_tools(tools=[], llm="LLM", agent_config={"instructions": "x"},
                               code_analyzer_config={"repos": []}, execution_id="e", logger_instance=log)
    names = [getattr(t, "name", "") for t in out2]
    check("add_extension_tools adds delegate", "delegate_investigation" in names, str(names))


# ── edit_tools.create_file + permission classification ───────────────────────
async def test_edit_tools() -> None:
    import json as _json
    import os
    import tempfile
    from app.config import settings
    from app.workflow.strategies.react.edit_tools import build_edit_tools
    from app.workflow.strategies.react.tool_permissions import evaluate

    tools = {t.name: t for t in build_edit_tools()}
    check("edit_tools exposes create_file", "create_file" in tools, str(list(tools)))

    # Permission classification: create_file must be gated 'ask' by default, and
    # 'allow' under auto_allow. (The "*_create" suffix glob does NOT match it, so
    # the explicit entry is what enforces this — guard against its removal.)
    check("create_file gated ask", evaluate("create_file") == "ask")
    check("create_file auto_allow→allow", evaluate("create_file", mode="auto_allow") == "allow")

    root = tempfile.mkdtemp(prefix="harness_edit_")
    _orig_root = settings.repos_base_path
    settings.repos_base_path = root
    try:
        create = tools["create_file"].coroutine
        # happy path — new file written to disk
        res = _json.loads(await create(repo="r", file="pkg/new_mod.py", content="X = 1\n"))
        on_disk = os.path.isfile(os.path.join(root, "r", "pkg", "new_mod.py"))
        check("create_file happy path", res.get("ok") and on_disk, str(res))
        # refuse to overwrite an existing file
        res2 = _json.loads(await create(repo="r", file="pkg/new_mod.py", content="Y = 2\n"))
        check("create_file refuses overwrite", "error" in res2 and "exists" in res2["error"], str(res2))
        # path-jail escape is rejected (file not written outside the repo root)
        res3 = _json.loads(await create(repo="r", file="../../../../etc/pwn", content="bad"))
        check("create_file blocks jail escape", "error" in res3, str(res3))
    finally:
        settings.repos_base_path = _orig_root


# ── llm_config node-level model gateway ───────────────────────────────────────
async def test_gateway_resolution() -> None:
    """Deterministic coverage for the node-level model gateway in
    ``app.workflow.llm_config``: edge-dialect coalescing, multi-model slot
    selection, and per-port wiring rules. Pure helpers need no DB; the
    ``resolve_llm_config_for_consumer_port`` cases hit the DB only for named
    config lookups (no live Bedrock)."""
    from app.workflow.llm_config import (
        resolve_llm_config_for_consumer_port,
        find_llm_node_for_consumer,
        read_llm_node,
        _edge_target_slot,
        _edge_source_slot,
        _model_key_from_slot,
    )
    from app.infrastructure.persistence import llm_config_repository

    # ── pure helpers (no DB) ──────────────────────────────────────────────────
    check("gateway._model_key_from_slot('lm::Foo')=='Foo'",
          _model_key_from_slot("lm::Foo") == "Foo")
    check("gateway._model_key_from_slot('lm') is None",
          _model_key_from_slot("lm") is None)
    check("gateway._edge_target_slot editor dialect (targetSlot)",
          _edge_target_slot({"targetSlot": "lm"}) == "lm")
    check("gateway._edge_target_slot legacy dialect (targetHandle)",
          _edge_target_slot({"targetHandle": "model"}) == "model")
    check("gateway._edge_source_slot editor dialect (sourceSlot)",
          _edge_source_slot({"sourceSlot": "lm::A"}) == "lm::A")
    check("gateway._edge_source_slot legacy dialect (sourceHandle)",
          _edge_source_slot({"sourceHandle": "lm"}) == "lm")

    # ── fetch real config names dynamically (container DB has 2) ───────────────
    db = await llm_config_repository.list_all()
    names = list(db.keys())
    if len(names) < 2:
        check("gateway: >=2 LLM configs in DB (skipped DB cases)", True,
              f"only {len(names)} config(s) — skipping DB-backed gateway cases")
        return
    A, B = names[0], names[1]
    model_A, model_B = db[A]["model"], db[B]["model"]

    # ── Case 5: pure read_llm_node model selection (no DB) ─────────────────────
    multi_node = {"id": "lm1", "type": "language_model", "params": {"llm": f"{A},{B}"}}
    check("gateway.read_llm_node multi: model_key picks named config",
          read_llm_node(multi_node, model_key=B).config_name == B,
          read_llm_node(multi_node, model_key=B).config_name)
    check("gateway.read_llm_node multi: no model_key picks first",
          read_llm_node(multi_node, model_key=None).config_name == A,
          read_llm_node(multi_node, model_key=None).config_name)

    # ── Case 1: multi-model node, editor dialect, slot selects the model ───────
    wf_multi = {
        "nodes": [
            {"id": "lm1", "type": "language_model", "params": {"llm": f"{A},{B}"}},
            {"id": "agent", "type": "agent"},
            {"id": "code_search_tool", "type": "code_search_tool"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent",
             "sourceSlot": f"lm::{A}", "targetSlot": "lm"},
            {"source": "lm1", "target": "code_search_tool",
             "sourceSlot": f"lm::{B}", "targetSlot": "lm"},
        ],
    }
    res_a = await resolve_llm_config_for_consumer_port(wf_multi, "agent", "lm")
    check("gateway lm::A→agent.lm resolves model A (editor)",
          res_a is not None and res_a.get("model") == model_A, str(res_a))
    res_b = await resolve_llm_config_for_consumer_port(wf_multi, "code_search_tool", "lm")
    check("gateway lm::B→code_search_tool.lm resolves model B (editor)",
          res_b is not None and res_b.get("model") == model_B, str(res_b))

    # find_llm_node_for_consumer surfaces the matched source slot
    node, slot, connected = find_llm_node_for_consumer(
        wf_multi, "agent", ("model", "lm"), accept_untagged=True, fallback=False)
    check("gateway find_llm_node_for_consumer returns (node, slot, connected)",
          connected and slot == f"lm::{A}" and node.get("id") == "lm1",
          f"connected={connected} slot={slot}")

    # ── Case 2: legacy dialect, single model, resolves ─────────────────────────
    wf_legacy = {
        "nodes": [
            {"id": "lm1", "type": "llm", "params": {"llm": A}},
            {"id": "agent", "type": "agent"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent",
             "sourceHandle": "lm", "targetHandle": "model"},
        ],
    }
    res_legacy = await resolve_llm_config_for_consumer_port(wf_legacy, "agent", "lm")
    check("gateway legacy sourceHandle/targetHandle resolves model A",
          res_legacy is not None and res_legacy.get("model") == model_A, str(res_legacy))

    # ── Case 3: unwired port → None ────────────────────────────────────────────
    res_sub = await resolve_llm_config_for_consumer_port(wf_multi, "agent", "subagent")
    check("gateway unwired subagent port → None", res_sub is None, str(res_sub))

    # ── crawler/subagent require an exact slot — no untagged bleed ─────────────
    wf_untagged = {
        "nodes": [
            {"id": "lm1", "type": "language_model", "params": {"llm": A}},
            {"id": "agent", "type": "agent"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent"},  # untagged single edge
        ],
    }
    res_u_lm = await resolve_llm_config_for_consumer_port(wf_untagged, "agent", "lm")
    check("gateway lm port accepts untagged legacy edge",
          res_u_lm is not None and res_u_lm.get("model") == model_A, str(res_u_lm))
    res_u_cr = await resolve_llm_config_for_consumer_port(wf_untagged, "agent", "crawler")
    check("gateway crawler port rejects untagged edge → None",
          res_u_cr is None, str(res_u_cr))

    # crawler with an exact slot resolves the slot-named model
    wf_crawler = {
        "nodes": [
            {"id": "lm1", "type": "language_model", "params": {"llm": f"{A},{B}"}},
            {"id": "agent", "type": "agent"},
        ],
        "edges": [
            {"source": "lm1", "target": "agent",
             "sourceSlot": f"lm::{B}", "targetSlot": "crawler"},
        ],
    }
    res_crawler = await resolve_llm_config_for_consumer_port(wf_crawler, "agent", "crawler")
    check("gateway crawler exact slot resolves slot-named model B",
          res_crawler is not None and res_crawler.get("model") == model_B, str(res_crawler))


async def _main() -> int:
    print("=== Agent Harness self-test ===")
    test_envelopes()
    test_tool_router()
    test_spec_and_facade()
    await test_supervisor_loop()
    test_context_builder()
    test_extension_tools()
    await test_edit_tools()
    await test_gateway_resolution()
    print("-" * 40)
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} check(s): {', '.join(_FAILURES)}")
        return 1
    print("ALL HARNESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
