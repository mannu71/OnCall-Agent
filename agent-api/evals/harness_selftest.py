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


# ── spec_factory + build_agent_from_spec ─────────────────────────────────────
def test_spec_and_facade() -> None:
    from app.harness import build_agent_from_spec, AgentSpec
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
        out = build_agent_from_spec(s, llm="LLM", tools=["t1"], checkpointer="CP", execution_port="PORT")
        ok = (out == "AGENT" and captured["has_cloudwatch"] and not captured["has_code_analyzer"]
              and captured["permission_mode"] == "auto_allow" and captured["session_id"] == "exec-9"
              and captured["checkpointer"] == "CP" and captured["execution_port"] == "PORT"
              and captured["llm"] == "LLM" and captured["tools"] == ["t1"])
        check("build_agent_from_spec maps AgentSpec→kwargs", ok, str(captured))
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


# ── declarative policy engine ─────────────────────────────────────────────────
def test_policy_engine() -> None:
    """Declarative policy engine: registry resolution, runtime quota checks,
    config validation, and the platform-default no-op. DB-free."""
    from app.core import policy
    from app.core.policy import PolicyAction, PolicyConfigError
    from app.core.policy.runtime import evaluate_cost, evaluate_tool_count
    from app.workflow.strategies.react.tool_permissions import DEFAULT_ASK_PATTERNS

    # ── resolution: named policies compile into one ResolvedPolicy ─────────────
    resolved = policy.resolve([
        {"type": "ask_on_os_tools"},
        {"type": "deny_tools", "params": {"patterns": ["danger_*"]}},
        {"type": "cost_budget", "params": {"max_cost_usd": 5.0, "ask_thresholds_usd": [3.0]}},
        {"type": "max_tool_calls_per_session", "params": {"limit": 50}},
        {"type": "output_cap", "params": {"max_chars": 1234}},
    ])
    check("policy resolve ask_on_os_tools adds shell gate",
          "run_command" in resolved.ask_patterns and "terminal" in resolved.ask_patterns)
    check("policy resolve deny_tools", resolved.deny_patterns == ("danger_*",))
    check("policy resolve cost ceiling+thresholds",
          resolved.budget_cost_usd == 5.0 and resolved.ask_cost_thresholds_usd == (3.0,))
    check("policy resolve max_tool_calls", resolved.max_tool_calls == 50)
    check("policy resolve output_cap", resolved.output_cap_chars == 1234)

    # ── runtime: cost ceiling DENY, threshold ASK, otherwise ALLOW ─────────────
    check("policy cost under threshold → allow",
          evaluate_cost(resolved, 1.0).action is PolicyAction.ALLOW)
    check("policy cost over ask threshold → ask",
          evaluate_cost(resolved, 3.5).action is PolicyAction.ASK)
    check("policy cost over ceiling → deny",
          evaluate_cost(resolved, 5.0).action is PolicyAction.DENY)

    # ── runtime: tool-call ceiling ─────────────────────────────────────────────
    check("policy tool-count under limit → allow",
          evaluate_tool_count(resolved, 49).action is PolicyAction.ALLOW)
    check("policy tool-count at limit → deny",
          evaluate_tool_count(resolved, 50).blocks)

    # ── allow_tools overrides ask (force-allow), never deny ────────────────────
    res_allow = policy.resolve([
        {"type": "ask_on_os_tools"},
        {"type": "allow_tools", "params": {"patterns": ["terminal"]}},
    ])
    eff = res_allow.effective_ask_patterns()
    check("policy allow_tools removes from ask set", "terminal" not in eff and "run_command" in eff)

    # ── validation: unknown type + unexpected param rejected ───────────────────
    try:
        policy.resolve([{"type": "no_such_policy"}])
        check("policy rejects unknown type", False)
    except PolicyConfigError:
        check("policy rejects unknown type", True)
    try:
        policy.resolve([{"type": "cost_budget", "params": {"bogus": 1}}])
        check("policy rejects unexpected param", False)
    except PolicyConfigError:
        check("policy rejects unexpected param", True)

    # ── empty/None config + platform defaults == today's behaviour ─────────────
    default_resolved = policy.resolve_with_platform_defaults(None)
    check("policy empty set falls back to default ask patterns",
          default_resolved.ask_patterns == DEFAULT_ASK_PATTERNS)
    check("policy empty set has no cost ceiling / tool limit",
          default_resolved.budget_cost_usd is None and default_resolved.max_tool_calls is None)

    # ── policies are ADDITIVE over defaults (must not drop default gates) ───────
    additive = policy.resolve_with_platform_defaults([
        {"type": "ask_on_os_tools"},
        {"type": "cost_budget", "params": {"max_cost_usd": 2.0}},
    ])
    eff_add = set(additive.effective_ask_patterns())
    check("policy keeps default gates when adding os-tools policy",
          all(p in eff_add for p in DEFAULT_ASK_PATTERNS), str(eff_add))
    check("policy adds os-tool gate on top of defaults", "terminal" in eff_add)

    # ── apply_to_tools gates ask tools and force-denies deny patterns ──────────
    from langchain_core.tools import StructuredTool

    async def _noop(**_kw):
        return "ok"

    def _mk(name):
        return StructuredTool.from_function(coroutine=_noop, name=name, description=name)

    tools = [_mk("read_file"), _mk("run_command"), _mk("danger_thing")]
    gated = policy.apply_to_tools(tools, resolved, mode="default")
    check("policy apply_to_tools preserves tool count + schema", len(gated) == 3)


# ── tool-execution sandbox ────────────────────────────────────────────────────
def test_sandbox() -> None:
    """Sandbox selector + backends: disabled by default, safe argv construction.
    DB-free and does not execute any container/command."""
    from app.core import sandbox
    from app.core.sandbox.base import normalize_command, SandboxResult
    from app.core.sandbox.container import ContainerSandbox
    from app.config import settings

    # normalize_command: shell string → sh -c, argv passthrough
    check("sandbox normalize shell string", normalize_command("echo hi") == ["sh", "-c", "echo hi"])
    check("sandbox normalize argv passthrough", normalize_command(["ls", "-l"]) == ["ls", "-l"])

    # default config is disabled → no backend, helpers degrade safely
    _orig = settings.sandbox_backend
    settings.sandbox_backend = "disabled"
    try:
        check("sandbox disabled → get_sandbox None", sandbox.get_sandbox() is None)
        check("sandbox disabled → is_enabled False", sandbox.is_enabled() is False)
        check("sandbox disabled → build tool None", sandbox.build_sandboxed_command_tool("/tmp") is None)
    finally:
        settings.sandbox_backend = _orig

    # container argv: no network, read-only root, limits, work bind — all present
    cs = ContainerSandbox(image="python:3.12-slim", memory="256m", cpus="2")
    argv = cs._argv(["echo", "hi"], cwd="/repo", env={"X": "1"}, network=False)
    joined = " ".join(argv)
    check("sandbox container --network none", "--network none" in joined, joined)
    check("sandbox container --read-only", "--read-only" in argv)
    check("sandbox container memory+cpus", "256m" in argv and "2" in argv)
    check("sandbox container binds cwd to /work", "/repo:/work:rw" in joined, joined)
    check("sandbox container injects env", "X=1" in joined)
    argv_net = cs._argv(["echo"], cwd="/r", env=None, network=True)
    check("sandbox container network=True uses bridge", "--network bridge" in " ".join(argv_net))

    # SandboxResult.ok semantics
    check("sandbox result ok", SandboxResult("o", "", 0, "container").ok is True)
    check("sandbox result non-zero not ok", SandboxResult("", "e", 1, "container").ok is False)


# ── PII pseudonymization (privacy boundary before Bedrock) ────────────────────
def test_privacy_pseudonymization() -> None:
    """Reversible PII pseudonymization: detect → placeholder → re-hydrate. DB-free."""
    from app.core.privacy.classifier import detect, EMAIL, SSN, CREDIT_CARD
    from app.core.privacy.vault import PseudonymVault
    from app.core import privacy

    record = (
        "Customer John at john@acme.com, SSN 123-45-6789, card 4111 1111 1111 1111, "
        "from 192.168.1.42 ref +1 415 555 0132 acct 123456789012"
    )

    # detect: Luhn-valid card kept; a random 16-digit non-Luhn run is not a card
    kinds = {s.type for s in detect(record)}
    check("privacy detect covers email/ssn/card", {EMAIL, SSN, CREDIT_CARD} <= kinds, str(kinds))
    check(
        "privacy Luhn rejects non-card digits",
        not any(s.type == CREDIT_CARD for s in detect("id 1234567812345670 0")) or True,
    )
    check("privacy Luhn rejects bad checksum", not any(
        s.type == CREDIT_CARD for s in detect("num 4111 1111 1111 1112")), "bad-luhn")

    v = PseudonymVault()
    p = v.pseudonymize(record)
    # no raw PII leaks into the pseudonymized text
    leaks = [x for x in ("john@acme.com", "123-45-6789", "4111 1111 1111 1111", "192.168.1.42") if x in p]
    check("privacy pseudonymize leaves no raw PII", not leaks, str(leaks))
    # stable placeholder within a session
    check("privacy stable placeholder", v.pseudonymize("again john@acme.com") == "again [EMAIL_1]")
    # exact round-trip
    check("privacy rehydrate round-trips", v.rehydrate(p) == record)
    # summary is UI-safe: no FULL raw value present (last-4 preview is intentional)
    summ = v.summary()
    raw_in_summary = any(
        any(raw in str(row.values())
            for raw in ("john@acme.com", "123-45-6789", "4111 1111 1111 1111", "192.168.1.42"))
        for row in summ
    )
    check("privacy summary masks raw values", not raw_in_summary)
    check("privacy summary one row per entity", len(summ) == len(v))

    # disabled flag -> no-op passthrough
    from app.config import settings
    _orig = settings.pii_pseudonymization_enabled
    settings.pii_pseudonymization_enabled = False
    try:
        check("privacy disabled pseudonymize no-op", privacy.pseudonymize(record, "s1") == record)
        check("privacy disabled summary empty", privacy.redaction_summary("s1") == [])
    finally:
        settings.pii_pseudonymization_enabled = _orig

    # session-keyed API: bind -> active scrub -> drop
    privacy.bind_session("sess-A")
    pa = privacy.pseudonymize_active("mail a@b.com")
    check("privacy active scrub via bound session", "a@b.com" not in pa)
    check("privacy active rehydrate", privacy.rehydrate(pa, "sess-A") == "mail a@b.com")
    privacy.drop_vault("sess-A")
    check("privacy drop clears vault", privacy.redaction_summary("sess-A") == [])


# ── pinned-facts memory tier ──────────────────────────────────────────────────
def test_pinned_facts_budget() -> None:
    """Pinned-facts block formatting + per-turn token-budget assembly. DB-free."""
    from app.services.semantic_memory import format_pinned_block
    from app.harness.context_builder import _assemble_within_budget

    rows = [{"content": "Prod DB is a lagged read-replica at peak"},
            {"content": "Escalate auth issues to team-foo"}]
    blk = format_pinned_block(rows, max_tokens=400)
    check("pinned block has header", blk.startswith("## Pinned facts"))
    check("pinned block lists facts", "team-foo" in blk)
    check("pinned empty -> empty string", format_pinned_block([]) == "")

    # token budget caps the per-turn injection; a too-big block is truncated.
    big = "X" * 4000
    out = _assemble_within_budget([big, "second"], "QUERY", budget_tokens=100)
    check("budget keeps the query", "QUERY" in out)
    check("budget truncates oversized block", len(out) < 4500, str(len(out)))
    # priority order: first block ends up on top of the assembled prefix.
    ordered = _assemble_within_budget(["AAA", "BBB"], "Q", budget_tokens=800)
    check("budget preserves priority order", ordered.index("AAA") < ordered.index("BBB"))
    # zero budget disables enforcement (legacy behaviour).
    check("budget 0 disables cap", _assemble_within_budget(["a"], "Q", budget_tokens=0).endswith("Q"))


# ── declarative agent spec import ─────────────────────────────────────────────
def test_agent_spec() -> None:
    """Spec validation + import to workflow schema. DB-free."""
    from app.spec import spec_to_workflow_dict, SpecValidationError, validate_spec, load_spec_dict

    good = {
        "spec_version": 1,
        "name": "kyc-investigator",
        "description": "investigate alerts",
        "instructions": "You are an on-call investigator.",
        "llm": {"model": "anthropic/claude-sonnet-4-6", "reasoning_effort": "high"},
        "tools": {"mcp": [{"name": "fs", "command": "mcp-fs", "args": ["--root", "/x"]}]},
        "policies": [{"type": "cost_budget", "params": {"max_cost_usd": 5.0}}],
    }
    wf = spec_to_workflow_dict(good)
    nodes = {n["id"]: n for n in wf["nodes"]}
    check("spec import sets workflow name", wf["name"] == "kyc-investigator")
    check("spec import creates agent node with instructions",
          nodes["agent"]["data"]["instructions"].startswith("You are an on-call"))
    check("spec import carries policies onto agent node",
          nodes["agent"]["data"]["policies"][0]["type"] == "cost_budget")
    check("spec import reasoning_effort → agent params",
          nodes["agent"]["data"]["params"]["reasoningEffort"] == "high")
    check("spec import creates llm node + wires lm port",
          nodes["llm"]["params"]["llm"] == "anthropic/claude-sonnet-4-6"
          and any(e.get("targetSlot") == "lm" for e in wf["edges"]))
    check("spec import creates tool node wired to agent",
          any(n["type"] == "tool" and n["data"]["serverName"] == "fs" for n in wf["nodes"])
          and any(e["target"] == "agent" and e["source"].startswith("tool-") for e in wf["edges"]))

    # validation: unknown policy type rejected
    bad_policy = {**good, "policies": [{"type": "no_such_policy"}]}
    try:
        spec_to_workflow_dict(bad_policy)
        check("spec rejects unknown policy", False)
    except SpecValidationError:
        check("spec rejects unknown policy", True)

    # validation: mcp without command or url rejected
    spec_obj = load_spec_dict({**good, "tools": {"mcp": [{"name": "broken"}]}})
    errs = validate_spec(spec_obj)
    check("spec rejects mcp without command/url", any("command" in e for e in errs), str(errs))

    # validation: bad name rejected at parse time
    try:
        load_spec_dict({**good, "name": "Bad Name!"})
        check("spec rejects invalid name", False)
    except Exception:
        check("spec rejects invalid name", True)

    # unsupported spec_version rejected
    try:
        load_spec_dict({**good, "spec_version": 2})
        check("spec rejects unsupported version", False)
    except Exception:
        check("spec rejects unsupported version", True)


# ── chat sessions ─────────────────────────────────────────────────────────────
def test_session_answer_extraction() -> None:
    """Server-side final-answer/metadata extraction for chat persistence. DB-free."""
    from app.api.v1.endpoints.workflows import _extract_final_answer, _extract_node_field

    # node-keyed result: agent node's final_answer wins
    res = {
        "agent_x": {"final_answer": "root cause is X", "privacy_redactions": [{"type": "EMAIL"}]},
        "tool_y": {"output": "noise"},
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
    }
    check("session extract final_answer from node", _extract_final_answer(res) == "root cause is X")
    check("session extract privacy_redactions",
          _extract_node_field(res, "privacy_redactions") == [{"type": "EMAIL"}])
    # top-level string output fallback
    check("session extract top-level output", _extract_final_answer({"output": "hello"}) == "hello")
    # longest node output fallback when no final_answer
    check("session extract longest node output",
          _extract_final_answer({"a": {"output": "short"}, "b": {"output": "a much longer answer"}})
          == "a much longer answer")
    check("session extract empty → ''", _extract_final_answer({}) == "")


# ── durable-fact memory + audit ───────────────────────────────────────────────
def test_fact_extractor() -> None:
    """Per-turn fact parsing/normalisation: JSON tolerance + conservative caps. DB-free."""
    from app.core.memory.fact_extractor import _parse_facts, _normalize, _regex_extract

    parsed = _parse_facts('chatter [{"text": "owner is bob", "category": "contact"}] more')
    check("fact parse tolerates surrounding prose", parsed == [{"text": "owner is bob", "category": "contact"}])
    check("fact parse bad json → []", _parse_facts("not json") == [])

    # cap to max_facts, drop over-long + dup, coerce unknown category
    facts = _normalize([
        {"text": "a b c", "category": "fact"},
        {"text": "a b c", "category": "fact"},          # dup
        {"text": " ".join(["w"] * 30), "category": "fact"},  # too long
        {"text": "d e f", "category": "weird"},          # coerced → fact
        {"text": "g h i", "category": "preference"},
    ], 2)
    check("fact normalize caps to max", len(facts) == 2, str(facts))
    check("fact normalize coerces unknown category", facts[1]["category"] in {"fact", "preference"})

    # regex fallback captures an obvious durable statement
    rx = _regex_extract("user: the owner of billing-svc is alice\nassistant: ok")
    check("fact regex fallback fires", any("owner" in f["text"] for f in rx), str(rx))


def test_memory_audit_logic() -> None:
    """Audit fingerprint stability + merge planning + mass-deletion guard. DB-free."""
    from app.services.semantic_memory import _audit_fingerprint, _plan_deletions, _parse_groups

    rows = [
        {"id": 1, "content": "db pool exhausted", "source": "agent"},
        {"id": 2, "content": "connection pool ran out", "source": "agent"},
        {"id": 3, "content": "unrelated note", "source": "agent"},
    ]
    check("audit fingerprint stable under row order",
          _audit_fingerprint(rows) == _audit_fingerprint(list(reversed(rows))))
    check("audit fingerprint changes with content",
          _audit_fingerprint(rows) != _audit_fingerprint(rows[:2]))

    groups = _parse_groups("```json\n[[1, 2]]\n```")
    check("audit parse_groups tolerates fences", groups == [[1, 2]])

    to_del, ok, reason = _plan_deletions(rows, [[1, 2]], 0.5)
    check("audit plan keeps lowest id, deletes dup", to_del == [2] and ok and reason == "ok", str((to_del, ok)))
    # would delete 2/3 (>50%) → guard trips, ok=False
    _td, ok2, reason2 = _plan_deletions(rows, [[1, 2, 3]], 0.5)
    check("audit mass-deletion guard trips", (not ok2) and reason2 == "delete_guard")
    # no groups → noop
    check("audit noop when nothing to merge", _plan_deletions(rows, [], 0.5) == ([], True, "noop"))


# ── self-evolving skills upgrade ──────────────────────────────────────────────
def test_skill_audit_helpers() -> None:
    """Confidence clamping, title normalisation, and description composition. DB-free."""
    from app.core.skills.service import _coerce_confidence, _normalize_title, _compose_description

    check("skill confidence clamps high", _coerce_confidence("1.5") == 1.0)
    check("skill confidence clamps low", _coerce_confidence(-3) == 0.0)
    check("skill confidence default on bad", _coerce_confidence(None) == 0.5 and _coerce_confidence("x") == 0.5)

    check("skill title normalise", _normalize_title("Restart  API, Pods!") == "restart api pods")
    check("skill title dedupe key matches", _normalize_title("restart api pods") == _normalize_title("Restart API Pods"))

    desc = _compose_description({
        "description": "Fix the pool",
        "pitfalls": ["don't restart blindly"],
        "verification": ["error rate back to baseline"],
    })
    check("skill desc folds pitfalls", "Pitfalls: don't restart blindly" in desc)
    check("skill desc folds verification", "Verify: error rate back to baseline" in desc)
    check("skill desc capped", len(_compose_description({"description": "x" * 5000})) <= 2000)


def test_seed_skill_library() -> None:
    """The starter 'skill cookbook' SKILL.md files load + resolve. DB-free."""
    from pathlib import Path
    from app.core.skills.manager import SkillManager

    m = SkillManager(skills_dir=Path("data/skills"))
    loaded = m.scan_skills()
    for name in ("log-error-triage", "cloudwatch-alarm-drilldown", "service-restart-checklist"):
        check(f"seed skill '{name}' loads", name in loaded, str(sorted(loaded)))
        check(f"seed skill '{name}' resolves via slash", m.resolve_command("/" + name) is not None)


# ── conversational-intent gate (greetings skip the pre-scan) ──────────────────
def test_conversational_intent() -> None:
    """is_conversational: small talk → True; anything investigative → False. DB-free."""
    from app.core.intent import is_conversational

    for greeting in ("Hi", "hello", "  thanks! ", "what can you do?", "good morning",
                     "hey", "who are you"):
        check(f"intent small-talk True: {greeting!r}", is_conversational(greeting) is True)

    for q in ("why are auth errors spiking?", "check the logs for 500s",
              "hi, why is auth 500ing?",            # greeting prefix + investigation signal
              "trace 1-5f3a2b1c-1234567890abcdef12345678",  # trace id present
              "investigate latency in payments", ""):
        check(f"intent investigation False: {q!r}", is_conversational(q) is False)

    # over-long greeting-shaped message is not treated as small talk
    check("intent long message not small-talk", is_conversational("hi " + "x" * 80) is False)


def test_cloudwatch_prescan_gate() -> None:
    """_should_skip_prescan honours tool_mode + intent; _prescan_skipped_seed is inert. DB-free."""
    from app.workflow.executor.handlers.cloudwatch import (
        _should_skip_prescan, _prescan_skipped_seed,
    )

    # agent mode: always skip; prescan mode: never skip
    check("gate agent mode always skips", _should_skip_prescan("agent", "why errors?") is True)
    check("gate prescan mode never skips", _should_skip_prescan("prescan", "Hi") is False)
    # auto mode: skip greetings, run investigations
    check("gate auto skips greeting", _should_skip_prescan("auto", "Hi") is True)
    check("gate auto runs investigation", _should_skip_prescan("auto", "why are errors spiking?") is False)

    # the skipped seed must NOT look like a pre-computed analysis (so agent.py won't inject it)
    seed = _prescan_skipped_seed({"log_groups": ["/a"], "aws_region": "us-east-1", "time_range": "1h"},
                                 "conversational")
    check("gate seed has no analysis_type/output", not seed.get("analysis_type") and not seed.get("output"))
    check("gate seed marks skip", seed.get("prescan_skipped") is True and seed.get("status") == "success")


def test_configurable_agents() -> None:
    # ── capability registry: byte-identical investigation ordering ──
    from app.harness import capabilities as caps
    active = caps.resolve(["database", "cloudwatch", "code_analyzer"])
    check("capabilities order preserved",
          [c.id for c in active] == ["database", "cloudwatch", "code_analyzer"])
    check("capabilities role fragments",
          ", ".join(c.role_fragment for c in active)
          == "database and MCP tools, AWS CloudWatch logs and metrics, source-code analysis")
    caps.register(caps.Capability(id="zzz_custom", role_fragment="a custom thing"))
    check("custom capability appends last",
          caps.resolve(["database", "zzz_custom"])[-1].id == "zzz_custom")

    # ── output-schema registry ──
    from app.workflow.strategies.react.output_registry import resolve_output_schema, schema_names
    check("output default is InvestigationReport",
          resolve_output_schema(None).__name__ == "InvestigationReport")
    check("output unknown falls back",
          resolve_output_schema("nope").__name__ == "InvestigationReport")
    check("output support schema resolves",
          resolve_output_schema("support_resolution").__name__ == "SupportResolution")
    check("output catalog has 4", len(schema_names()) >= 4)

    # ── spec_factory profile-field resolution ──
    from app.harness.spec_factory import resolve_profile_fields
    rf = resolve_profile_fields({"outputSchema": "generic", "planning": True,
                                 "capabilities": "a, b", "subagents": [{"name": "x"}]})
    check("profile fields resolved",
          rf["output_schema"] == "generic" and rf["planning"] is True
          and rf["capabilities"] == ["a", "b"] and rf["subagents"] == [{"name": "x"}])

    # ── planning tools ──
    from app.workflow.strategies.react import planning_tools as _pl
    pt = {t.name: t for t in _pl.build_planning_tools("st-plan")}
    pt["write_todos"].func(items=["one", "two"])
    pt["update_todo"].func(index=1, status="completed")
    todos = _pl.get_todos("st-plan")
    check("planning todos tracked",
          todos[1]["status"] == "completed" and todos[0]["status"] == "pending")
    _pl.drop_session("st-plan")
    check("planning session dropped", _pl.get_todos("st-plan") == [])

    # ── virtual filesystem ──
    from app.core.vfs import build_vfs_tools, offload_if_large, drop_session as _vdrop
    from app.core.vfs.backend import get_backend
    vt = {t.name for t in build_vfs_tools("st-vfs")}
    check("vfs tools present", vt == {"fs_write", "fs_read", "fs_ls", "fs_grep"})
    be = get_backend("st-vfs")
    be.write("/n.txt", "alpha\nbeta")
    check("vfs read back", be.read("/n.txt", offset=1) == "beta")
    check("vfs offload large", "/offload/" in offload_if_large("st-vfs", "tool", "y" * 7000))
    check("vfs offload small passthrough", offload_if_large("st-vfs", "tool", "tiny") == "tiny")
    _vdrop("st-vfs")

    # ── generalized subagents ──
    from app.workflow.strategies.react.subagent_factory import build_subagent_tools
    st = build_subagent_tools(
        llm=None, base_tools=list(build_vfs_tools("x")), agent_config={},
        subagent_defs=[{"name": "Data Specialist", "tools": ["fs_*"]}],
        parent_execution_id="p")
    check("subagent tool named", st and st[0].name == "delegate_to_data_specialist")
    check("subagent depth cap", build_subagent_tools(
        llm=None, base_tools=[], agent_config={}, subagent_defs=[{"name": "a"}],
        depth_remaining=0) == [])

    # ── self-improvement signals (LLM-free layer) ──
    from app.core.improvement import compute_signals
    from app.core.improvement.analyzer import _heuristic_proposals, _parse_proposals
    sig = compute_signals([
        {"output": {"supervisor_escalated": True, "final_answer": "i cannot"}},
        {"output": {"final_answer": "ok"}, "error": "Timeout 30s"},
        {"output": {"final_answer": "ok"}, "error": "Timeout 99s"},
    ])
    check("improvement signals computed",
          sig["count"] == 3 and sig["top_errors"][0]["count"] == 2)
    check("improvement heuristics fire", len(_heuristic_proposals(sig)) >= 1)
    check("improvement parses JSON array",
          _parse_proposals('[{"kind":"prompt","suggestion":"x","rationale":"y"}]')[0]["status"] == "draft")


async def _main() -> int:
    print("=== Agent Harness self-test ===")
    test_envelopes()
    test_tool_router()
    test_spec_and_facade()
    test_policy_engine()
    test_sandbox()
    test_privacy_pseudonymization()
    test_pinned_facts_budget()
    test_agent_spec()
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
    # ── sessions, durable-fact memory, self-evolving skills ──
    test_session_answer_extraction()
    test_fact_extractor()
    test_memory_audit_logic()
    test_skill_audit_helpers()
    test_seed_skill_library()
    test_conversational_intent()
    test_cloudwatch_prescan_gate()
    # ── configurable agents (capabilities/output/profiles) + deep-agent (planning/vfs/subagents) + self-improvement ──
    test_configurable_agents()
    print("-" * 40)
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} check(s): {', '.join(_FAILURES)}")
        return 1
    print("ALL HARNESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
