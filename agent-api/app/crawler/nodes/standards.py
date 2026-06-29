"""Coding-standards extraction node for indexFlow.

``ExtractStandards`` derives a per-repo conventions profile (naming, file/folder
layout, framework idioms, error handling, testing conventions, and the
frontend-vs-backend split) from the knowledge graph's signature digest plus a
small sample of representative source. Stored as the ``standards`` row in
``repo_docs`` and exposed to agents via ``crawler_coding_standards`` so generated
code matches the project. Best-effort, like the other intelligence nodes.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace
from app.crawler.nodes.intelligence import (
    upsert_repo_doc,
    parse_yaml_block,
    _load_kg_for_intelligence,
    _signatures_block,
)

logger = logging.getLogger(__name__)

_MAX_DIRS = 12          # sample signatures from this many directories
_SAMPLE_FILES = 8       # excerpt this many representative files
_FILE_CHARS = 1_500
_CTX_CHARS = 14_000


class ExtractStandards(AsyncNode):
    """LLM: produce the repo's coding-conventions profile."""

    def __init__(self, max_retries: int = 2, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.crawler.files import read_repo_files_bounded
        from sqlalchemy import text

        repo = shared["repo"]
        force = bool(shared.get("force"))

        async with AsyncSessionLocal() as session:
            standards_exists = (await session.execute(
                text("SELECT 1 FROM repo_docs WHERE repo_name = :r AND doc_type = 'standards'"),
                {"r": repo},
            )).first() is not None

        skip = (not force) and standards_exists and shared.get("_modules_generated", 0) == 0

        signatures = ""
        excerpts = ""
        if not skip:
            by_module, _sha = await _load_kg_for_intelligence(repo)
            # Signature digest grouped by the largest directories.
            top_dirs = sorted(by_module.items(), key=lambda t: len(t[1]), reverse=True)[:_MAX_DIRS]
            sig_parts = [f"## {d}\n{_signatures_block(nodes, limit=25)}" for d, nodes in top_dirs]
            signatures = "\n\n".join(sig_parts)

            # One representative file from each of the top directories.
            rep_files: List[str] = []
            for _d, nodes in top_dirs:
                files = sorted({n["file"] for n in nodes})
                if files:
                    rep_files.append(files[0])
                if len(rep_files) >= _SAMPLE_FILES:
                    break
            total = 0
            for path, content in await read_repo_files_bounded(repo, rep_files):
                chunk = f"--- {path} ---\n{content[:_FILE_CHARS]}\n\n"
                if total + len(chunk) > _CTX_CHARS:
                    break
                excerpts += chunk
                total += len(chunk)

        return {
            "repo": repo,
            "model": shared.get("model_id"),
            "skip": skip,
            "signatures": signatures,
            "excerpts": excerpts,
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm

        if prep_res["skip"]:
            return {"written": 0, "tokens_in": 0, "tokens_out": 0, "ms": 0, "skipped": True}

        t0 = time.monotonic()
        repo = prep_res["repo"]
        prompt = f"""You are establishing the coding standards a new contributor to the
project "{repo}" must follow so their code blends in. Infer the conventions
ACTUALLY used from the evidence below — do not prescribe generic best practices.

Symbol signatures by directory:
{prep_res['signatures'] or '(none)'}

Representative source excerpts:
{prep_res['excerpts'] or '(none)'}

Output YAML only:

```yaml
naming:
  - observed naming convention (files, classes, functions, variables)
layout: |
  How the codebase is organised on disk and where new code of each kind belongs.
frameworks:
  - language_or_stack: the dominant idioms/patterns to follow in it
error_handling: |
  How errors/exceptions/logging are handled across the codebase.
testing: |
  Test framework, file locations, and naming conventions.
frontend_patterns: |
  Conventions for UI code (omit or 'n/a' if this repo has no frontend).
backend_patterns: |
  Conventions for server/API code (omit or 'n/a' if not applicable).
```"""
        resp, tin, tout, _cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True, tier="index",
        )
        parsed = parse_yaml_block(resp)
        written = 0
        if isinstance(parsed, dict):
            await upsert_repo_doc(
                repo, "standards", "",
                {
                    "naming": parsed.get("naming", []),
                    "layout": parsed.get("layout", ""),
                    "frameworks": parsed.get("frameworks", []),
                    "error_handling": parsed.get("error_handling", ""),
                    "testing": parsed.get("testing", ""),
                    "frontend_patterns": parsed.get("frontend_patterns", ""),
                    "backend_patterns": parsed.get("backend_patterns", ""),
                },
                model_id=prep_res["model"], tokens_in=tin, tokens_out=tout,
            )
            written = 1
        return {
            "written": written, "tokens_in": tin, "tokens_out": tout,
            "ms": int((time.monotonic() - t0) * 1000), "skipped": False,
        }

    async def post(self, shared, prep_res, exec_res) -> Optional[str]:
        _append_trace(
            shared, "ExtractStandards", exec_res["written"],
            exec_res["tokens_in"], exec_res["tokens_out"], False,
            llm_calls=0 if exec_res.get("skipped") else 1, ms=exec_res["ms"],
        )
        logger.info(
            "ExtractStandards: %s (repo=%s)",
            "skipped (no change)" if exec_res.get("skipped") else f"{exec_res['written']} doc written",
            shared.get("repo"),
        )
        return None


__all__ = ["ExtractStandards"]
