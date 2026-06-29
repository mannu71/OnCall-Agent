"""Run the SAME crawler case set against the codegraph engine and grade it.

This is the codegraph half of the A/B harness. It reuses the AST-derived ground
truth (``build_crawler_cases.derive_crawler_cases``) and the deterministic
graders (``graders.grade_find / grade_body / grade_trace``) unchanged — the only
work here is driving codegraph and normalizing its tool output into the shapes
the graders expect. Same truth + same graders ⇒ scores directly comparable to
``run_crawler.run_crawler_suite``.

codegraph is driven in-process exactly as it ships: an inline-config stdio MCP
session (``codegraph serve``), never a registered ``mcp_servers`` row.

Run inside the agent-api container (the codegraph binary + languages.so are baked
into the image):
    python -m evals.accuracy.run_codegraph
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy import graders
from evals.accuracy.build_crawler_cases import (
    DEFAULT_REPO_NAME, SAMPLE_REPO_DIR, derive_crawler_cases,
)
from evals.accuracy.run_crawler import _sync_fixture_repo


def _result_text(result: Dict[str, Any]) -> str:
    """Flatten an MCPClientManager.execute_tool result to its text payload."""
    if result.get("isError"):
        # surface the error text so callers can see it
        content = result.get("content") or result.get("error") or ""
    else:
        content = result.get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _result_json(result: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the tool's text payload as JSON; {} on failure."""
    text = _result_text(result)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"_raw": obj}
    except Exception:  # noqa: BLE001
        return {}


def _approx_tokens(result: Dict[str, Any]) -> int:
    """Approximate token cost of a tool result (chars ÷ 4)."""
    return max(0, len(_result_text(result)) // 4)


# ── grader-shape normalizers ────────────────────────────────────────────────

def _norm_find(payload: Dict[str, Any]) -> Dict[str, Any]:
    """codegraph find_symbol {symbols:[{name,label,file,line_start,...}]} →
    grade_find shape {found, results:[{file,line,kind}]}."""
    syms = payload.get("symbols") or []
    results = [
        {
            "file": s.get("file", ""),
            "line": s.get("line_start"),
            "kind": str(s.get("label", "")).lower(),
        }
        for s in syms
    ]
    return {"found": bool(results), "results": results}


def _norm_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    """codegraph get_code_snippet {start_line,end_line} → grade_body shape."""
    return {
        "handle_line_start": payload.get("start_line"),
        "handle_line_end": payload.get("end_line"),
    }


def _norm_trace_callees(payload: Dict[str, Any], caller: str) -> Dict[str, Any]:
    """codegraph trace_path(outbound) {callees:[{name,...}]} → grade_trace shape
    {edges:[{from,to}]}. Hop-1 callees only (depth=1) become caller→callee edges."""
    callees = payload.get("callees") or []
    edges = [
        {"from": caller, "to": c.get("name", "")}
        for c in callees
        if c.get("name") and c.get("name") != caller
    ]
    return {"edges": edges}


# ── codegraph driver ────────────────────────────────────────────────────────

async def _index_and_project(manager: Any, server_id: str) -> Tuple[str, Optional[str]]:
    """Index the fixture into codegraph; return (project_name, error)."""
    from app.config import settings

    base = _sync_fixture_repo()
    settings.repos_base_path = base
    repo_path = os.path.join(base, DEFAULT_REPO_NAME)
    res = await manager.execute_tool(
        server_id=server_id,
        tool_name="index_repository",
        arguments={"repo_path": repo_path, "mode": "full"},
        tool_timeout=0,
    )
    if res.get("isError"):
        return "", _result_text(res)[:300]
    payload = _result_json(res)
    # codegraph derives the project name from the path basename.
    project = payload.get("project") or DEFAULT_REPO_NAME
    return project, None


async def run_codegraph_suite() -> List[Dict[str, Any]]:
    """Run find/body/trace over the fixture via codegraph and grade each case."""
    from app.services.mcp_client_manager import MCPClientManager
    from app.workflow.tools.codegraph_tools import (
        CODEGRAPH_SERVER_ID, codegraph_inline_config,
    )

    out: List[Dict[str, Any]] = []
    manager = MCPClientManager()
    connected = False
    index_err: Optional[str] = None
    project = DEFAULT_REPO_NAME
    try:
        connected = await manager.connect_server(
            CODEGRAPH_SERVER_ID, codegraph_inline_config()
        )
        if not connected:
            index_err = "codegraph engine failed to start"
        else:
            project, index_err = await _index_and_project(manager, CODEGRAPH_SERVER_ID)

        cases = derive_crawler_cases()
        for c in cases:
            op = c["op"]
            args = c["args"]
            t0 = time.time()
            tokens = 0
            try:
                if index_err:
                    score, diag, res = 0.0, f"index error: {index_err}", {}
                elif op == "find":
                    res = await manager.execute_tool(
                        server_id=CODEGRAPH_SERVER_ID, tool_name="find_symbol",
                        arguments={"project": project, "name": args["symbol"]})
                    tokens = _approx_tokens(res)
                    score, diag = graders.grade_find(
                        _norm_find(_result_json(res)), c["expected"])
                elif op == "body":
                    res = await manager.execute_tool(
                        server_id=CODEGRAPH_SERVER_ID, tool_name="get_code_snippet",
                        arguments={"project": project, "qualified_name": args["symbol"]})
                    tokens = _approx_tokens(res)
                    score, diag = graders.grade_body(
                        _norm_body(_result_json(res)), c["expected"])
                elif op == "trace":
                    res = await manager.execute_tool(
                        server_id=CODEGRAPH_SERVER_ID, tool_name="trace_path",
                        arguments={"project": project, "function_name": args["symbol"],
                                   "direction": "outbound",
                                   "depth": args.get("depth", 1)})
                    tokens = _approx_tokens(res)
                    score, diag = graders.grade_trace(
                        _norm_trace_callees(_result_json(res), args["symbol"]),
                        c["expected_edges"])
                else:
                    score, diag, res = 0.0, f"unknown op {op}", {}
            except Exception as exc:  # noqa: BLE001
                score, diag = 0.0, f"exception: {type(exc).__name__}: {exc}"
            out.append({
                "feature": "codegraph",
                "id": c["id"],
                "op": op,
                "metric": op,
                "score": score,
                "diagnostic": diag,
                "latency_s": round(time.time() - t0, 3),
                "result_tokens": tokens,
                "index_error": index_err,
            })
    finally:
        if connected:
            try:
                await manager.disconnect_all()
            except Exception:  # noqa: BLE001
                pass
    return out


if __name__ == "__main__":
    import asyncio
    rows = asyncio.run(run_codegraph_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else "XX "
        print(f"{flag}{r['id']:<28} {r['score']:.2f}  {r['diagnostic']}")
    mean = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    print(f"\ncodegraph mean deterministic score: {mean:.4f}  ({len(rows)} cases)")
