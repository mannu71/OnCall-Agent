"""Agent-trajectory eval — run the REAL ReAct agent and grade what it DID.

The codegraph/CloudWatch suites grade tools in isolation. This suite closes the
gap that actually leaks real-world accuracy: given a question and a set of
attached tools, does the agent reach for the right tool family, follow the
locate→read→trace / drill-don't-rescan protocol, and state a correct,
ID-grounded answer?

Hermetic setup:
  * CloudWatch is faked at the data layer (``fakes_cloudwatch.patched_cloudwatch``)
    so the real tool code runs over recorded fixtures — no live AWS.
  * Code intelligence is the REAL in-process codegraph engine, connected and
    fixture-indexed ONCE per suite via the same inline-stdio path production uses
    (``codegraph_tools.build_codegraph_tools``). Code cases therefore run
    IN-CONTAINER only (the engine binary is baked into the image), like
    ``run_codegraph`` — on a host without it they fail loud rather than silently
    grading a different tool set. The generic repo file tools run alongside it
    against ``fixtures/sample_repo`` (synced via ``_sync_fixture_repo``).
  * The agent LLM is real Bedrock (same judge-calibrated path as the other
    suites); ``temperature`` is pinned low for repeatability.

Grading is deterministic (no LLM judge in the headline): predicate sets over the
tool-call trajectory and substring checks over the final answer. Because the
agent LLM is non-deterministic, a case that misses on the first run is retried
once and the better-scoring run is kept (``retried`` flagged on its rows).

This deliberately bypasses ``assemble_base_tools`` / the tool router (a hermetic
run can't do the STS credential pre-flight) — the router is covered by
``evals.harness_selftest``. We build the same tool families the strategy would,
through the same builders it calls (``build_codegraph_tools`` /
``build_repo_file_tools`` / ``add_extension_tools``), and drive the same
``build_agent_from_spec`` → ``execute_agent`` path.

Run:  python -m evals.accuracy.run_trajectory
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from evals.accuracy import _bootstrap  # noqa: F401  (boto3 SSL patch on import)
from evals.accuracy import graders
from evals.accuracy.build_crawler_cases import DEFAULT_REPO_NAME
from evals.accuracy.fakes_cloudwatch import FakeCloudWatchRecording, patched_cloudwatch

HERE = os.path.dirname(__file__)
CASES_PATH = os.path.join(HERE, "datasets", "trajectory_cases.jsonl")

# Agent model for the loop. Defaults to the haiku id proven enabled in this
# account/region (same default as the CW synthesis + judge). Override per-run.
TRAJ_AGENT_MODEL = os.getenv("TRAJ_AGENT_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0")

logger = logging.getLogger("eval.trajectory")


# ── trajectory capture ────────────────────────────────────────────────────────
class ToolTrace:
    """Records every tool call's raw (uncapped) output for grounding evidence.

    Wraps each tool with a schema-preserving recorder clone placed INNERMOST, so
    the permission gate + output cap that ``build_agent`` adds sit on top: the
    recorder sees the raw output while the agent sees the capped version (the
    documented asymmetry only loosens grounding, never falsely fails it).
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def wrap(self, tools: List[Any]) -> List[Any]:
        return [self._wrap_one(t) for t in tools]

    def _wrap_one(self, tool: Any) -> Any:
        from langchain_core.tools import StructuredTool

        orig = getattr(tool, "coroutine", None)
        name = getattr(tool, "name", "")
        if orig is None:
            # No ``.coroutine`` — an MCP-backed BaseTool (codegraph's
            # MCPToolWrapper) implements ``_arun`` instead. Dispatch through its
            # own ``ainvoke`` so its calls land in the trace too; without this the
            # whole codegraph family would be invisible (undercounting
            # tool_calls_n and dropping its output from the grounding evidence).
            if not hasattr(tool, "_arun"):
                return tool  # genuinely sync / non-structured — leave untouched
            orig = lambda **kw: tool.ainvoke(kw)  # noqa: E731

        async def _recorded(*args: Any, **kwargs: Any) -> Any:
            out = await orig(*args, **kwargs)
            try:
                self.calls.append({"tool": name, "args": dict(kwargs), "output": str(out)})
            except Exception:  # noqa: BLE001 — recording must never break a run
                pass
            return out

        return StructuredTool.from_function(
            coroutine=_recorded,
            name=name,
            description=getattr(tool, "description", ""),
            args_schema=getattr(tool, "args_schema", None),
        )

    def evidence_blob(self) -> str:
        return "\n".join(c["output"] for c in self.calls)


def _turns_from_messages(messages: List[Dict[str, Any]]) -> List[List[str]]:
    """Ordered list of tool-call turns (one per assistant message that called tools)."""
    turns: List[List[str]] = []
    for m in messages or []:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            turns.append([tc.get("name") for tc in m["tool_calls"]])
    return turns


# ── skill utilization: hermetic skill set + listing injection ─────────────────
# Skill fixtures shipped with the eval — the same disk source a real deployment
# would author. Loaded into a hermetic manager per case (below) so the trajectory
# suite grades whether the agent actually LOADS the right runbook (not just that
# the tool works — that's harness_selftest.test_skill_tool_wiring).
SKILLS_FIXTURE_DIR = os.path.join(HERE, "fixtures", "skills")


def _install_skill_fixture(
    invoked_sink: List[str], allowed: Optional[Set[str]] = None,
) -> Tuple[List[Any], List[str], Any]:
    """Point the shared SkillManager at the eval's skill fixtures and build the
    pinned ``search_skills`` + ``skill`` tools over it.

    ``allowed``: per-case skill scoping — the set of fixture skill names this
    case's agent may see/load (its map, search, and invoke set). ``None`` = every
    fixture skill (the whole library). Real deployments scope skills per agent
    (the Agent node's Skills picker), and a case's realistic map size is what
    determines skill-first reliability, so each case declares its own set rather
    than inheriting the accidental union of every fixture on disk.

    Returns ``(skill_tools, prev_default_mgr, mgr)``: the tools to bind, and the
    previously-cached default manager so the caller can restore it (skills use a
    process-wide singleton — mirrors harness_selftest's hermetic override). The
    ``# Skill map`` block is injected into the query by the caller so the system
    prompt's ``# Skills`` section (auto-added when the skill tool is bound) and
    the map both reach the model exactly as in production.
    """
    from pathlib import Path
    import app.core.skills as _skills_pkg
    from app.core.skills.manager import SkillManager
    from app.harness.skill_tools import build_skill_search_tool, build_skill_tool

    mgr = SkillManager(skills_dir=Path(SKILLS_FIXTURE_DIR))
    mgr.scan_skills()
    prev = _skills_pkg._default_manager
    _skills_pkg._default_manager = mgr

    tools = [
        build_skill_search_tool(allowed_skills=allowed, execution_id="trajectory"),
        build_skill_tool(
            allowed_skills=allowed, execution_id="trajectory", invoked_sink=invoked_sink),
    ]
    return tools, prev, mgr


def _skill_map_block(mgr: Any, allowed: Optional[Set[str]] = None) -> str:
    """The ``# Skill map`` block preflight injects — the production builder itself,
    so the eval exercises the real stage-1 disclosure and can't drift from it.
    ``allowed`` scopes the map to this case's visible skills (per-agent scoping)."""
    from app.config import settings
    from app.harness.context_builder import build_skill_map_block

    return build_skill_map_block(mgr.build_map(
        allowed=allowed,
        char_budget=int(getattr(settings, "skill_map_char_budget", 1500))))


async def _build_agent_llm() -> Any:
    """Build the agent LLM via the app's enriched-config path (DB Bedrock creds)."""
    from app.workflow.llm_config import resolve_llm_config
    from app.workflow.strategies.react.llm_factory import build_llm

    wf = {"nodes": [{"id": "agent_lm", "type": "language_model",
                     "data": {"provider": "bedrock", "model": TRAJ_AGENT_MODEL,
                              "temperature": 0}}], "edges": []}
    cfg = await resolve_llm_config(wf)
    return build_llm(cfg)


def _load_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    with open(CASES_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _zero_rows(
    case: Dict[str, Any],
    *,
    engine_name: str,
    diagnostic: str,
    stop_reason: str,
    latency: float = 0.0,
    tool_calls_n: int = 0,
    invoked_skills: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """A full zero-scored row set for *case* — the shape a failed attempt returns.

    Used when the run can't happen at all (an exception, or codegraph being
    unavailable): every objective metric the case would have been graded on
    scores 0 with the real cause in the diagnostic, so the suite completes and
    the report is unambiguously red rather than silently short.
    """
    metrics = ["tool_selection", "protocol", "final_answer", "grounding"]
    if (case.get("expected") or {}).get("skill_invocation"):
        metrics.append("skill_invocation")
    if case.get("post_check"):
        metrics.append("post_check")
    return [{"feature": "trajectory", "id": case["id"], "metric": m, "objective": True,
             "score": 0.0, "diagnostic": diagnostic, "latency_s": latency,
             "tool_calls_n": tool_calls_n, "engine": engine_name,
             "stop_reason": stop_reason, "did_forced_synthesis": False,
             "truncated": False, "invoked_skills": list(invoked_skills or []),
             "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            for m in metrics]


async def _attempt(
    case: Dict[str, Any],
    *,
    mcp_manager: Any = None,
    codegraph_err: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Run one agent trajectory for *case*; return (rows, tool_call_count).

    We drive ``run_agent_once`` rather than ``execute_agent`` directly so the
    suite exercises the same entry point production uses.

    ``mcp_manager`` / ``codegraph_err``: the suite-level codegraph session (see
    :func:`run_trajectory_suite`). A code case with no working engine fails loud
    here, before any Bedrock spend.
    """
    from app.harness import AgentSpec
    from app.harness.hitl import make_checkpointer
    from app.harness.engine import run_agent_once

    # Retained as a stable column in the report schema.
    engine_name = "langgraph"

    trace = ToolTrace()
    recording: Optional[FakeCloudWatchRecording] = None
    cm: Any = contextlib.nullcontext()
    if case.get("cw_fixture"):
        recording = FakeCloudWatchRecording.load(case["cw_fixture"])
        cm = patched_cloudwatch(recording)

    # Skill utilization: when a case opts in (``skill_fixture`` or an
    # ``expected.skill_invocation`` block) bind the pinned ``skill`` tool over the
    # eval's hermetic skill set and inject the listing, so the agent can load a
    # runbook. ``invoked_skills`` records which it actually loaded; the hermetic
    # manager override is restored via ``_restore_skill_default`` before every
    # return (skills use a process-wide singleton — see _install_skill_fixture).
    invoked_skills: List[str] = []
    _skill_wanted = bool(
        case.get("skill_fixture") or (case.get("expected") or {}).get("skill_invocation"))
    _skill_prev = None
    _skill_restored = False

    def _restore_skill_default() -> None:
        nonlocal _skill_restored
        if _skill_wanted and not _skill_restored:
            import app.core.skills as _skills_pkg
            _skills_pkg._default_manager = _skill_prev
            _skill_restored = True

    with cm:
        tools: List[Any] = []
        if case.get("cw_fixture"):
            from app.workflow.tools.cloudwatch_agent_tools import build_cloudwatch_agent_tools
            tools += build_cloudwatch_agent_tools(
                region="us-east-1", credentials={}, log_groups=case.get("log_groups"))

        code_cfg: Optional[Dict[str, Any]] = None
        if case.get("crawler"):
            # Code intel = the real codegraph engine (connected + fixture-indexed
            # once per suite) plus the generic repo file tools, in the same order
            # assemble_base_tools binds them in production. The capabilities
            # prompt this case gets (has_code_analyzer=True) names the
            # codegraph__* tools explicitly, so binding anything less would grade
            # an agent told to call tools it doesn't have.
            # NB: no _restore_skill_default() on these early returns — the skill
            # fixture is installed further down, so nothing has been overridden
            # yet and "restoring" would null out the real process-wide manager.
            if mcp_manager is None or codegraph_err:
                return _zero_rows(
                    case, engine_name=engine_name,
                    diagnostic=f"codegraph unavailable: {codegraph_err or 'no MCP manager'}",
                    stop_reason="codegraph_unavailable",
                ), 0

            from evals.accuracy.build_crawler_cases import _sync_fixture_repo
            from app.workflow.tools.codegraph_tools import build_codegraph_tools
            from app.workflow.tools.repo_file_tools import build_repo_file_tools
            from app.config import settings
            # Re-sync per case so the edit case's mutation never leaks into the
            # next one. Content-identical at identical paths, so the suite-level
            # index stays valid. MUST precede build_codegraph_tools: that reads
            # repos_base_path to alias project= args to codegraph's canonical name.
            settings.repos_base_path = _sync_fixture_repo()
            code_cfg = {"repos": [{"name": DEFAULT_REPO_NAME}]}
            cg_tools = await build_codegraph_tools(mcp_manager, repos=code_cfg["repos"])
            if not cg_tools:
                # build_codegraph_tools degrades to [] rather than raising — for a
                # code case that means the eval would measure nothing. Fail loud.
                return _zero_rows(
                    case, engine_name=engine_name,
                    diagnostic="codegraph unavailable: engine returned no tools",
                    stop_reason="codegraph_unavailable",
                ), 0
            tools += cg_tools
            tools += build_repo_file_tools(repos=code_cfg["repos"])

        llm = await _build_agent_llm()

        if code_cfg:
            from app.harness.tool_assembler import add_extension_tools
            tools = add_extension_tools(
                tools=tools, llm=llm, agent_config={}, code_analyzer_config=code_cfg,
                execution_id=case["id"], logger_instance=logger)

        skill_map_block = ""
        if _skill_wanted:
            # Per-case skill scoping: ``skill_allow`` lists the fixture skills this
            # case's agent may see (its realistic map). Absent → every fixture,
            # matching the legacy behaviour.
            _allow_list = case.get("skill_allow")
            _allowed = set(_allow_list) if _allow_list else None
            skill_tools, _skill_prev, _skill_mgr = _install_skill_fixture(
                invoked_skills, allowed=_allowed)
            # Binding the skill tool makes agent_builder add the "# Skills" prompt.
            tools.extend(skill_tools)
            skill_map_block = _skill_map_block(_skill_mgr, allowed=_allowed)

        tools = trace.wrap(tools)

        spec = AgentSpec(
            agent_config={}, has_cloudwatch=bool(case.get("cw_fixture")),
            has_code_analyzer=bool(code_cfg), permission_mode="auto_allow",
            session_id=case["id"])
        checkpointer = await make_checkpointer()

        query = case["question"]
        if case.get("precomputed_block") and recording is not None:
            from app.harness.context_builder import seed_context_blocks
            query, _ = seed_context_blocks(
                augmented_query=query,
                context={"cloudwatch_context": {"node": {"output": recording.synthesis}}})
        # Stage-1 disclosure: the skill map rides above the query, exactly as
        # preflight.build_run_plan prepends it in production.
        if skill_map_block:
            query = f"{skill_map_block}\n\n---\n\n{query}"

        t0 = time.time()
        try:
            result = await run_agent_once(
                spec, llm, tools, query,
                logger_instance=logger, execution_id=case["id"], thread_id=case["id"],
                checkpointer=checkpointer)
        except Exception as exc:  # noqa: BLE001 — a crashed run is a 0, not a stack trace
            _restore_skill_default()
            rows = _zero_rows(
                case, engine_name=engine_name,
                diagnostic=f"exception: {type(exc).__name__}: {exc}",
                stop_reason="exception",
                latency=round(time.time() - t0, 3),
                tool_calls_n=len(trace.calls),
                invoked_skills=invoked_skills,
            )
            return rows, len(trace.calls)
    _restore_skill_default()

    latency = round(time.time() - t0, 3)
    final = result.get("final_answer") or ""
    turns = _turns_from_messages(result.get("messages") or [])
    exp = case.get("expected") or {}

    sel = graders.grade_tool_selection(turns, exp.get("tool_selection") or {})
    pro = graders.grade_protocol(turns, exp.get("protocol") or {})
    ans = graders.grade_final_answer(final, exp.get("answer") or {})

    ev_parts = [query, trace.evidence_blob()]
    if recording is not None:
        ev_parts.append(recording.synthesis)
    grd = graders.grade_id_grounding(final, "\n".join(ev_parts))

    # Free per-case metrics from the (engine-agnostic) result contract — used by
    # the bake-off report; ignored by runner.aggregate's objective headline.
    common = {"feature": "trajectory", "id": case["id"], "latency_s": latency,
              "tool_calls_n": len(trace.calls), "engine": engine_name,
              "stop_reason": result.get("stop_reason"),
              "did_forced_synthesis": bool(result.get("did_forced_synthesis")),
              "truncated": bool(result.get("truncated")),
              "invoked_skills": list(invoked_skills),
              "input_tokens": result.get("input_tokens") or 0,
              "output_tokens": result.get("output_tokens") or 0,
              "total_tokens": result.get("total_tokens") or 0}
    rows: List[Dict[str, Any]] = [
        {**common, "metric": "tool_selection", "objective": True, "score": sel[0], "diagnostic": sel[1]},
        {**common, "metric": "protocol", "objective": True, "score": pro[0], "diagnostic": pro[1]},
        {**common, "metric": "final_answer", "objective": True, "score": ans[0], "diagnostic": ans[1]},
        {**common, "metric": "grounding", "objective": True, "score": grd[0], "diagnostic": grd[1]},
    ]

    # Skill utilization: did the agent load the runbook the case expects? Graded
    # off the tool's invoked_sink (invoked_skills), captured above.
    skill_exp = exp.get("skill_invocation")
    if skill_exp:
        sk = graders.grade_skill_invocation(invoked_skills, skill_exp)
        rows.append({**common, "metric": "skill_invocation", "objective": True,
                     "score": sk[0], "diagnostic": sk[1]})

    # Optional disk post-check (the edit-feature case): the change must land.
    pc = case.get("post_check")
    if pc:
        score, diag = _check_disk(pc)
        rows.append({**common, "metric": "post_check", "objective": True, "score": score, "diagnostic": diag})

    return rows, len(trace.calls)


def _check_disk(pc: Dict[str, Any]) -> Tuple[float, str]:
    """Assert a file under the eval repos dir exists and contains a marker string."""
    from app.config import settings
    path = os.path.join(settings.repos_base_path, DEFAULT_REPO_NAME, pc["file"])
    if not os.path.isfile(path):
        return 0.0, f"file not created: {pc['file']}"
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    marker = pc.get("contains", "")
    return (1.0, f"file contains {marker!r}") if marker in text else (0.0, f"missing {marker!r} in {pc['file']}")


def _objective_sum(rows: List[Dict[str, Any]]) -> float:
    return sum(r["score"] for r in rows if r.get("objective"))


async def _run_case(
    case: Dict[str, Any],
    *,
    mcp_manager: Any = None,
    codegraph_err: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run a case; retry once and keep the better-scoring run (Bedrock is noisy)."""
    kw = {"mcp_manager": mcp_manager, "codegraph_err": codegraph_err}
    rows, _ = await _attempt(case, **kw)
    obj = [r for r in rows if r.get("objective")]
    if obj and (_objective_sum(rows) < len(obj)):  # any objective miss → retry once
        retry_rows, _ = await _attempt(case, **kw)
        if _objective_sum(retry_rows) > _objective_sum(rows):
            rows = retry_rows
        for r in rows:
            r["retried"] = True
    return rows


async def _start_codegraph(cases: List[Dict[str, Any]]) -> Tuple[Any, Optional[str]]:
    """Connect the in-process codegraph engine and index the fixture ONCE.

    Returns ``(manager, error)`` — ``error`` non-None means the code cases can't
    run (caller fails them loud). Indexing is shared across every code case:
    the fixture is identical for all of them, and ``_index_and_project`` blocks
    until the index is built, so no case races a half-built index.
    """
    if not any(c.get("crawler") for c in cases):
        return None, None

    from app.services.mcp_client_manager import MCPClientManager
    from app.workflow.tools.codegraph_tools import (
        CODEGRAPH_SERVER_ID, codegraph_inline_config,
    )
    from evals.accuracy.run_codegraph import _index_and_project

    manager = MCPClientManager()
    try:
        if not await manager.connect_server(CODEGRAPH_SERVER_ID, codegraph_inline_config()):
            return manager, (
                "codegraph engine failed to start — the code cases run IN-CONTAINER "
                "only (the binary is baked into the agent-api image)"
            )
        _project, index_err = await _index_and_project(manager, CODEGRAPH_SERVER_ID)
        return manager, index_err
    except Exception as exc:  # noqa: BLE001 — a dead engine is a red suite, not a crash
        return manager, f"{type(exc).__name__}: {exc}"


async def run_trajectory_suite() -> List[Dict[str, Any]]:
    """Run the trajectory suite."""
    cases = _load_cases()
    manager, codegraph_err = await _start_codegraph(cases)
    if codegraph_err:
        logger.error("trajectory: codegraph unavailable — code cases will fail: %s",
                     codegraph_err)
    out: List[Dict[str, Any]] = []
    try:
        for case in cases:
            out.extend(await _run_case(
                case, mcp_manager=manager, codegraph_err=codegraph_err))
    finally:
        if manager is not None:
            try:
                await manager.disconnect_all()
            except Exception:  # noqa: BLE001 — teardown must not mask results
                logger.warning("trajectory: codegraph disconnect failed", exc_info=True)
    return out


if __name__ == "__main__":
    import asyncio
    rows = asyncio.run(run_trajectory_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else ("~~ " if r["score"] >= 0.5 else "XX ")
        rt = " (retried)" if r.get("retried") else ""
        print(f"{flag}{r['id']:<26} {r['metric']:<15} {r['score']:.2f}  {r['diagnostic']}{rt}")
    obj = [r for r in rows if r.get("objective")]
    mean = sum(r["score"] for r in obj) / len(obj) if obj else 0.0
    print(f"\ntrajectory mean OBJECTIVE score: {mean:.4f}  ({len(obj)} checks)")
