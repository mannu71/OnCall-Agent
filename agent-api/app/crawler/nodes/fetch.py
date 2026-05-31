"""FetchRepo node — crawls local repo files into shared state."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.engine.crawler_engine import AsyncNode

logger = logging.getLogger(__name__)


class FetchRepo(AsyncNode):
    """Crawl *repo* under REPOS_BASE_PATH and populate ``shared["file_paths"]``.

    File bodies are **not** loaded into memory here — downstream nodes read
    content on demand via :func:`app.crawler.files.read_repo_file`.

    Reads from shared:
        repo              (str) — repository name
        include_patterns  (set, optional)
        exclude_patterns  (set, optional)
        max_file_size     (int, optional)

    Writes to shared:
        file_paths        list[str] — relative paths only
        files             [] — legacy key kept empty for backward compatibility
        files_sha256      aggregate sha256 of path + content prefixes
        _trace            appends one entry
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "include": shared.get("include_patterns"),
            "exclude": shared.get("exclude_patterns"),
            "max_size": shared.get("max_file_size", 100_000),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.files import compute_files_sha256, crawl_file_paths

        t0 = time.monotonic()
        paths = await crawl_file_paths(
            repo_name=prep_res["repo"],
            include_patterns=prep_res["include"],
            exclude_patterns=prep_res["exclude"],
            max_file_size=prep_res["max_size"],
        )
        files_sha256 = await compute_files_sha256(prep_res["repo"], paths)
        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        return {"paths": paths, "files_sha256": files_sha256}

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["file_paths"] = exec_res["paths"]
        shared["files"] = []
        shared["files_sha256"] = exec_res["files_sha256"]

        _append_trace(
            shared, "FetchRepo", len(exec_res["paths"]), 0, 0, False,
            ms=prep_res.get("_ms", 0),
        )
        logger.info(
            "FetchRepo: %d file paths (sha256=%s…)",
            len(exec_res["paths"]),
            shared["files_sha256"][:8],
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Shared trace helper used by all nodes
# ─────────────────────────────────────────────────────────────────────────────

def _append_trace(
    shared: Dict[str, Any],
    node_name: str,
    result_count: int,
    tokens_in: int,
    tokens_out: int,
    cached: bool,
    llm_calls: int = 0,
    ms: int = 0,
) -> None:
    """Append a trace entry to ``shared["_trace"]``."""
    if "_trace" not in shared:
        shared["_trace"] = []
    shared["_trace"].append({
        "node": node_name,
        "ms": ms,
        "ok": True,
        "result_count": result_count,
        "llm_calls": llm_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cached": cached,
    })
