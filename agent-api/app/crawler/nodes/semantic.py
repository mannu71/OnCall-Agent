"""searchSemanticFlow nodes.

Pipeline: LookupOverview → LLMSuggestFiles → ReadFiles
          → LLMExtractMatches → VerifyOnDisk → BuildHitList

Answers freeform natural-language queries against an indexed repo.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

_MAX_SCAN_FILES = 25
_MAX_FILE_CHARS = 80_000
# Total LLM-prompt context cap comes from settings.crawler_search_context_max_chars.


# ─────────────────────────────────────────────────────────────────────────────
# Node 0: QueryKGForHits  (knowledge-graph fast path)
# ─────────────────────────────────────────────────────────────────────────────

class QueryKGForHits(AsyncNode):
    """Answer the query directly from ``kg_nodes`` before falling back to the LLM.

    Deterministic, disk-valid retrieval over the knowledge graph: rows are real
    indexed definitions, so there is no LLM guess and no on-disk verification
    gate (which previously rejected every LLM-proposed hit on large repos,
    yielding ``VerifyOnDisk: 0/N``). On a hit we populate ``_verified_hits``
    (BuildHitList's input shape) and skip the LLM pipeline; on a miss we fall
    through to the existing LLM-based flow so natural-language recall is kept.

    Reads shared:  repo, query, limit (default 10), kind (optional)
    Writes shared: _verified_hits, _kg_hit; returns ``"skip_to_build"`` on hit.
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo":  shared["repo"],
            "query": shared["query"],
            "limit": shared.get("limit", 10),
            "kind":  shared.get("kind"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> List[Dict[str, Any]]:
        from app.crawler.kg_search import kg_search

        t0 = time.monotonic()
        rows = await kg_search(
            repo=prep_res["repo"], query=prep_res["query"],
            limit=prep_res["limit"], kind=prep_res["kind"],
        )
        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        # Map kg_search rows → BuildHitList hit shape.
        return [
            {
                "file":        r["file"],
                "line":        r["line"],
                "snippet":     r["snippet"],
                "relevance":   f"{r['kind']} {r['name']}".strip(),
                "_line_count": r["_line_count"],
            }
            for r in rows
        ]

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: List[Dict[str, Any]]
    ) -> Optional[str]:
        if exec_res:
            shared["_verified_hits"] = exec_res
            shared["_kg_hit"] = True
            _append_trace(shared, "QueryKGForHits", len(exec_res), 0, 0, False,
                          ms=prep_res.get("_ms", 0))
            logger.info("QueryKGForHits: HIT repo=%s query=%r → %d hits",
                        shared.get("repo"), str(shared.get("query"))[:60], len(exec_res))
            return "skip_to_build"

        shared["_kg_hit"] = False
        _append_trace(shared, "QueryKGForHits", 0, 0, 0, False, ms=prep_res.get("_ms", 0))
        logger.info("QueryKGForHits: MISS repo=%s query=%r → LLM fallback",
                    shared.get("repo"), str(shared.get("query"))[:60])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 1: LookupOverview
# ─────────────────────────────────────────────────────────────────────────────

class LookupOverview(AsyncNode):
    """Load the indexed overview for *repo* from ``repo_abstractions``."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"repo": shared["repo"]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("SELECT overview FROM repo_abstractions WHERE repo_name = :repo"),
                {"repo": prep_res["repo"]},
            )
            row = row.fetchone()

        if row is None:
            raise ValueError(
                f"LookupOverview: repo '{prep_res['repo']}' has not been indexed. "
                "Run indexFlow first."
            )
        overview = row[0]
        return {
            "abstractions": overview.get("abstractions", []),
            "file_map": overview.get("file_map", {}),
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_overview"] = exec_res
        _append_trace(shared, "LookupOverview", len(exec_res["abstractions"]), 0, 0, False)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 2: LLMSuggestFiles
# ─────────────────────────────────────────────────────────────────────────────

class LLMSuggestFiles(AsyncNode):
    """Ask the LLM which files are most likely to answer the query."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        overview = shared["_overview"]
        abstraction_text = "\n".join(
            f"- {a['name']}: {str(a.get('summary', ''))[:100]}"
            for a in overview["abstractions"]
        )
        file_listing = "\n".join(
            f"- {path}"
            for paths in overview["file_map"].values()
            for path in paths
        )
        return {
            "repo": shared["repo"],
            "query": shared["query"],
            "abstraction_text": abstraction_text,
            "file_listing": file_listing,
            "file_map": overview["file_map"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        prompt = f"""Project: "{prep_res['repo']}"

Semantic query: "{prep_res['query']}"

Available abstractions:
{prep_res['abstraction_text']}

Available files:
{prep_res['file_listing'][:4000]}

Which files are most likely to contain code relevant to this query?
List the most relevant files first.

Output YAML only:

```yaml
- app/auth/login.py
- app/models/user.py
```"""

        # This node falls back gracefully on a parse miss (uses all mapped
        # files), so retries are transport-only — reading the cache on retry is
        # always safe here.
        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            files = yaml.safe_load(yaml_str) or []
            if not isinstance(files, list):
                files = []
            files = [str(f).strip() for f in files if f]
        except Exception:
            files = []

        # Fallback: use all mapped files
        if not files:
            seen: set = set()
            for paths in prep_res["file_map"].values():
                for p in paths:
                    if p not in seen:
                        seen.add(p)
                        files.append(p)

        return {
            "suggested_files": files[:_MAX_SCAN_FILES],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_suggested_files"] = exec_res["suggested_files"]
        _append_trace(
            shared, "LLMSuggestFiles",
            len(exec_res["suggested_files"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.debug("LLMSuggestFiles: %d files", len(exec_res["suggested_files"]))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 3: ReadFiles
# ─────────────────────────────────────────────────────────────────────────────

class ReadFiles(AsyncNode):
    """Read suggested files from disk and build LLM context."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "files": shared["_suggested_files"],
            "query": shared.get("query", ""),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        from app.config import settings
        repos_root = settings.repos_base_path
        repo_dir = os.path.join(repos_root, prep_res["repo"])

        def _read() -> List[Tuple[str, str]]:
            results = []
            for relpath in prep_res["files"]:
                if not relpath:
                    continue
                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        content = f.read()
                    results.append((relpath, content))
                except Exception as exc:
                    logger.debug("ReadFiles: skip %s — %s", relpath, exc)
            return results

        t0 = time.monotonic()
        file_contents = await asyncio.to_thread(_read)
        ms = int((time.monotonic() - t0) * 1000)

        from app.crawler.files import extract_snippets, derive_search_terms
        _ctx_cap = settings.crawler_search_context_max_chars
        terms = derive_search_terms(prep_res.get("query", ""))
        use_snippets = settings.crawler_snippet_extraction and bool(terms)

        parts: List[str] = []
        total = 0
        included: List[str] = []
        for relpath, content in file_contents:
            if use_snippets:
                body = extract_snippets(
                    content, terms,
                    window=settings.crawler_snippet_window,
                    max_chars=_MAX_FILE_CHARS,
                )
            else:
                body = content[:_MAX_FILE_CHARS]
            entry = f"--- File: {relpath} ---\n{body}\n\n"
            if total + len(entry) > _ctx_cap:
                break
            parts.append(entry)
            included.append(relpath)
            total += len(entry)

        context = "".join(parts)
        if use_snippets and context:
            context = (
                "NOTE: Each code line is prefixed with its 1-based file line "
                "number (`<n>: <code>`). Use these exact numbers for the 'line' "
                "field; `... [N lines omitted] ...` marks elided regions.\n\n"
            ) + context

        return {"file_context": context, "included": included, "ms": ms}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_semantic_context"] = exec_res["file_context"]
        shared["_included_files"] = exec_res["included"]
        _append_trace(shared, "ReadFiles", len(exec_res["included"]),
                      0, 0, False, ms=exec_res["ms"])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 4: LLMExtractMatches
# ─────────────────────────────────────────────────────────────────────────────

class LLMExtractMatches(AsyncNode):
    """Extract the most relevant code passages for the query."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "query": shared["query"],
            "limit": shared.get("limit", 10),
            "file_context": shared["_semantic_context"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        prompt = f"""Project: "{prep_res['repo']}"

Semantic query: "{prep_res['query']}"

File contents:
{prep_res['file_context']}

Find the {prep_res['limit']} most relevant code passages that best answer the query.
For each passage provide the exact file path, starting line, and a short snippet.

Output YAML only:

```yaml
- file: app/auth/login.py
  line: 45
  snippet: "def authenticate(user, password): ..."
  relevance: "Implements the core authentication logic"
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=self.cur_retry == 0
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            hits = yaml.safe_load(yaml_str) or []
            if not isinstance(hits, list):
                hits = []
        except Exception as exc:
            raise ValueError(
                f"LLMExtractMatches: cannot parse YAML: {exc}\nRaw: {response[:400]}"
            )

        return {
            "hits": hits,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_raw_hits"] = exec_res["hits"]
        _append_trace(
            shared, "LLMExtractMatches",
            len(exec_res["hits"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.debug("LLMExtractMatches: %d raw hits", len(exec_res["hits"]))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 5: VerifyOnDisk
# ─────────────────────────────────────────────────────────────────────────────

class VerifyOnDisk(AsyncNode):
    """Hard gate: confirm each hit's (file, line) exists on disk."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"repo": shared["repo"], "raw_hits": shared["_raw_hits"]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        from app.config import settings
        repos_root = settings.repos_base_path
        repo_dir = os.path.join(repos_root, prep_res["repo"])

        def _verify() -> List[Dict[str, Any]]:
            verified = []
            for hit in prep_res["raw_hits"]:
                relpath = str(hit.get("file", "")).strip()
                line_no = hit.get("line")
                if not relpath or line_no is None:
                    continue
                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        lines = f.readlines()
                    if 1 <= int(line_no) <= len(lines):
                        verified.append({
                            **hit,
                            "file": relpath,
                            "line": int(line_no),
                            "_line_count": len(lines),
                        })
                    else:
                        logger.debug("VerifyOnDisk: line %d out of range in %s", line_no, relpath)
                except PathJailError:
                    logger.debug("VerifyOnDisk: path jail %s", abs_path)
                except FileNotFoundError:
                    logger.debug("VerifyOnDisk: not found %s", abs_path)
                except Exception as exc:
                    logger.debug("VerifyOnDisk: %s → %s", relpath, exc)
            return verified

        t0 = time.monotonic()
        verified = await asyncio.to_thread(_verify)
        ms = int((time.monotonic() - t0) * 1000)
        return {"verified": verified, "ms": ms}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_verified_hits"] = exec_res["verified"]
        _append_trace(shared, "VerifyOnDisk", len(exec_res["verified"]),
                      0, 0, False, ms=exec_res["ms"])
        logger.info(
            "VerifyOnDisk: %d/%d hits verified",
            len(exec_res["verified"]), len(prep_res["raw_hits"]),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 6: BuildHitList
# ─────────────────────────────────────────────────────────────────────────────

class BuildHitList(AsyncNode):
    """Assemble the final semantic search response with body handles."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "query": shared["query"],
            "hits": shared["_verified_hits"],
            "limit": shared.get("limit", 10),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.handles import make_body_handle as _make_body_handle

        results = []
        for i, hit in enumerate(prep_res["hits"][: prep_res["limit"]]):
            relpath = hit["file"]
            line_no = int(hit["line"])
            line_count = hit.get("_line_count", line_no)
            window_end = min(line_count, line_no + 29)
            handle = _make_body_handle(
                prep_res["repo"], relpath, max(1, line_no - 2), window_end
            )
            results.append({
                "rank": i + 1,
                "file": relpath,
                "line": line_no,
                "snippet": str(hit.get("snippet", ""))[:300],
                "relevance": str(hit.get("relevance", ""))[:200],
                "body_handle": handle,
            })

        return {"hits": results, "count": len(results)}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["response"] = {
            "query": prep_res["query"],
            "repo": prep_res["repo"],
            "count": exec_res["count"],
            "hits": exec_res["hits"],
        }
        _append_trace(shared, "BuildHitList", exec_res["count"], 0, 0, False)
        return None
