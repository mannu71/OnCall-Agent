"""Run the CodeCrawler suite against the fixture repo and grade deterministically.

Flow per run:
  1. copy fixtures/sample_repo -> <writable repos dir>/eval-fixture-repo
  2. point settings.repos_base_path at that writable dir (avoids the container's
     read-only indexed_repos mount)
  3. index the fixture (force) — the only step that needs Bedrock
  4. for each derived case run find / trace / body and score via graders
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy import graders
from evals.accuracy.build_crawler_cases import (
    DEFAULT_REPO_NAME, SAMPLE_REPO_DIR, derive_crawler_cases,
)


def _sync_fixture_repo() -> str:
    """Copy the fixture into a writable repos dir; return that dir (repos_base_path)."""
    base = os.getenv("EVAL_REPOS_DIR") or os.path.join(tempfile.gettempdir(), "eval_repos")
    target = os.path.join(base, DEFAULT_REPO_NAME)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(SAMPLE_REPO_DIR, target)
    return base


async def _ensure_indexed() -> Dict[str, Any]:
    from app.config import settings
    from app.services.crawler_flows import crawler_index_repo

    base = _sync_fixture_repo()
    settings.repos_base_path = base  # consumed live by app.crawler.files
    res = await crawler_index_repo(repo=DEFAULT_REPO_NAME, force=True)
    return res


async def run_crawler_suite() -> List[Dict[str, Any]]:
    from app.services.crawler_flows import (
        crawler_find_symbol, crawler_get_body, crawler_trace_path,
    )

    index_res = await _ensure_indexed()
    index_err = index_res.get("error")
    cases = derive_crawler_cases()
    out: List[Dict[str, Any]] = []

    for c in cases:
        op = c["op"]
        args = c["args"]
        t0 = time.time()
        try:
            if op == "find":
                res = await crawler_find_symbol(
                    symbol=args["symbol"], repo=args["repo"], kind=args.get("kind"), limit=5)
                score, diag = graders.grade_find(res, c["expected"])
            elif op == "body":
                found = await crawler_find_symbol(
                    symbol=args["symbol"], repo=args["repo"], limit=5)
                handle = None
                for r in (found.get("results") or []):
                    if graders.path_matches(str(r.get("file", "")), c["expected"]["file"]):
                        handle = r.get("body_handle")
                        break
                if not handle:
                    score, diag = 0.0, "no body_handle from find"
                    res = found
                else:
                    res = await crawler_get_body(handle=handle)
                    score, diag = graders.grade_body(res, c["expected"])
            elif op == "trace":
                res = await crawler_trace_path(
                    symbol=args["symbol"], repo=args["repo"],
                    direction=args.get("direction", "callees"), depth=args.get("depth", 1))
                score, diag = graders.grade_trace(res, c["expected_edges"])
            else:
                score, diag, res = 0.0, f"unknown op {op}", {}
        except Exception as exc:  # noqa: BLE001
            score, diag, res = 0.0, f"exception: {type(exc).__name__}: {exc}", {}
        out.append({
            "feature": "crawler",
            "id": c["id"],
            "op": op,
            "metric": op,
            "score": score,
            "diagnostic": diag,
            "latency_s": round(time.time() - t0, 3),
            "index_error": index_err,
        })
    return out


if __name__ == "__main__":
    import asyncio
    rows = asyncio.run(run_crawler_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else "XX "
        print(f"{flag}{r['id']:<28} {r['score']:.2f}  {r['diagnostic']}")
    mean = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    print(f"\ncrawler mean deterministic score: {mean:.4f}  ({len(rows)} cases)")
