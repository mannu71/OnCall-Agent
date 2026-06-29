"""Multi-repo crawler eval — verifies group-wide search labels hits by repo.

Deterministic (KG fast path only): copies the sample fixture under two repo
names, indexes both, then runs ``crawler_find_symbol`` with ``repos=[a, b]`` and
asserts each known symbol is found and labelled with the correct repo.

Run inside the container (needs DB + Bedrock for the index step):

    python -m evals.accuracy.run_crawler_multi
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy.build_crawler_cases import SAMPLE_REPO_DIR, derive_crawler_cases

REPO_A = "eval-multi-a"
REPO_B = "eval-multi-b"


def _sync_two_repos() -> str:
    base = os.getenv("EVAL_REPOS_DIR") or os.path.join(tempfile.gettempdir(), "eval_repos")
    for name in (REPO_A, REPO_B):
        target = os.path.join(base, name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(SAMPLE_REPO_DIR, target)
    return base


async def run_multi_suite() -> List[Dict[str, Any]]:
    from app.config import settings
    from app.services.crawler_flows import crawler_find_symbol, crawler_index_repo

    base = _sync_two_repos()
    settings.repos_base_path = base
    await crawler_index_repo(repo=REPO_A, force=True)
    await crawler_index_repo(repo=REPO_B, force=True)

    # Use the 'find' cases derived from the fixture as the symbol set.
    find_cases = [c for c in derive_crawler_cases() if c["op"] == "find"]
    out: List[Dict[str, Any]] = []
    for c in find_cases:
        sym = c["args"]["symbol"]
        t0 = time.time()
        try:
            res = await crawler_find_symbol(symbol=sym, repo=REPO_A, repos=[REPO_A, REPO_B], limit=10)
            results = res.get("results") or []
            repos_seen = {r.get("repo") for r in results if r.get("repo")}
            # Pass when the symbol is found and every labelled hit belongs to one
            # of the two scoped repos (and at least one repo label is present).
            ok = bool(results) and repos_seen.issubset({REPO_A, REPO_B}) and len(repos_seen) >= 1
            score = 1.0 if ok else 0.0
            diag = f"found={len(results)} repos_seen={sorted(repos_seen)}"
        except Exception as exc:  # noqa: BLE001
            score, diag = 0.0, f"exception: {type(exc).__name__}: {exc}"
        out.append({"feature": "crawler_multi", "id": f"multi::{sym}", "op": "find_multi",
                    "score": score, "diagnostic": diag, "latency_s": round(time.time() - t0, 3)})
    return out


if __name__ == "__main__":
    import asyncio
    rows = asyncio.run(run_multi_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else "XX "
        print(f"{flag}{r['id']:<34} {r['score']:.2f}  {r['diagnostic']}")
    mean = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    print(f"\ncrawler-multi mean score: {mean:.4f}  ({len(rows)} cases)")
