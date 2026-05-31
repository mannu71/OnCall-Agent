"""Abstraction-extraction nodes for indexFlow.

Pipeline: ExtractAbstractions → AnalyzeRelationships → BuildFileMap → PersistOverview
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

# Keep individual files and total context small enough to stay under
# Bedrock's ApplyGuardrail per-second text-unit rate limit.
_MAX_FILE_CHARS = 3_000       # ~750 tokens per file — enough for signatures/structure
_MAX_CONTEXT_CHARS = 60_000   # ~15K tokens total prompt — well under guardrail threshold


def _build_file_context(files: List[Tuple[str, str]]) -> Tuple[str, str]:
    """Return (context_text, file_listing) for the LLM prompt."""
    parts: List[str] = []
    listing: List[str] = []
    total = 0
    for i, (path, content) in enumerate(files):
        truncated = content[:_MAX_FILE_CHARS]
        entry = f"--- File Index {i}: {path} ---\n{truncated}\n\n"
        if total + len(entry) > _MAX_CONTEXT_CHARS:
            break
        parts.append(entry)
        listing.append(f"- {i} # {path}")
        total += len(entry)
    return "".join(parts), "\n".join(listing)


async def _build_file_context_from_paths(
    repo: str,
    file_paths: List[str],
) -> Tuple[str, str, int]:
    """Load files on demand (bounded parallel) until context cap is reached."""
    from app.crawler.files import read_repo_files_bounded

    parts: List[str] = []
    listing: List[str] = []
    total = 0
    loaded = 0

    batch_size = 32
    for start in range(0, len(file_paths), batch_size):
        chunk_paths = file_paths[start:start + batch_size]
        for i, (path, content) in enumerate(
            await read_repo_files_bounded(repo, chunk_paths),
            start=start,
        ):
            truncated = content[:_MAX_FILE_CHARS]
            entry = f"--- File Index {i}: {path} ---\n{truncated}\n\n"
            if total + len(entry) > _MAX_CONTEXT_CHARS:
                return "".join(parts), "\n".join(listing), loaded
            parts.append(entry)
            listing.append(f"- {i} # {path}")
            total += len(entry)
            loaded += 1

    return "".join(parts), "\n".join(listing), loaded


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 of indexFlow: ExtractAbstractions
# ─────────────────────────────────────────────────────────────────────────────

class ExtractAbstractions(AsyncNode):
    """LLM call: identify 5–10 core abstractions from the crawled files."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        file_paths = shared.get("file_paths")
        if file_paths is None:
            file_paths = [p for p, _c in shared.get("files", [])]

        context, file_listing, loaded_count = await _build_file_context_from_paths(
            shared["repo"], file_paths,
        )
        return {
            "repo": shared["repo"],
            "context": context,
            "file_listing": file_listing,
            "file_count": loaded_count,
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm

        t0 = time.monotonic()
        prompt = f"""You are analysing the codebase of the project "{prep_res['repo']}".

Codebase context ({prep_res['file_count']} files):
{prep_res['context']}

File index listing:
{prep_res['file_listing']}

Identify the 5-10 most important core abstractions in this codebase.

For each abstraction provide:
- name: short descriptive name (3-6 words)
- summary: 2-3 sentence explanation of what it does and why it matters
- file_indices: list of integer file indices that implement this abstraction

Output YAML only, no other text:

```yaml
- name: Authentication Layer
  summary: |
    Handles user login, token issuance, and session management.
    All API endpoints that require login pass through this layer.
  file_indices: [0, 3, 7]
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=self.cur_retry == 0
        )

        ms = int((time.monotonic() - t0) * 1000)

        # Best-effort YAML parse — when the model refuses ("Sorry, ...") or
        # produces malformed output we fall back to an empty abstraction list
        # so the downstream KG nodes (which are pure static analysis) still
        # run.  The flow continues; repo_abstractions gets an empty overview.
        abstractions: Any = []
        try:
            if "```yaml" in response:
                yaml_str = response.split("```yaml")[1].split("```")[0].strip()
                parsed = yaml.safe_load(yaml_str)
                if isinstance(parsed, list):
                    abstractions = parsed
            elif response.strip().startswith("- "):
                parsed = yaml.safe_load(response)
                if isinstance(parsed, list):
                    abstractions = parsed
        except Exception as exc:
            logger.warning(
                "ExtractAbstractions: YAML parse failed (%s); continuing with empty abstractions. "
                "Raw[:200]=%r",
                exc, response[:200],
            )

        if not abstractions:
            logger.warning(
                "ExtractAbstractions: model returned no usable abstractions "
                "(likely a refusal / content filter). Continuing with []."
            )

        return {
            "abstractions": abstractions,
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
        shared["abstractions"] = exec_res["abstractions"]
        _append_trace(
            shared, "ExtractAbstractions",
            len(exec_res["abstractions"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.info(
            "ExtractAbstractions: %d abstractions (cached=%s)",
            len(exec_res["abstractions"]), exec_res["cached"],
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 of indexFlow: AnalyzeRelationships
# ─────────────────────────────────────────────────────────────────────────────

class AnalyzeRelationships(AsyncNode):
    """LLM call: map dependency edges between abstractions."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        abstraction_text = "\n".join(
            f"- {a['name']}: {str(a.get('summary', ''))[:120]}"
            for a in shared["abstractions"]
        )
        return {
            "repo": shared["repo"],
            "abstraction_text": abstraction_text,
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm

        t0 = time.monotonic()
        prompt = f"""Project: "{prep_res['repo']}"

Core abstractions:
{prep_res['abstraction_text']}

Describe the dependency relationships between these abstractions.

Output YAML only:

```yaml
- from: Authentication Layer
  to: Data Access Layer
  label: calls
```"""

        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=self.cur_retry == 0
        )

        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            relationships = yaml.safe_load(yaml_str) or []
        except Exception:
            relationships = []  # Non-fatal

        return {
            "relationships": relationships,
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
        shared["relationships"] = exec_res["relationships"]
        _append_trace(
            shared, "AnalyzeRelationships",
            len(exec_res["relationships"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 of indexFlow: BuildFileMap
# ─────────────────────────────────────────────────────────────────────────────

class BuildFileMap(AsyncNode):
    """Deterministic: invert file_indices → {abstraction_name: [file_paths]}.

    No LLM call.
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        file_paths = shared.get("file_paths")
        if file_paths is None:
            file_paths = [p for p, _c in shared.get("files", [])]
        return {
            "abstractions": shared["abstractions"],
            "file_paths": file_paths,
            "relationships": shared.get("relationships", []),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.monotonic()
        file_paths: List[str] = prep_res["file_paths"]
        file_map: Dict[str, List[str]] = {}

        for abstraction in prep_res["abstractions"]:
            name = abstraction["name"]
            paths: List[str] = []
            for idx in abstraction.get("file_indices", []):
                if isinstance(idx, int) and 0 <= idx < len(file_paths):
                    paths.append(file_paths[idx])
                elif isinstance(idx, str):
                    try:
                        i = int(idx.split("#")[0].strip())
                        if 0 <= i < len(file_paths):
                            paths.append(file_paths[i])
                    except (ValueError, IndexError):
                        pass
            file_map[name] = paths

        # Mermaid graph
        mermaid = ["graph TD"]
        for rel in prep_res["relationships"]:
            src = rel.get("from", "?").replace(" ", "_").replace("-", "_")
            tgt = rel.get("to", "?").replace(" ", "_").replace("-", "_")
            label = rel.get("label", "→")
            mermaid.append(
                f'  {src}["{rel.get("from","?")}"] -->|{label}| {tgt}["{rel.get("to","?")}"]'
            )

        return {
            "file_map": file_map,
            "mermaid": "\n".join(mermaid),
            "ms": int((time.monotonic() - t0) * 1000),
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["file_map"] = exec_res["file_map"]
        shared["mermaid"] = exec_res["mermaid"]
        _append_trace(shared, "BuildFileMap", len(exec_res["file_map"]), 0, 0, False,
                      ms=exec_res["ms"])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 of indexFlow: PersistOverview
# ─────────────────────────────────────────────────────────────────────────────

class PersistOverview(AsyncNode):
    """Upsert the indexed overview into ``repo_abstractions``."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        trace = shared.get("_trace", [])
        tokens_in = sum(t.get("tokens_in", 0) for t in trace)
        tokens_out = sum(t.get("tokens_out", 0) for t in trace)
        return {
            "repo": shared["repo"],
            "files_count": len(shared.get("file_paths") or shared.get("files", [])),
            "files_sha256": shared["files_sha256"],
            "overview": {
                "abstractions": shared["abstractions"],
                "relationships": shared.get("relationships", []),
                "file_map": shared["file_map"],
                "mermaid": shared.get("mermaid", ""),
            },
            "model_id": shared.get("model_id"),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        t0 = time.monotonic()
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO repo_abstractions
                        (repo_name, overview, files_indexed, files_sha256,
                         model_id, tokens_in, tokens_out)
                    VALUES
                        (:repo_name, CAST(:overview AS jsonb), :files_indexed,
                         :files_sha256, :model_id, :tokens_in, :tokens_out)
                    ON CONFLICT (repo_name) DO UPDATE SET
                        overview      = EXCLUDED.overview,
                        files_indexed = EXCLUDED.files_indexed,
                        files_sha256  = EXCLUDED.files_sha256,
                        model_id      = EXCLUDED.model_id,
                        tokens_in     = EXCLUDED.tokens_in,
                        tokens_out    = EXCLUDED.tokens_out,
                        generated_at  = NOW()
                """),
                {
                    "repo_name": prep_res["repo"],
                    "overview": json.dumps(prep_res["overview"]),
                    "files_indexed": prep_res["files_count"],
                    "files_sha256": prep_res["files_sha256"],
                    "model_id": prep_res["model_id"],
                    "tokens_in": prep_res["tokens_in"],
                    "tokens_out": prep_res["tokens_out"],
                },
            )
            await session.commit()

        return {"ms": int((time.monotonic() - t0) * 1000)}

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        # Preserve any existing keys (e.g. ``kg`` from PersistGraphDelta, which
        # runs earlier in indexFlow under the KG-first ordering).
        existing = shared.get("response") or {}
        existing.update({
            "repo_name": prep_res["repo"],
            "files_indexed": prep_res["files_count"],
            "abstractions": prep_res["overview"]["abstractions"],
            "relationships": prep_res["overview"]["relationships"],
            "mermaid": prep_res["overview"]["mermaid"],
            "files_sha256": prep_res["files_sha256"],
        })
        shared["response"] = existing
        _append_trace(shared, "PersistOverview", 1, 0, 0, False, ms=exec_res["ms"])
        logger.info(
            "PersistOverview: saved '%s' (%d files, %d abstractions)",
            prep_res["repo"],
            prep_res["files_count"],
            len(prep_res["overview"]["abstractions"]),
        )
        return None
