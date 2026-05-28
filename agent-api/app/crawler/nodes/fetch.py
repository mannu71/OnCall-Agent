"""FetchRepo node — crawls local repo files into shared state."""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.engine.crawler_engine import AsyncNode

logger = logging.getLogger(__name__)


class FetchRepo(AsyncNode):
    """Crawl *repo* under REPOS_BASE_PATH and populate ``shared["files"]``.

    Reads from shared:
        repo              (str) — repository name
        include_patterns  (set, optional)
        exclude_patterns  (set, optional)
        max_file_size     (int, optional)

    Writes to shared:
        files             list[(relative_path, content)]
        files_sha256      aggregate sha256 of all file paths+contents
        _trace            appends one entry
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "include": shared.get("include_patterns"),
            "exclude": shared.get("exclude_patterns"),
            "max_size": shared.get("max_file_size", 100_000),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> List[Tuple[str, str]]:
        from app.crawler.files import crawl_local_files

        t0 = time.monotonic()
        files = await crawl_local_files(
            repo_name=prep_res["repo"],
            include_patterns=prep_res["include"],
            exclude_patterns=prep_res["exclude"],
            max_file_size=prep_res["max_size"],
        )
        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        return files

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: List[Tuple[str, str]],
    ) -> Optional[str]:
        shared["files"] = exec_res

        # Aggregate hash for skip-if-unchanged in indexFlow
        h = hashlib.sha256()
        for path, content in exec_res:
            h.update(path.encode())
            h.update(content[:512].encode())
        shared["files_sha256"] = h.hexdigest()

        _append_trace(shared, "FetchRepo", len(exec_res), 0, 0, False,
                      ms=prep_res.get("_ms", 0))
        logger.info(
            "FetchRepo: %d files (sha256=%s…)", len(exec_res), shared["files_sha256"][:8]
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
