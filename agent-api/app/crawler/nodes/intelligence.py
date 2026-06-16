"""Project-intelligence nodes for indexFlow.

These LLM nodes run *after* the knowledge-graph nodes (KG-first ordering) and
the abstraction nodes, turning the freshly-persisted ``kg_*`` rows plus the
abstraction overview into a layered, human-style understanding of the repo
stored in ``repo_docs`` (migration 015):

    SummarizeModules    → per-directory "wiki" docs (doc_type='module'), incremental
    BuildProjectBrief   → product/domain/architecture narrative + feature map
                          (doc_type='brief' | 'domain' | 'features')

All nodes are best-effort: a model refusal or a parse failure leaves any prior
docs intact and never fails the flow (the KG is already persisted upstream).
``ExtractStandards`` (coding-conventions profile) lives in ``standards.py`` and
shares the :func:`upsert_repo_doc` helper defined here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

# Bounded concurrency for the per-module LLM fan-out — keeps us under Bedrock's
# per-second guardrail rate limit while still parallelising.
_LLM_CONCURRENCY = 4

# Module selection / context caps.
_MAX_MODULES = 30          # hard ceiling on per-directory docs generated per run
_MIN_MODULE_NODES = 2      # skip trivial dirs (a lone constants file, etc.)
_MODULE_FILES_SAMPLE = 6   # excerpt at most this many files per module
_MODULE_FILE_CHARS = 1_500
_MODULE_CTX_CHARS = 12_000

# Files worth feeding verbatim into the project brief (read from disk if present).
_BRIEF_DOC_FILES = (
    "README.md", "readme.md", "README.rst",
    "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
    "docker-compose.yml", "docker-compose.yaml",
)
_BRIEF_DOC_CHARS = 4_000


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm(path: str) -> str:
    return (path or "").replace("\\", "/")


def _module_key(file_path: str) -> str:
    """Directory a file belongs to, used as the module doc_path. '(root)' for top level."""
    p = _norm(file_path)
    return p.rsplit("/", 1)[0] if "/" in p else "(root)"


def parse_yaml_block(response: str) -> Any:
    """Best-effort parse of a ```yaml fenced block (or raw YAML) from an LLM reply."""
    try:
        if "```yaml" in response:
            yaml_str = response.split("```yaml", 1)[1].split("```", 1)[0].strip()
        elif "```" in response:
            yaml_str = response.split("```", 1)[1].split("```", 1)[0].strip()
        else:
            yaml_str = response.strip()
        return yaml.safe_load(yaml_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("intelligence: YAML parse failed (%s); raw[:200]=%r", exc, response[:200])
        return None


async def upsert_repo_doc(
    repo: str,
    doc_type: str,
    doc_path: str,
    content: Dict[str, Any],
    *,
    files_sha256: Optional[str] = None,
    model_id: Optional[str] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Upsert one ``repo_docs`` row. Used by every intelligence node."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO repo_docs
                    (repo_name, doc_type, doc_path, content, files_sha256,
                     model_id, tokens_in, tokens_out, generated_at)
                VALUES
                    (:repo, :doc_type, :doc_path, CAST(:content AS jsonb), :sha,
                     :model_id, :tin, :tout, NOW())
                ON CONFLICT (repo_name, doc_type, doc_path) DO UPDATE SET
                    content      = EXCLUDED.content,
                    files_sha256 = EXCLUDED.files_sha256,
                    model_id     = EXCLUDED.model_id,
                    tokens_in    = EXCLUDED.tokens_in,
                    tokens_out   = EXCLUDED.tokens_out,
                    generated_at = NOW()
            """),
            {
                "repo": repo, "doc_type": doc_type, "doc_path": doc_path,
                "content": json.dumps(content, default=str),
                "sha": files_sha256, "model_id": model_id,
                "tin": tokens_in, "tout": tokens_out,
            },
        )
        await session.commit()


async def _load_kg_for_intelligence(repo: str) -> Tuple[Dict[str, List[dict]], Dict[str, str]]:
    """Return ({module_dir: [node dicts]}, {module_dir: aggregate_sha}) for *repo*.

    Reads the freshly-persisted kg_nodes / kg_files (this node runs after
    PersistGraphDelta), so it sees the full repo graph regardless of which files
    changed this run.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    by_module: Dict[str, List[dict]] = {}
    file_sha: Dict[str, str] = {}

    async with AsyncSessionLocal() as session:
        nrows = await session.execute(
            text("""
                SELECT name, kind, qualified_name, file_path, signature, exported
                FROM kg_nodes WHERE repo_name = :r
                ORDER BY file_path, line_start
            """),
            {"r": repo},
        )
        for name, kind, qname, file_path, signature, exported in nrows.fetchall():
            by_module.setdefault(_module_key(file_path), []).append({
                "name": name, "kind": kind, "qname": qname,
                "file": _norm(file_path), "signature": signature, "exported": exported,
            })

        frows = await session.execute(
            text("SELECT file_path, sha256 FROM kg_files WHERE repo_name = :r"),
            {"r": repo},
        )
        for file_path, sha in frows.fetchall():
            file_sha[_norm(file_path)] = sha or ""

    # Aggregate per-module fingerprint from member files' shas (order-stable).
    module_sha: Dict[str, str] = {}
    files_by_module: Dict[str, List[str]] = {}
    for fp, sha in file_sha.items():
        files_by_module.setdefault(_module_key(fp), []).append((fp, sha))
    for mod, items in files_by_module.items():
        h = hashlib.sha256()
        for fp, sha in sorted(items):
            h.update(fp.encode()); h.update((sha or "").encode())
        module_sha[mod] = h.hexdigest()

    return by_module, module_sha


def _signatures_block(nodes: List[dict], *, limit: int = 60) -> str:
    """Compact `kind name(signature)` listing for a module's symbols."""
    lines: List[str] = []
    for n in nodes[:limit]:
        sig = (n.get("signature") or "").strip().replace("\n", " ")
        mark = "*" if n.get("exported") else " "
        lines.append(f"{mark} {n['kind']} {n['name']}{(' ' + sig) if sig else ''}"[:200])
    if len(nodes) > limit:
        lines.append(f"… (+{len(nodes) - limit} more symbols)")
    return "\n".join(lines)


async def _module_excerpts(repo: str, file_paths: List[str]) -> str:
    """Read a bounded sample of a module's files as line-capped excerpts."""
    from app.crawler.files import read_repo_files_bounded

    sample = file_paths[:_MODULE_FILES_SAMPLE]
    if not sample:
        return ""
    parts: List[str] = []
    total = 0
    for path, content in await read_repo_files_bounded(repo, sample):
        chunk = f"--- {path} ---\n{content[:_MODULE_FILE_CHARS]}\n\n"
        if total + len(chunk) > _MODULE_CTX_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Node: SummarizeModules — per-directory "wiki" docs (incremental)
# ─────────────────────────────────────────────────────────────────────────────

class SummarizeModules(AsyncNode):
    """LLM: one concise doc per directory/module, refreshed only when changed."""

    def __init__(self, max_retries: int = 2, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        repo = shared["repo"]
        force = bool(shared.get("force"))
        by_module, module_sha = await _load_kg_for_intelligence(repo)

        existing: Dict[str, str] = {}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text("SELECT doc_path, files_sha256 FROM repo_docs "
                     "WHERE repo_name = :r AND doc_type = 'module'"),
                {"r": repo},
            )
            for doc_path, sha in rows.fetchall():
                existing[doc_path] = sha or ""

        # Pick changed (or all, on force) non-trivial modules, largest first.
        candidates = [
            (mod, nodes) for mod, nodes in by_module.items()
            if len(nodes) >= _MIN_MODULE_NODES
        ]
        candidates.sort(key=lambda t: len(t[1]), reverse=True)

        modules: List[Dict[str, Any]] = []
        skipped = 0
        for mod, nodes in candidates:
            sha = module_sha.get(mod, "")
            if not force and existing.get(mod) == sha and mod in existing:
                skipped += 1
                continue
            if len(modules) >= _MAX_MODULES:
                break
            files = sorted({n["file"] for n in nodes})
            modules.append({
                "dir": mod,
                "sha": sha,
                "files": files,
                "signatures": _signatures_block(nodes),
            })

        return {
            "repo": repo,
            "model": shared.get("model_id"),
            "modules": modules,
            "skipped": skipped,
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm

        t0 = time.monotonic()
        repo = prep_res["repo"]
        model = prep_res["model"]
        modules: List[Dict[str, Any]] = prep_res["modules"]
        if not modules:
            return {"generated": 0, "skipped": prep_res["skipped"],
                    "tokens_in": 0, "tokens_out": 0, "ms": 0}

        sem = asyncio.Semaphore(_LLM_CONCURRENCY)
        tin_total = tout_total = 0

        async def _one(mod: Dict[str, Any]) -> int:
            nonlocal tin_total, tout_total
            async with sem:
                excerpts = await _module_excerpts(repo, mod["files"])
                prompt = f"""You are documenting the module "{mod['dir']}" of the project "{repo}".

Symbols in this module (* = exported):
{mod['signatures']}

Source excerpts:
{excerpts}

Write a concise module document. Output YAML only:

```yaml
responsibility: One or two sentences on what this module is responsible for.
key_components:
  - name: ClassOrFunctionName
    role: what it does
data_flow: How data enters and leaves this module (inputs → processing → outputs).
depends_on:
  - other module or external system this relies on
```"""
                try:
                    resp, tin, tout, _cached = await call_llm(
                        prompt, model_id=model, use_cache=True, tier="index",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("SummarizeModules: LLM failed for %s: %s", mod["dir"], exc)
                    return 0
                tin_total += tin; tout_total += tout
                parsed = parse_yaml_block(resp)
                if not isinstance(parsed, dict):
                    return 0
                content = {
                    "path": mod["dir"],
                    "responsibility": parsed.get("responsibility", ""),
                    "key_components": parsed.get("key_components", []),
                    "data_flow": parsed.get("data_flow", ""),
                    "depends_on": parsed.get("depends_on", []),
                    "files": mod["files"],
                }
                await upsert_repo_doc(
                    repo, "module", mod["dir"], content,
                    files_sha256=mod["sha"], model_id=model,
                    tokens_in=tin, tokens_out=tout,
                )
                return 1

        results = await asyncio.gather(*(_one(m) for m in modules))
        return {
            "generated": sum(results),
            "skipped": prep_res["skipped"],
            "tokens_in": tin_total,
            "tokens_out": tout_total,
            "ms": int((time.monotonic() - t0) * 1000),
        }

    async def post(self, shared, prep_res, exec_res) -> Optional[str]:
        shared["_modules_generated"] = exec_res["generated"]
        _append_trace(
            shared, "SummarizeModules", exec_res["generated"],
            exec_res["tokens_in"], exec_res["tokens_out"], False,
            llm_calls=exec_res["generated"], ms=exec_res["ms"],
        )
        logger.info(
            "SummarizeModules: %d module docs generated, %d unchanged (repo=%s)",
            exec_res["generated"], exec_res["skipped"], shared.get("repo"),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node: BuildProjectBrief — product/domain/architecture + feature map
# ─────────────────────────────────────────────────────────────────────────────

class BuildProjectBrief(AsyncNode):
    """LLM roll-up: feed summaries (not raw code) to produce the project brief.

    Writes three ``repo_docs`` rows: ``brief``, ``domain``, ``features``.
    Skipped on a no-op reindex (no module regenerated and a brief already exists).
    """

    def __init__(self, max_retries: int = 2, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.crawler.files import read_repo_file
        from sqlalchemy import text

        repo = shared["repo"]
        force = bool(shared.get("force"))

        async with AsyncSessionLocal() as session:
            mrows = await session.execute(
                text("SELECT doc_path, content FROM repo_docs "
                     "WHERE repo_name = :r AND doc_type = 'module' ORDER BY doc_path"),
                {"r": repo},
            )
            module_docs = [(dp, c) for dp, c in mrows.fetchall()]
            brief_exists = (await session.execute(
                text("SELECT 1 FROM repo_docs WHERE repo_name = :r AND doc_type = 'brief'"),
                {"r": repo},
            )).first() is not None

        skip = (not force) and brief_exists and shared.get("_modules_generated", 0) == 0

        # Pull README / manifest text if present (best-effort).
        doc_text = ""
        if not skip:
            file_paths = shared.get("file_paths") or [p for p, _c in shared.get("files", [])]
            present = {_norm(p): p for p in file_paths}
            for cand in _BRIEF_DOC_FILES:
                hit = next((orig for norm, orig in present.items()
                            if norm == cand or norm.endswith("/" + cand)), None)
                if hit:
                    try:
                        body = await read_repo_file(repo, hit)
                        doc_text += f"--- {hit} ---\n{body[:_BRIEF_DOC_CHARS]}\n\n"
                    except Exception:  # noqa: BLE001
                        pass
                if len(doc_text) > _BRIEF_DOC_CHARS * 2:
                    break

        module_lines = []
        for dp, c in module_docs:
            resp = (c or {}).get("responsibility", "") if isinstance(c, dict) else ""
            module_lines.append(f"- {dp}: {str(resp)[:200]}")

        return {
            "repo": repo,
            "model": shared.get("model_id"),
            "skip": skip,
            "doc_text": doc_text,
            "module_summaries": "\n".join(module_lines),
            "abstractions": shared.get("abstractions", []),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm

        if prep_res["skip"]:
            return {"written": 0, "tokens_in": 0, "tokens_out": 0, "ms": 0, "skipped": True}

        t0 = time.monotonic()
        repo = prep_res["repo"]
        abstraction_text = "\n".join(
            f"- {a.get('name','?')}: {str(a.get('summary',''))[:160]}"
            for a in prep_res["abstractions"] if isinstance(a, dict)
        )
        prompt = f"""You are a staff engineer onboarding to the project "{repo}".
Using ONLY the summarised evidence below (do not invent features), produce an
intelligence brief that lets a teammate reason about the system like an insider.

Project documentation / manifests:
{prep_res['doc_text'] or '(none found)'}

Module summaries:
{prep_res['module_summaries'] or '(none)'}

Core abstractions:
{abstraction_text or '(none)'}

Output YAML only:

```yaml
brief: |
  2-4 sentences: what the product does, who uses it, and why it exists.
domain_model:
  - entity: BusinessConcept
    description: what it represents and how it relates to others
architecture: |
  The layers/services and how they fit together (UI ↔ API ↔ DB ↔ external systems),
  and where major responsibilities live.
features:
  - feature: User-facing capability or flow
    description: one line
    modules: [list of module directories that implement it]
```"""
        resp, tin, tout, _cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True, tier="index",
        )
        parsed = parse_yaml_block(resp)
        written = 0
        if isinstance(parsed, dict):
            await upsert_repo_doc(
                repo, "brief", "",
                {"brief": parsed.get("brief", ""), "architecture": parsed.get("architecture", "")},
                model_id=prep_res["model"], tokens_in=tin, tokens_out=tout,
            )
            await upsert_repo_doc(
                repo, "domain", "",
                {"domain_model": parsed.get("domain_model", [])},
                model_id=prep_res["model"],
            )
            await upsert_repo_doc(
                repo, "features", "",
                {"features": parsed.get("features", [])},
                model_id=prep_res["model"],
            )
            written = 3
        return {
            "written": written, "tokens_in": tin, "tokens_out": tout,
            "ms": int((time.monotonic() - t0) * 1000), "skipped": False,
        }

    async def post(self, shared, prep_res, exec_res) -> Optional[str]:
        _append_trace(
            shared, "BuildProjectBrief", exec_res["written"],
            exec_res["tokens_in"], exec_res["tokens_out"], False,
            llm_calls=0 if exec_res.get("skipped") else 1, ms=exec_res["ms"],
        )
        logger.info(
            "BuildProjectBrief: %s (repo=%s)",
            "skipped (no change)" if exec_res.get("skipped") else f"{exec_res['written']} docs written",
            shared.get("repo"),
        )
        return None


__all__ = ["SummarizeModules", "BuildProjectBrief", "upsert_repo_doc", "parse_yaml_block",
           "_load_kg_for_intelligence", "_signatures_block"]
