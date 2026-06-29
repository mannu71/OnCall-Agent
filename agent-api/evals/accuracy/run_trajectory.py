"""Agent-trajectory eval — run the REAL ReAct agent and grade what it DID.

The crawler/CloudWatch suites grade tools in isolation. This suite closes the
gap that actually leaks real-world accuracy: given a question and a set of
attached tools, does the agent reach for the right tool family, follow the
locate→read→trace / drill-don't-rescan protocol, and state a correct,
ID-grounded answer?

Hermetic setup:
  * CloudWatch is faked at the data layer (``fakes_cloudwatch.patched_cloudwatch``)
    so the real tool code runs over recorded fixtures — no live AWS.
  * The crawler runs FOR REAL against ``fixtures/sample_repo`` (reusing
    ``run_crawler._ensure_indexed``).
  * The agent LLM is real Bedrock (same judge-calibrated path as the other
    suites); ``temperature`` is pinned low for repeatability.

Grading is deterministic (no LLM judge in the headline): predicate sets over the
tool-call trajectory and substring checks over the final answer. Because the
agent LLM is non-deterministic, a case that misses on the first run is retried
once and the better-scoring run is kept (``retried`` flagged on its rows).

This deliberately bypasses ``assemble_base_tools`` / the tool router (a hermetic
run can't do the STS credential pre-flight) — the router is covered by
``evals.harness_selftest``. We build the same tool families the strategy would
and drive the same ``build_agent_from_spec`` → ``execute_agent`` path.

Run:  python -m evals.accuracy.run_trajectory
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

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
        if orig is None:  # sync / non-structured tool — leave untouched
            return tool

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


async def _attempt(case: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """Run one agent trajectory for *case*; return (rows, tool_call_count)."""
    from app.harness import build_agent_from_spec, AgentSpec
    from app.workflow.strategies.react.hitl import make_checkpointer
    from app.workflow.strategies.react.agent_runner import execute_agent

    trace = ToolTrace()
    recording: Optional[FakeCloudWatchRecording] = None
    cm: Any = contextlib.nullcontext()
    if case.get("cw_fixture"):
        recording = FakeCloudWatchRecording.load(case["cw_fixture"])
        cm = patched_cloudwatch(recording)

    with cm:
        tools: List[Any] = []
        if case.get("cw_fixture"):
            from app.workflow.tools.cloudwatch_agent_tools import build_cloudwatch_agent_tools
            tools += build_cloudwatch_agent_tools(
                region="us-east-1", credentials={}, log_groups=case.get("log_groups"))

        code_cfg: Optional[Dict[str, Any]] = None
        if case.get("crawler"):
            from evals.accuracy.run_crawler import _ensure_indexed
            from app.workflow.tools.code_analyzer_tools import build_crawler_tools
            await _ensure_indexed()
            code_cfg = {"repos": [{"name": DEFAULT_REPO_NAME}]}
            tools += build_crawler_tools(repos=code_cfg["repos"])

        llm = await _build_agent_llm()

        if code_cfg:
            from app.harness.tool_assembler import add_extension_tools
            tools = add_extension_tools(
                tools=tools, llm=llm, agent_config={}, code_analyzer_config=code_cfg,
                execution_id=case["id"], logger_instance=logger)

        tools = trace.wrap(tools)

        spec = AgentSpec(
            agent_config={}, has_cloudwatch=bool(case.get("cw_fixture")),
            has_code_analyzer=bool(code_cfg), permission_mode="auto_allow",
            session_id=case["id"])
        checkpointer = await make_checkpointer()
        agent = build_agent_from_spec(spec, llm, tools, checkpointer=checkpointer)

        query = case["question"]
        if case.get("precomputed_block") and recording is not None:
            from app.harness.context_builder import seed_context_blocks
            query, _ = seed_context_blocks(
                augmented_query=query,
                context={"cloudwatch_context": {"node": {"output": recording.synthesis}}})

        t0 = time.time()
        try:
            result = await execute_agent(
                agent, query, logger, execution_id=case["id"], thread_id=case["id"])
        except Exception as exc:  # noqa: BLE001 — a crashed run is a 0, not a stack trace
            latency = round(time.time() - t0, 3)
            rows = [{"feature": "trajectory", "id": case["id"], "metric": m, "objective": True,
                     "score": 0.0, "diagnostic": f"exception: {type(exc).__name__}: {exc}",
                     "latency_s": latency, "tool_calls_n": len(trace.calls)}
                    for m in ("tool_selection", "protocol", "final_answer", "grounding")]
            return rows, len(trace.calls)

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

    common = {"feature": "trajectory", "id": case["id"], "latency_s": latency,
              "tool_calls_n": len(trace.calls)}
    rows: List[Dict[str, Any]] = [
        {**common, "metric": "tool_selection", "objective": True, "score": sel[0], "diagnostic": sel[1]},
        {**common, "metric": "protocol", "objective": True, "score": pro[0], "diagnostic": pro[1]},
        {**common, "metric": "final_answer", "objective": True, "score": ans[0], "diagnostic": ans[1]},
        {**common, "metric": "grounding", "objective": True, "score": grd[0], "diagnostic": grd[1]},
    ]

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


async def _run_case(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run a case; retry once and keep the better-scoring run (Bedrock is noisy)."""
    rows, _ = await _attempt(case)
    obj = [r for r in rows if r.get("objective")]
    if obj and (_objective_sum(rows) < len(obj)):  # any objective miss → retry once
        retry_rows, _ = await _attempt(case)
        if _objective_sum(retry_rows) > _objective_sum(rows):
            rows = retry_rows
        for r in rows:
            r["retried"] = True
    return rows


async def run_trajectory_suite() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for case in _load_cases():
        out.extend(await _run_case(case))
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
