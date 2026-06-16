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


# ── cloudwatch instrumentation (Phase 0) ─────────────────────────────────────
async def test_cloudwatch_cache_stats() -> None:
    """Cache hit/miss counters and reset behave deterministically."""
    from app.core import cloudwatch_cache as cc

    cc.invalidate()          # clear store so keys are fresh
    cc.reset_stats()
    calls = {"n": 0}

    async def _fn(**kw):
        calls["n"] += 1
        return kw.get("v")

    # First call: miss (fn runs). Second identical call: hit (fn does NOT run).
    r1 = await cc.cached_call("selftest_fn", _fn, ttl_seconds=60, v=7)
    r2 = await cc.cached_call("selftest_fn", _fn, ttl_seconds=60, v=7)
    st = cc.stats()
    check("cw_cache hit/miss counted", st["hits"] == 1 and st["misses"] == 1 and calls["n"] == 1, str(st))
    check("cw_cache returns cached value", r1 == 7 and r2 == 7)
    check("cw_cache hit_rate", abs(st["hit_rate"] - 0.5) < 1e-9, str(st["hit_rate"]))
    cc.reset_stats()
    check("cw_cache reset_stats zeroes", cc.stats()["hits"] == 0 and cc.stats()["misses"] == 0)
    # ttl_seconds=0 bypasses the cache entirely (always calls fn, no counters)
    calls["n"] = 0
    await cc.cached_call("selftest_fn", _fn, ttl_seconds=0, v=1)
    await cc.cached_call("selftest_fn", _fn, ttl_seconds=0, v=1)
    check("cw_cache ttl=0 bypass", calls["n"] == 2 and cc.stats()["hits"] == 0)


def test_cloudwatch_error_classifier() -> None:
    """_classify_aws_error maps AWS exceptions to the coarse retry categories."""
    from botocore.exceptions import ClientError
    from app.services.log_watch_service import _classify_aws_error

    def _ce(code: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

    cases = {
        "ThrottlingException": "throttling",
        "AccessDeniedException": "access-denied",
        "ValidationException": "validation",
        "ExpiredTokenException": "expired-credentials",
    }
    for code, expected in cases.items():
        got = _classify_aws_error(_ce(code))
        check(f"cw_classify {code}→{expected}", got == expected, f"got {got}")
    check("cw_classify timeout (non-ClientError)",
          _classify_aws_error(TimeoutError("operation timed out")) == "timeout")
    check("cw_classify generic→error",
          _classify_aws_error(RuntimeError("boom")) == "error")


def test_cloudwatch_synthesis_payload_excludes_run_stats() -> None:
    """run_stats must never leak into the LLM synthesis payload (token guard)."""
    from app.workflow.executor.cloudwatch_analysis import build_synthesis_payload

    payload = build_synthesis_payload({
        "data_quality": {"coverage": "full"},
        "run_stats": {"insights": {"bytes_scanned": 9_999_999}},
        "patterns": {"unique_patterns": []},
    })
    check("cw_payload omits run_stats", "run_stats" not in payload, str(list(payload)))
    check("cw_payload keeps data_quality", payload.get("data_quality") == {"coverage": "full"})


# ── cloudwatch reliability (Phase 1) ─────────────────────────────────────────
def test_cloudwatch_severity_clamp() -> None:
    """Weak evidence caps synthesized severity at 'medium' — including the new
    reduced-scope/partial flag branch — unless a strong signal survives."""
    from app.workflow.executor.cloudwatch_analysis import _clamp_severity_for_weak_evidence

    class P:
        def __init__(self, sev):
            self.severity = sev

    # coverage 'full' but partial flag set (reduced-scope retry) → clamp high→medium
    p = P("high")
    _clamp_severity_for_weak_evidence(p, {"data_quality": {"coverage": "full", "partial": True}})
    check("cw_clamp partial_flag caps high→medium", p.severity == "medium", p.severity)

    # reduced_scope flag alone (coverage full) also clamps
    p2 = P("critical")
    _clamp_severity_for_weak_evidence(p2, {"data_quality": {"coverage": "full", "reduced_scope": True}})
    check("cw_clamp reduced_scope caps critical→medium", p2.severity == "medium", p2.severity)

    # strong signal (alarm in ALARM) survives weak coverage — NOT clamped
    p3 = P("high")
    _clamp_severity_for_weak_evidence(p3, {
        "data_quality": {"coverage": "partial", "partial": True},
        "alarms": {"summary": {"in_alarm": 1}},
    })
    check("cw_clamp alarm-firing bypasses clamp", p3.severity == "high", p3.severity)

    # full coverage, strong evidence → untouched
    p4 = P("critical")
    _clamp_severity_for_weak_evidence(p4, {"data_quality": {"coverage": "full"}, "patterns": {"evidence_grade": "high"}})
    check("cw_clamp strong evidence untouched", p4.severity == "critical", p4.severity)


async def test_cloudwatch_analyzer_cache() -> None:
    """run_workflow_analysis caches read-only triage types and bypasses
    unique/side-effecting ones (Phase 1 per-analyzer TTLs)."""
    from app.core import cloudwatch_cache as cc
    from app.services.log_watch_service import LogWatchService

    svc = LogWatchService()
    calls: dict = {}

    async def _fake_dispatch(*, analysis_type, **kw):
        calls[analysis_type] = calls.get(analysis_type, 0) + 1
        return {"status": "success", "analysis_type": analysis_type}

    svc._dispatch_analysis = _fake_dispatch  # type: ignore[assignment]
    cc.invalidate()

    common = dict(log_group_names=["/a", "/b"], time_range_minutes=60,
                  region="us-east-1", credentials=None, node_data={})
    # alarms: ttl>0 → second identical call served from cache (dispatch once)
    await svc.run_workflow_analysis(analysis_type="alarms", **common)
    await svc.run_workflow_analysis(analysis_type="alarms", **common)
    check("cw_cache alarms cached (1 dispatch)", calls.get("alarms") == 1, str(calls))
    # correlation: ttl=0 → never cached (dispatch twice)
    await svc.run_workflow_analysis(analysis_type="correlation", **common)
    await svc.run_workflow_analysis(analysis_type="correlation", **common)
    check("cw_cache correlation not cached (2 dispatch)", calls.get("correlation") == 2, str(calls))
    # different time window → distinct cache key (fresh dispatch)
    await svc.run_workflow_analysis(analysis_type="alarms", **{**common, "time_range_minutes": 120})
    check("cw_cache distinct window = cache miss", calls.get("alarms") == 2, str(calls))
    cc.invalidate()


# ── cloudwatch accuracy (Phase 2) ────────────────────────────────────────────
def test_cloudwatch_drilldown_scoring() -> None:
    """Deterministic drill-down scoring ranks the strongest lead first and is
    stable across calls."""
    from app.workflow.tools.cloudwatch_drilldown import select_drill_targets, score_drill_target

    patterns = {"unique_patterns": [
        {"normalized_pattern": "low-volume warning", "occurrence_count": 3, "example_message": "x"},
        {"normalized_pattern": "huge error burst", "occurrence_count": 5000, "severity": "high", "example_message": "y"},
    ], "evidence_grade": "high"}
    anomalies = {"anomalies": [
        {"log_group": "/a", "z_score": 1.2, "severity": "low"},
    ]}
    t1 = select_drill_targets(patterns, anomalies, top_n=3)
    t2 = select_drill_targets(patterns, anomalies, top_n=3)
    check("cw_score deterministic", [x.get("label") for x in t1] == [x.get("label") for x in t2])
    check("cw_score high-volume critical pattern ranks first",
          t1 and "huge error burst" in (t1[0].get("label") or ""), str(t1[0] if t1 else None))
    # a critical high-z anomaly must outscore a marginal low pattern
    s_big = score_drill_target({"kind": "anomaly", "severity": "critical", "z_score": 10}, 1.0)
    s_small = score_drill_target({"kind": "pattern", "severity": "low", "occurrence_count": 2}, 1.0)
    check("cw_score critical anomaly > marginal pattern", s_big > s_small, f"{s_big} vs {s_small}")


def test_cloudwatch_query_fixup() -> None:
    """fixup_insights_query injects a missing limit and hard-fails broken queries."""
    from app.workflow.tools.cloudwatch_drilldown import fixup_insights_query

    fixed, err = fixup_insights_query("fields @timestamp, @message | filter @message like /(?i)error/")
    check("cw_fixup injects limit", err is None and "limit" in fixed.lower(), f"{err} :: {fixed}")
    fixed2, err2 = fixup_insights_query("fields @timestamp | filter x | limit 5")
    check("cw_fixup leaves valid query unchanged", err2 is None and fixed2.strip().endswith("limit 5"))
    _f3, err3 = fixup_insights_query("SELECT * FROM logs")
    check("cw_fixup rejects non-Insights lead command", err3 is not None, str(err3))
    _f4, err4 = fixup_insights_query("")
    check("cw_fixup rejects empty", err4 is not None)


def test_cloudwatch_alarm_and_metric_helpers() -> None:
    """Pure helpers: flapping detection and log-group→namespace inference."""
    from app.mcp.tools.metrics_tools import detect_flapping, infer_namespace_from_log_group

    check("cw_flapping at threshold", detect_flapping(3) is True)
    check("cw_flapping below threshold", detect_flapping(2) is False)

    lam = infer_namespace_from_log_group("/aws/lambda/kyc-auth")
    check("cw_infer lambda namespace",
          lam == {"namespace": "AWS/Lambda", "dimension_name": "FunctionName",
                  "dimension_value": "kyc-auth", "log_group": "/aws/lambda/kyc-auth"}, str(lam))
    rds = infer_namespace_from_log_group("/aws/rds/instance/kyc-db/error")
    check("cw_infer rds namespace", rds and rds["namespace"] == "AWS/RDS", str(rds))
    check("cw_infer unknown → None", infer_namespace_from_log_group("/custom/app/foo") is None)


def test_cloudwatch_payload_includes_metrics() -> None:
    """build_synthesis_payload surfaces metric summaries (Phase 2 fusion)."""
    from app.workflow.executor.cloudwatch_analysis import build_synthesis_payload

    payload = build_synthesis_payload({
        "metrics": {"metrics": {"t": {"label": "Throttles", "summary": {"maximum": 95}}}},
        "patterns": {"unique_patterns": []},
    })
    check("cw_payload includes metrics", "metrics" in payload and payload["metrics"][0]["label"] == "Throttles", str(payload.get("metrics")))


# ── cloudwatch cost controls (Phase 3) ───────────────────────────────────────
def test_cloudwatch_cost_footer() -> None:
    """format_cost_footer reports GB/USD and the budget note, and is empty when
    nothing was scanned."""
    from app.workflow.executor.cloudwatch_analysis import format_cost_footer

    check("cw_footer empty with no scans", format_cost_footer({"insights": {"queries": 0, "bytes_scanned": 0}}) == "")
    f = format_cost_footer({"insights": {"queries": 2, "bytes_scanned": 2e9}})
    check("cw_footer reports GB + queries", "2.000 GB" in f and "2 Insights queries" in f, f)
    fb = format_cost_footer({"insights": {"queries": 1, "bytes_scanned": 1e9}, "budget_limited": True})
    check("cw_footer notes budget limit", "scan budget reached" in fb, fb)


def test_cloudwatch_drill_window() -> None:
    """compute_drill_window narrows to first/last-seen, clamped to the triage window."""
    from datetime import datetime, timezone, timedelta
    from app.workflow.tools.cloudwatch_drilldown import compute_drill_window

    end = datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=6)
    tgt = {"first_seen": "2026-06-10T14:00:00Z", "last_seen": "2026-06-10T14:30:00Z"}
    lo, hi = compute_drill_window(tgt, start, end, margin_minutes=5)
    check("cw_drill_window narrows to pattern span", lo > start and hi < end, f"{lo}..{hi}")
    check("cw_drill_window margins applied",
          lo == datetime(2026, 6, 10, 13, 55, tzinfo=timezone.utc)
          and hi == datetime(2026, 6, 10, 14, 35, tzinfo=timezone.utc), f"{lo}..{hi}")
    # no timestamps → full window
    lo2, hi2 = compute_drill_window({}, start, end)
    check("cw_drill_window fallback to full window", lo2 == start and hi2 == end)
    # never widens beyond triage window
    wide = {"first_seen": "2020-01-01T00:00:00Z", "last_seen": "2030-01-01T00:00:00Z"}
    lo3, hi3 = compute_drill_window(wide, start, end)
    check("cw_drill_window never widens", lo3 >= start and hi3 <= end, f"{lo3}..{hi3}")


async def test_cloudwatch_rate_limiter() -> None:
    """Token bucket grants a burst up to rps immediately, then is gated by refill;
    rps<=0 disables it. Uses a fake clock so no real time passes."""
    from app.core import cloudwatch_ratelimit as rl

    clock = {"t": 0.0}
    bucket = rl.TokenBucket(rps=5, time_fn=lambda: clock["t"])
    waits = [await bucket.acquire() for _ in range(5)]
    check("cw_ratelimit burst up to rps no wait", all(w == 0.0 for w in waits), str(waits))
    # advance fake clock 1s → full refill → next 5 also immediate
    clock["t"] = 1.0
    waits2 = [await bucket.acquire() for _ in range(5)]
    check("cw_ratelimit refills over time", all(w == 0.0 for w in waits2), str(waits2))
    check("cw_ratelimit disabled when rps<=0", await rl.acquire("r", "StartQuery", 0) == 0.0)
    rl.reset()


# ── cloudwatch feature breadth (Phase 4) ─────────────────────────────────────
def test_cloudwatch_time_buckets() -> None:
    """compute_time_buckets splits long ranges newest-first into ≤bucket pieces."""
    from datetime import datetime, timezone, timedelta
    from app.mcp.tools.watch_tools import compute_time_buckets

    end = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=72)  # 3 days
    buckets = compute_time_buckets(start, end, 1440)  # 24h buckets
    check("cw_buckets count for 72h/24h", len(buckets) == 3, str(len(buckets)))
    check("cw_buckets newest-first", buckets[0][1] == end and buckets[-1][0] == start, str(buckets[0]))
    check("cw_buckets contiguous", buckets[0][0] == buckets[1][1] and buckets[1][0] == buckets[2][1])
    # range within one bucket → single bucket
    one = compute_time_buckets(end - timedelta(hours=6), end, 1440)
    check("cw_buckets single when within bucket", len(one) == 1, str(one))


def test_cloudwatch_metric_query_build() -> None:
    """build_metric_queries_from_discovery filters to inferred resources + caps."""
    from app.mcp.tools.metrics_tools import build_metric_queries_from_discovery

    disc = {
        "inferred_targets": [{"dimension_value": "kyc-auth"}],
        "metrics": [
            {"namespace": "AWS/Lambda", "metric_name": "Throttles",
             "dimensions": [{"Name": "FunctionName", "Value": "kyc-auth"}]},
            {"namespace": "AWS/Lambda", "metric_name": "Errors",
             "dimensions": [{"Name": "FunctionName", "Value": "other-fn"}]},
        ],
    }
    q = build_metric_queries_from_discovery(disc)
    check("cw_mq filters to inferred resource", len(q) == 1 and q[0]["MetricStat"]["Metric"]["MetricName"] == "Throttles", str(q))
    check("cw_mq builds valid MetricStat", q[0]["MetricStat"]["Stat"] == "Sum" and q[0]["Id"] == "m0", str(q[0]))


async def test_cloudwatch_bucketed_merge() -> None:
    """query_with_insights_bucketed merges per-bucket results/stats and early-stops."""
    from datetime import datetime, timezone, timedelta
    from app.mcp.tools.watch_tools import CloudWatchLogWatcher

    w = CloudWatchLogWatcher.__new__(CloudWatchLogWatcher)  # skip boto3 client
    w.region = "us-east-1"
    calls = {"n": 0}

    async def _fake_q(**kw):
        calls["n"] += 1
        return {"status": "Complete", "results": [{"r": calls["n"]}],
                "statistics": {"bytesScanned": 1000, "recordsScanned": 10, "recordsMatched": 1}}

    w.query_with_insights = _fake_q
    end = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=72)
    merged = await w.query_with_insights_bucketed(
        log_group_names=["/a"], query_string="fields @timestamp | limit 50",
        start_time=start, end_time=end, limit=50, bucket_minutes=1440)
    check("cw_bucketed runs one query per bucket", calls["n"] == 3, str(calls))
    check("cw_bucketed merges results", len(merged["results"]) == 3, str(merged["results"]))
    check("cw_bucketed sums stats", merged["statistics"]["bytesScanned"] == 3000, str(merged["statistics"]))

    # early stop once limit reached
    calls["n"] = 0
    merged2 = await w.query_with_insights_bucketed(
        log_group_names=["/a"], query_string="fields @timestamp | limit 2",
        start_time=start, end_time=end, limit=2, bucket_minutes=1440)
    check("cw_bucketed early-stops at limit", calls["n"] == 2 and len(merged2["results"]) == 2, str(calls))


# ── cloudwatch multi-region (Phase 5) ────────────────────────────────────────
def test_cloudwatch_merge_region_evidence() -> None:
    """merge_region_evidence namespaces findings and computes worst-of coverage."""
    from app.workflow.executor.cloudwatch_analysis import merge_region_evidence

    per_region = {
        "us-east-1": {
            "data_quality": {"coverage": "full", "partial": False, "failures": []},
            "alarms": {"summary": {"total": 2, "in_alarm": 1, "names": ["a1"], "flapping": ["a1"]}},
            "patterns": {"unique_patterns": [{"normalized_pattern": "boom", "occurrence_count": 9}]},
            "anomalies": {"anomalies": [{"log_group": "/lg", "z_score": 4.0, "severity": "high"}]},
        },
        "eu-west-1": {
            "data_quality": {"coverage": "none", "partial": True,
                             "failures": [{"analysis": "error-patterns", "error": "AccessDenied"}]},
            "alarms": {"summary": {"total": 1, "in_alarm": 0, "names": ["b1"]}},
            "patterns": {"unique_patterns": []},
            "anomalies": {"anomalies": []},
        },
    }
    m = merge_region_evidence(per_region)
    check("cw_merge worst-of coverage", m["data_quality"]["coverage"] == "partial", m["data_quality"]["coverage"])
    check("cw_merge per-region breakdown",
          m["data_quality"]["regions"] == {"us-east-1": "full", "eu-west-1": "none"}, str(m["data_quality"]["regions"]))
    check("cw_merge tags pattern region", m["patterns"]["unique_patterns"][0]["region"] == "us-east-1")
    check("cw_merge namespaces anomaly log_group",
          m["anomalies"]["anomalies"][0]["log_group"] == "us-east-1:/lg", str(m["anomalies"]["anomalies"][0]))
    check("cw_merge sums alarms + namespaces names",
          m["alarms"]["summary"]["total"] == 3 and m["alarms"]["summary"]["in_alarm"] == 1
          and "us-east-1:a1" in m["alarms"]["summary"]["names"], str(m["alarms"]["summary"]))
    check("cw_merge namespaces flapping", m["alarms"]["summary"]["flapping"] == ["us-east-1:a1"])
    check("cw_merge carries failures with region",
          any(f.get("region") == "eu-west-1" for f in m["data_quality"]["failures"]), str(m["data_quality"]["failures"]))
    # all-full → full
    m2 = merge_region_evidence({
        "r1": {"data_quality": {"coverage": "full"}, "patterns": {"unique_patterns": []}},
        "r2": {"data_quality": {"coverage": "full"}, "patterns": {"unique_patterns": []}},
    })
    check("cw_merge all-full → full", m2["data_quality"]["coverage"] == "full")


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
    await test_cloudwatch_cache_stats()
    test_cloudwatch_error_classifier()
    test_cloudwatch_synthesis_payload_excludes_run_stats()
    test_cloudwatch_severity_clamp()
    await test_cloudwatch_analyzer_cache()
    test_cloudwatch_drilldown_scoring()
    test_cloudwatch_query_fixup()
    test_cloudwatch_alarm_and_metric_helpers()
    test_cloudwatch_payload_includes_metrics()
    test_cloudwatch_cost_footer()
    test_cloudwatch_drill_window()
    await test_cloudwatch_rate_limiter()
    test_cloudwatch_time_buckets()
    test_cloudwatch_metric_query_build()
    await test_cloudwatch_bucketed_merge()
    test_cloudwatch_merge_region_evidence()
    print("-" * 40)
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} check(s): {', '.join(_FAILURES)}")
        return 1
    print("ALL HARNESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
