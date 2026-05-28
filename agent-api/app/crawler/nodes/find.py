"""findSymbolFlow nodes.

Pipeline: LookupOverview → NarrowToAbstractions → ScanFilesInScope
          → LLMExtractMatches → VerifyOnDisk → BuildFindResponse
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

_MAX_SCAN_FILES = 30           # cap how many files we hand to the LLM
_MAX_FILE_CHARS = 80_000       # per-file content truncation for the LLM prompt
_MAX_CONTEXT_CHARS = 160_000   # total context cap for the LLM prompt


# ─────────────────────────────────────────────────────────────────────────────
# Node 0: QueryKGForSymbol  (knowledge-graph fast path)
# ─────────────────────────────────────────────────────────────────────────────


class QueryKGForSymbol(AsyncNode):
    """Look up *symbol* directly in ``kg_nodes`` before falling back to LLM.

    If the knowledge graph already has a definition for the symbol we can
    return it instantly — exact file/line/signature, no LLM call, no
    hallucination.  When the graph misses we return action ``None`` so the
    flow falls through to the existing LLM-based pipeline.

    Reads from shared:
        repo, symbol, kind (optional), limit (default 5)

    Writes to shared on hit:
        _verified_matches  list (same shape VerifyOnDisk produces)
        _kg_hit            True
        _trace
    Returns action ``"skip_to_build"`` on hit, ``None`` otherwise.
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo":   shared["repo"],
            "symbol": shared["symbol"],
            "kind":   shared.get("kind"),
            "limit":  shared.get("limit", 5),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> List[Dict[str, Any]]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        t0 = time.monotonic()
        async with AsyncSessionLocal() as session:
            if prep_res["kind"]:
                sql = """
                    SELECT name, kind, qualified_name, file_path,
                           line_start, line_end, signature
                    FROM kg_nodes
                    WHERE repo_name = :r
                      AND name = :s
                      AND kind = :k
                    ORDER BY exported DESC, line_start ASC
                    LIMIT :lim
                """
                rows = await session.execute(
                    text(sql),
                    {"r": prep_res["repo"], "s": prep_res["symbol"],
                     "k": prep_res["kind"], "lim": prep_res["limit"]},
                )
            else:
                sql = """
                    SELECT name, kind, qualified_name, file_path,
                           line_start, line_end, signature
                    FROM kg_nodes
                    WHERE repo_name = :r AND name = :s
                    ORDER BY exported DESC, line_start ASC
                    LIMIT :lim
                """
                rows = await session.execute(
                    text(sql),
                    {"r": prep_res["repo"], "s": prep_res["symbol"], "lim": prep_res["limit"]},
                )

            results = rows.fetchall()

        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)

        matches: List[Dict[str, Any]] = []
        for row in results:
            name, kind, _qname, file_path, line_start, line_end, signature = row
            # Match VerifyOnDisk shape so BuildFindResponse needs no changes.
            line_count_hint = max(line_end or line_start or 1, (line_start or 1) + 50)
            matches.append({
                "file":        file_path,
                "line":        int(line_start or 1),
                "kind":        kind,
                "context":     (signature or "")[:200],
                "_abs_path":   "",
                "_line_count": line_count_hint,
            })
        return matches

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: List[Dict[str, Any]],
    ) -> Optional[str]:
        if exec_res:
            shared["_verified_matches"] = exec_res
            shared["_kg_hit"] = True
            _append_trace(
                shared, "QueryKGForSymbol",
                result_count=len(exec_res),
                tokens_in=0, tokens_out=0, cached=False,
                ms=prep_res.get("_ms", 0),
            )
            logger.info(
                "QueryKGForSymbol: HIT repo=%s symbol=%s → %d matches",
                shared.get("repo"), shared.get("symbol"), len(exec_res),
            )
            return "skip_to_build"

        shared["_kg_hit"] = False
        _append_trace(
            shared, "QueryKGForSymbol",
            result_count=0,
            tokens_in=0, tokens_out=0, cached=False,
            ms=prep_res.get("_ms", 0),
        )
        logger.info(
            "QueryKGForSymbol: MISS repo=%s symbol=%s → falling through to LLM",
            shared.get("repo"), shared.get("symbol"),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 1: LookupOverview
# ─────────────────────────────────────────────────────────────────────────────

class LookupOverview(AsyncNode):
    """Load the indexed overview for *repo* from ``repo_abstractions``."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text(
                    "SELECT overview FROM repo_abstractions WHERE repo_name = :repo"
                ),
                {"repo": prep_res["repo"]},
            )
            row = row.fetchone()

        if row is None:
            raise ValueError(
                f"LookupOverview: repo '{prep_res['repo']}' has not been indexed. "
                "Run indexFlow first."
            )

        overview = row[0]  # JSONB already decoded to dict
        return {
            "abstractions": overview.get("abstractions", []),
            "file_map": overview.get("file_map", {}),
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_overview"] = exec_res
        _append_trace(shared, "LookupOverview", len(exec_res["abstractions"]), 0, 0, False)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 2: NarrowToAbstractions
# ─────────────────────────────────────────────────────────────────────────────

class NarrowToAbstractions(AsyncNode):
    """Select the abstractions most likely to contain the target symbol."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        overview = shared["_overview"]
        return {
            "symbol": shared["symbol"],
            "kind": shared.get("kind"),
            "abstractions": overview["abstractions"],
            "file_map": overview["file_map"],
            "repo": shared["repo"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        abstraction_text = "\n".join(
            f"- {a['name']}: {str(a.get('summary', ''))[:100]}"
            for a in prep_res["abstractions"]
        )
        kind_hint = f" (type: {prep_res['kind']})" if prep_res["kind"] else ""

        prompt = f"""Project: "{prep_res['repo']}"

Looking for symbol: "{prep_res['symbol']}"{kind_hint}

Available abstractions:
{abstraction_text}

Which abstractions are most likely to define or heavily use "{prep_res['symbol']}"?
List the abstraction names that should be searched, most relevant first.

Output YAML only:

```yaml
- Authentication Layer
- Data Access Layer
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            names = yaml.safe_load(yaml_str) or []
            if not isinstance(names, list):
                names = list(prep_res["file_map"].keys())
        except Exception:
            names = list(prep_res["file_map"].keys())

        # Resolve names → file paths, dedup, cap at _MAX_SCAN_FILES
        seen: set = set()
        candidate_files: List[str] = []
        for name in names:
            for path in prep_res["file_map"].get(name, []):
                if path not in seen:
                    seen.add(path)
                    candidate_files.append(path)

        # Fallback: if LLM returned nothing useful, use all mapped files
        if not candidate_files:
            for paths in prep_res["file_map"].values():
                for path in paths:
                    if path not in seen:
                        seen.add(path)
                        candidate_files.append(path)

        return {
            "candidate_files": candidate_files[:_MAX_SCAN_FILES],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_candidate_files"] = exec_res["candidate_files"]
        _append_trace(
            shared, "NarrowToAbstractions",
            len(exec_res["candidate_files"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.debug("NarrowToAbstractions: %d candidates", len(exec_res["candidate_files"]))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 3: ScanFilesInScope
# ─────────────────────────────────────────────────────────────────────────────

class ScanFilesInScope(AsyncNode):
    """Read candidate files from disk and build context for the LLM."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "candidate_files": shared["_candidate_files"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        repos_root = os.getenv("REPOS_BASE_PATH", "/tmp/indexed_repos")
        repo_dir = os.path.join(repos_root, prep_res["repo"])

        def _read_files() -> List[Tuple[str, str]]:
            results = []
            for relpath in prep_res["candidate_files"]:
                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        content = f.read()
                    results.append((relpath, content))
                except PathJailError:
                    logger.warning("Path jail violation: %s", abs_path)
                except FileNotFoundError:
                    logger.debug("File not found: %s", abs_path)
                except Exception as exc:
                    logger.debug("Cannot read %s: %s", abs_path, exc)
            return results

        t0 = time.monotonic()
        file_contents = await asyncio.to_thread(_read_files)
        ms = int((time.monotonic() - t0) * 1000)

        # Build LLM context, capped at _MAX_CONTEXT_CHARS
        parts: List[str] = []
        total = 0
        included: List[str] = []
        for relpath, content in file_contents:
            truncated = content[:_MAX_FILE_CHARS]
            entry = f"--- File: {relpath} ---\n{truncated}\n\n"
            if total + len(entry) > _MAX_CONTEXT_CHARS:
                break
            parts.append(entry)
            included.append(relpath)
            total += len(entry)

        return {
            "file_context": "".join(parts),
            "included_files": included,
            "ms": ms,
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_file_context"] = exec_res["file_context"]
        shared["_included_files"] = exec_res["included_files"]
        _append_trace(shared, "ScanFilesInScope", len(exec_res["included_files"]),
                      0, 0, False, ms=exec_res["ms"])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 4: LLMExtractMatches
# ─────────────────────────────────────────────────────────────────────────────

class LLMExtractMatches(AsyncNode):
    """Ask the LLM to locate the symbol definition(s) in the file context."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "symbol": shared["symbol"],
            "kind": shared.get("kind"),
            "limit": shared.get("limit", 5),
            "file_context": shared["_file_context"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        kind_hint = f" of type '{prep_res['kind']}'" if prep_res["kind"] else ""
        limit = prep_res["limit"]

        prompt = f"""Project: "{prep_res['repo']}"

Find the definition(s) of symbol "{prep_res['symbol']}"{kind_hint}.

File contents:
{prep_res['file_context']}

For each match provide the exact file path and line number where the symbol is defined or declared.
Return up to {limit} results, most precise definition first.

Output YAML only, no other text:

```yaml
- file: path/to/file.py
  line: 42
  kind: function
  context: "def my_function(arg1, arg2):"
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=self.cur_retry == 0
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            matches = yaml.safe_load(yaml_str) or []
            if not isinstance(matches, list):
                matches = []
        except Exception as exc:
            raise ValueError(
                f"LLMExtractMatches: cannot parse YAML: {exc}\nRaw: {response[:400]}"
            )

        return {
            "matches": matches,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_raw_matches"] = exec_res["matches"]
        _append_trace(
            shared, "LLMExtractMatches",
            len(exec_res["matches"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.debug("LLMExtractMatches: %d raw matches", len(exec_res["matches"]))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 5: VerifyOnDisk
# ─────────────────────────────────────────────────────────────────────────────

class VerifyOnDisk(AsyncNode):
    """Hard gate: open each LLM-cited (file, line) and confirm it exists on disk.

    Only matches whose file exists and whose line number is within range are
    forwarded.  LLM hallucinations are silently dropped.
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "raw_matches": shared["_raw_matches"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        repos_root = os.getenv("REPOS_BASE_PATH", "/tmp/indexed_repos")
        repo_dir = os.path.join(repos_root, prep_res["repo"])

        def _verify() -> List[Dict[str, Any]]:
            verified = []
            for match in prep_res["raw_matches"]:
                relpath = str(match.get("file", "")).strip()
                line_no = match.get("line")
                if not relpath or line_no is None:
                    continue
                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        lines = f.readlines()
                    if not (1 <= int(line_no) <= len(lines)):
                        logger.debug(
                            "VerifyOnDisk: line %d out of range in %s (%d lines)",
                            line_no, relpath, len(lines),
                        )
                        continue
                    verified.append({
                        **match,
                        "file": relpath,
                        "line": int(line_no),
                        "_abs_path": abs_path,
                        "_line_count": len(lines),
                    })
                except PathJailError:
                    logger.debug("VerifyOnDisk: path jail %s", abs_path)
                except FileNotFoundError:
                    logger.debug("VerifyOnDisk: file not found %s", abs_path)
                except Exception as exc:
                    logger.debug("VerifyOnDisk: %s → %s", relpath, exc)
            return verified

        t0 = time.monotonic()
        verified = await asyncio.to_thread(_verify)
        ms = int((time.monotonic() - t0) * 1000)
        return {"verified": verified, "ms": ms}

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_verified_matches"] = exec_res["verified"]
        _append_trace(shared, "VerifyOnDisk", len(exec_res["verified"]),
                      0, 0, False, ms=exec_res["ms"])
        logger.info(
            "VerifyOnDisk: %d/%d matches verified",
            len(exec_res["verified"]), len(prep_res["raw_matches"]),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 6: BuildFindResponse
# ─────────────────────────────────────────────────────────────────────────────

class BuildFindResponse(AsyncNode):
    """Assemble the final find response and attach body handles."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "symbol": shared["symbol"],
            "verified": shared["_verified_matches"],
            "limit": shared.get("limit", 5),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.handles import make_body_handle as _make_body_handle

        results = []
        for match in prep_res["verified"][: prep_res["limit"]]:
            relpath = match["file"]
            line_no = int(match["line"])
            line_count = match.get("_line_count", line_no)

            # Window of ~40 lines centred on the definition
            window_start = max(1, line_no - 2)
            window_end = min(line_count, line_no + 37)
            handle = _make_body_handle(
                prep_res["repo"], relpath, window_start, window_end
            )

            results.append({
                "file": relpath,
                "line": line_no,
                "kind": match.get("kind", "unknown"),
                "context": str(match.get("context", ""))[:200],
                "body_handle": handle,
                "confidence": "verified",
            })

        return {"results": results, "found": len(results) > 0}

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["response"] = {
            "symbol": prep_res["symbol"],
            "repo": prep_res["repo"],
            "found": exec_res["found"],
            "results": exec_res["results"],
            "count": len(exec_res["results"]),
        }
        _append_trace(shared, "BuildFindResponse", len(exec_res["results"]), 0, 0, False)
        return None
