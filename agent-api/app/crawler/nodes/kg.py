"""Knowledge-graph flow nodes — chained after PersistOverview in indexFlow.

The four nodes here turn the in-memory ``shared["files"]`` list (already
populated by :class:`~app.crawler.nodes.fetch.FetchRepo`) into rows in the
``kg_*`` tables created by migration 009.

Pipeline::

    FilterChangedFiles      ← compares per-file SHA against kg_files
        ↓
    ParseFilesAST           ← tree-sitter parse of changed files
        ↓
    ResolveBareCallTargets  ← cross-file resolution pass
        ↓
    PersistGraphDelta       ← transactional DELETE+INSERT into kg_nodes/edges/files

If ``_changed_files`` is empty (everything cached), ParseFilesAST short-
circuits and PersistGraphDelta still runs to handle file deletions.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)


def _extract_domain(file_path: str, qname: str) -> Optional[str]:
    """Deterministically extract high-level domain from file path or namespace.
    
    e.g. "ui/src/components/workflow/connectionValidation.js" -> "Workflow"
    e.g. "agent-api/app/api/v1/endpoints/crawler.py" -> "Crawler"
    e.g. "src/Services/Billing/InvoiceService.cs" -> "Billing"
    """
    if not file_path:
        return None
    # Normalize slashes
    path = file_path.replace("\\", "/")
    parts = path.split("/")
    
    # Heuristic 1: Look for folder names directly after source directories
    src_indices = [i for i, part in enumerate(parts) if part.lower() in ("src", "app", "endpoints", "services", "components", "pages")]
    if src_indices:
        idx = src_indices[0]
        if idx + 1 < len(parts) - 1:
            domain = parts[idx + 1].capitalize()
            if domain not in ("Controllers", "Services", "Repositories", "Endpoints", "Models", "V1", "V2"):
                return domain
            elif idx + 2 < len(parts) - 1:
                return parts[idx + 2].capitalize()
                
    # Heuristic 2: Deduce from class/symbol naming suffix or namespaces
    if "::" in qname:
        q_parts = qname.split("::")
        for part in q_parts[1:-1]:
            if part.lower() not in ("services", "controllers", "repositories", "models", "endpoints"):
                return part
                
    # Heuristic 3: Default to parent folder name
    if len(parts) > 1:
        parent = parts[-2].capitalize()
        if parent not in ("Src", "App", "Endpoints", "Services", "Components", "Pages", "V1", "V2", "Controllers"):
            return parent
            
    # Default fallback to file basename without extension
    base = os.path.basename(path)
    base_no_ext = os.path.splitext(base)[0]
    for suffix in ("Controller", "Service", "Repository", "Model", "Handler", "Api", "Route"):
        if base_no_ext.endswith(suffix) and len(base_no_ext) > len(suffix):
            base_no_ext = base_no_ext[:-len(suffix)]
            break
    return base_no_ext.capitalize()


# ───────────────────────────────────────────────────────────────────────────
# Node 1: FilterChangedFiles
# ───────────────────────────────────────────────────────────────────────────


class FilterChangedFiles(AsyncNode):
    """Diff ``shared["files"]`` against ``kg_files`` and find what changed.

    Reads from shared:
        repo   (str)
        files  (list[(relative_path, content)])

    Writes to shared:
        _kg_changed_files   list[(relative_path, content, sha256)]  — needs re-parse
        _kg_deleted_files   list[str]                                — present in DB, gone from disk
        _kg_kept_count      int                                       — unchanged files
        _kg_supported_count int                                       — files whose language we parse
        _trace              appends one entry
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo":  shared["repo"],
            "files": shared.get("files", []),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.kg.parser import detect_language
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        t0 = time.monotonic()
        repo  = prep_res["repo"]
        files = prep_res["files"]

        # 1. Compute current SHA256 per file and filter to parseable languages.
        current: Dict[str, Tuple[str, str]] = {}  # file_path -> (sha256, content)
        for path, content in files:
            if detect_language(path) is None:
                continue  # skip files we can't parse
            sha = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
            current[path] = (sha, content)

        # 2. Load the previously-known SHAs for this repo.
        existing: Dict[str, str] = {}
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text("SELECT file_path, sha256 FROM kg_files WHERE repo_name = :r"),
                {"r": repo},
            )
            for row in rows.fetchall():
                existing[row[0]] = row[1]

        # 3. Diff.
        changed: List[Tuple[str, str, str]] = []
        kept    = 0
        for path, (sha, content) in current.items():
            prev = existing.get(path)
            if prev == sha:
                kept += 1
            else:
                changed.append((path, content, sha))

        deleted = [p for p in existing.keys() if p not in current]

        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        return {
            "changed":   changed,
            "deleted":   deleted,
            "kept":      kept,
            "supported": len(current),
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_kg_changed_files"]   = exec_res["changed"]
        shared["_kg_deleted_files"]   = exec_res["deleted"]
        shared["_kg_kept_count"]      = exec_res["kept"]
        shared["_kg_supported_count"] = exec_res["supported"]

        _append_trace(
            shared, "FilterChangedFiles",
            result_count=len(exec_res["changed"]),
            tokens_in=0, tokens_out=0, cached=False,
            ms=prep_res.get("_ms", 0),
        )
        logger.info(
            "FilterChangedFiles: repo=%s supported=%d changed=%d unchanged=%d deleted=%d",
            shared.get("repo"),
            exec_res["supported"],
            len(exec_res["changed"]),
            exec_res["kept"],
            len(exec_res["deleted"]),
        )
        return None


# ───────────────────────────────────────────────────────────────────────────
# Node 2: ParseFilesAST
# ───────────────────────────────────────────────────────────────────────────


class ParseFilesAST(AsyncNode):
    """Run tree-sitter against each changed file (parallel via thread pool).

    Reads from shared:
        _kg_changed_files   list[(path, content, sha)]

    Writes to shared:
        _kg_nodes           list[NodeRecord]
        _kg_edges           list[EdgeRecord]
        _kg_parse_errors    dict[str, str]   — path -> error message
        _kg_per_file        dict[str, (node_count, edge_count)]
        _trace              appends one entry
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"changed": shared.get("_kg_changed_files", [])}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.kg.parser import parse_file

        t0 = time.monotonic()
        changed: List[Tuple[str, str, str]] = prep_res["changed"]

        if not changed:
            prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
            return {"nodes": [], "edges": [], "errors": {}, "per_file": {}}

        # Parse files concurrently in a thread pool — tree-sitter releases the GIL.
        def _parse_one(path: str, content: str):
            try:
                ns, es = parse_file(path, content)
                return path, ns, es, None
            except Exception as exc:  # noqa: BLE001
                return path, [], [], str(exc)

        results = await asyncio.gather(*[
            asyncio.to_thread(_parse_one, p, c) for (p, c, _sha) in changed
        ])

        all_nodes: List = []
        all_edges: List = []
        errors: Dict[str, str] = {}
        per_file: Dict[str, Tuple[int, int]] = {}

        for path, ns, es, err in results:
            if err:
                errors[path] = err
                per_file[path] = (0, 0)
            else:
                all_nodes.extend(ns)
                all_edges.extend(es)
                per_file[path] = (len(ns), len(es))

        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        return {
            "nodes":    all_nodes,
            "edges":    all_edges,
            "errors":   errors,
            "per_file": per_file,
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        shared["_kg_nodes"]        = exec_res["nodes"]
        shared["_kg_edges"]        = exec_res["edges"]
        shared["_kg_parse_errors"] = exec_res["errors"]
        shared["_kg_per_file"]     = exec_res["per_file"]

        _append_trace(
            shared, "ParseFilesAST",
            result_count=len(exec_res["nodes"]),
            tokens_in=0, tokens_out=0, cached=False,
            ms=prep_res.get("_ms", 0),
        )
        logger.info(
            "ParseFilesAST: %d nodes, %d edges across %d files (%d errors) in %dms",
            len(exec_res["nodes"]), len(exec_res["edges"]),
            len(exec_res["per_file"]),
            len(exec_res["errors"]),
            prep_res.get("_ms", 0),
        )
        return None


# ───────────────────────────────────────────────────────────────────────────
# Node 3: ResolveBareCallTargets
# ───────────────────────────────────────────────────────────────────────────


class ResolveBareCallTargets(AsyncNode):
    """Promote bare-name edge targets to qualified names using imports.

    Resolution needs the FULL set of nodes for the repo, not just the
    changed-file subset.  We therefore pull the unchanged nodes from
    ``kg_nodes`` and merge them with the freshly parsed nodes before running
    the resolver.

    Reads from shared:
        repo, _kg_nodes, _kg_edges, _kg_changed_files

    Writes to shared:
        _kg_edges  — replaced with promoted edges
        _trace
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo":          shared["repo"],
            "new_nodes":     shared.get("_kg_nodes", []),
            "edges":         shared.get("_kg_edges", []),
            "changed_paths": [p for (p, _c, _s) in shared.get("_kg_changed_files", [])],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> List:
        from app.crawler.kg.parser import NodeRecord
        from app.crawler.kg.resolver import resolve_bare_call_targets
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        t0 = time.monotonic()
        edges = prep_res["edges"]
        if not edges:
            prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
            return []

        # Pull (name, qualified_name) for files NOT in the changed set —
        # they stay in the DB and the resolver needs them as candidates.
        all_nodes: List[NodeRecord] = list(prep_res["new_nodes"])
        async with AsyncSessionLocal() as session:
            if prep_res["changed_paths"]:
                rows = await session.execute(
                    text("""
                        SELECT name, qualified_name, file_path
                        FROM kg_nodes
                        WHERE repo_name = :r
                          AND file_path <> ALL(:paths)
                    """),
                    {"r": prep_res["repo"], "paths": prep_res["changed_paths"]},
                )
            else:
                rows = await session.execute(
                    text("SELECT name, qualified_name, file_path FROM kg_nodes WHERE repo_name = :r"),
                    {"r": prep_res["repo"]},
                )
            for row in rows.fetchall():
                # Minimal NodeRecord stub — only name/qualified_name matter for resolution.
                all_nodes.append(NodeRecord(
                    kind="",
                    name=row[0],
                    qualified_name=row[1],
                    file_path=row[2],
                    line_start=0, line_end=0,
                ))

        resolved = resolve_bare_call_targets(all_nodes, edges)
        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        return resolved

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: List,
    ) -> Optional[str]:
        shared["_kg_edges"] = exec_res

        _append_trace(
            shared, "ResolveBareCallTargets",
            result_count=len(exec_res),
            tokens_in=0, tokens_out=0, cached=False,
            ms=prep_res.get("_ms", 0),
        )
        return None


# ───────────────────────────────────────────────────────────────────────────
# Node 4: PersistGraphDelta
# ───────────────────────────────────────────────────────────────────────────


class PersistGraphDelta(AsyncNode):
    """Apply node/edge/file deltas to the kg_* tables in one transaction.

    Reads from shared:
        repo, _kg_changed_files, _kg_deleted_files,
        _kg_nodes, _kg_edges, _kg_per_file, _kg_parse_errors

    Writes to shared:
        response["kg"]  summary dict appended to the existing response
        _trace
    """

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo":         shared["repo"],
            "changed":      shared.get("_kg_changed_files", []),
            "deleted":      shared.get("_kg_deleted_files", []),
            "nodes":        shared.get("_kg_nodes", []),
            "edges":        shared.get("_kg_edges", []),
            "per_file":     shared.get("_kg_per_file", {}),
            "parse_errors": shared.get("_kg_parse_errors", {}),
            "supported":    shared.get("_kg_supported_count", 0),
            "kept":         shared.get("_kg_kept_count", 0),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.crawler.kg.parser import detect_language
        from sqlalchemy import text

        t0 = time.monotonic()
        repo         = prep_res["repo"]
        changed      = prep_res["changed"]
        deleted      = prep_res["deleted"]
        nodes        = prep_res["nodes"]
        edges        = prep_res["edges"]
        per_file     = prep_res["per_file"]
        parse_errors = prep_res["parse_errors"]

        affected_paths: List[str] = [p for (p, _c, _s) in changed] + deleted

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # 1. Delete obsolete rows for changed + deleted files.
                if affected_paths:
                    await session.execute(
                        text("""
                            DELETE FROM kg_edges
                            WHERE repo_name = :r AND file_path = ANY(:paths)
                        """),
                        {"r": repo, "paths": affected_paths},
                    )
                    await session.execute(
                        text("""
                            DELETE FROM kg_nodes
                            WHERE repo_name = :r AND file_path = ANY(:paths)
                        """),
                        {"r": repo, "paths": affected_paths},
                    )

                # 2. Drop kg_files rows for deleted files only.
                if deleted:
                    await session.execute(
                        text("""
                            DELETE FROM kg_files
                            WHERE repo_name = :r AND file_path = ANY(:paths)
                        """),
                        {"r": repo, "paths": deleted},
                    )

                # 3. Bulk insert nodes.
                if nodes:
                    node_rows = [
                        {
                            "repo_name":       repo,
                            "kind":            n.kind,
                            "name":            n.name,
                            "qualified_name":  n.qualified_name,
                            "file_path":       n.file_path,
                            "line_start":      n.line_start,
                            "line_end":        n.line_end,
                            "col_start":       n.col_start,
                            "col_end":         n.col_end,
                            "language":        n.language,
                            "parent_name":     n.parent_name,
                            "signature":       n.signature,
                            "docstring":       n.docstring,
                            "exported":        bool(n.exported),
                            "is_async":        bool(n.is_async),
                            "is_static":       bool(n.is_static),
                            "is_abstract":     bool(n.is_abstract),
                            "is_test":         bool(n.is_test),
                            "decorators":      json.dumps(n.decorators)      if n.decorators      else None,
                            "type_parameters": json.dumps(n.type_parameters) if n.type_parameters else None,
                            "domain":          _extract_domain(n.file_path, n.qualified_name),
                        }
                        for n in nodes
                    ]
                    await session.execute(
                        text("""
                            INSERT INTO kg_nodes
                              (repo_name, kind, name, qualified_name, file_path,
                               line_start, line_end, col_start, col_end,
                               language, parent_name, signature, docstring,
                               exported, is_async, is_static, is_abstract, is_test,
                               decorators, type_parameters, domain)
                            VALUES
                              (:repo_name, :kind, :name, :qualified_name, :file_path,
                               :line_start, :line_end, :col_start, :col_end,
                               :language, :parent_name, :signature, :docstring,
                               :exported, :is_async, :is_static, :is_abstract, :is_test,
                               CAST(:decorators AS jsonb), CAST(:type_parameters AS jsonb), :domain)
                            ON CONFLICT (repo_name, qualified_name) DO UPDATE SET
                                kind            = EXCLUDED.kind,
                                name            = EXCLUDED.name,
                                file_path       = EXCLUDED.file_path,
                                line_start      = EXCLUDED.line_start,
                                line_end        = EXCLUDED.line_end,
                                col_start       = EXCLUDED.col_start,
                                col_end         = EXCLUDED.col_end,
                                language        = EXCLUDED.language,
                                parent_name     = EXCLUDED.parent_name,
                                signature       = EXCLUDED.signature,
                                docstring       = EXCLUDED.docstring,
                                exported        = EXCLUDED.exported,
                                is_async        = EXCLUDED.is_async,
                                is_static       = EXCLUDED.is_static,
                                is_abstract     = EXCLUDED.is_abstract,
                                is_test         = EXCLUDED.is_test,
                                decorators      = EXCLUDED.decorators,
                                type_parameters = EXCLUDED.type_parameters,
                                domain          = EXCLUDED.domain
                        """),
                        node_rows,
                    )

                # 4. Bulk insert edges.
                if edges:
                    edge_rows = [
                        {
                            "repo_name":    repo,
                            "kind":         e.kind,
                            "source_qname": e.source_qname,
                            "target_qname": e.target_qname,
                            "file_path":    e.file_path,
                            "line":         e.line,
                            "col":          e.col,
                            "confidence":   e.confidence,
                        }
                        for e in edges
                    ]
                    await session.execute(
                        text("""
                            INSERT INTO kg_edges
                              (repo_name, kind, source_qname, target_qname,
                               file_path, line, col, confidence)
                            VALUES
                              (:repo_name, :kind, :source_qname, :target_qname,
                               :file_path, :line, :col, :confidence)
                        """),
                        edge_rows,
                    )

                # 5. Upsert kg_files for every changed file.
                if changed:
                    file_rows = []
                    for (path, content, sha) in changed:
                        node_cnt, edge_cnt = per_file.get(path, (0, 0))
                        file_rows.append({
                            "repo_name":   repo,
                            "file_path":   path,
                            "sha256":      sha,
                            "language":    detect_language(path),
                            "size_bytes":  len(content.encode("utf-8", errors="replace")),
                            "node_count":  node_cnt,
                            "edge_count":  edge_cnt,
                            "parse_error": parse_errors.get(path),
                        })
                    await session.execute(
                        text("""
                            INSERT INTO kg_files
                              (repo_name, file_path, sha256, language,
                               size_bytes, node_count, edge_count, parse_error,
                               parsed_at)
                            VALUES
                              (:repo_name, :file_path, :sha256, :language,
                               :size_bytes, :node_count, :edge_count, :parse_error,
                               NOW())
                            ON CONFLICT (repo_name, file_path) DO UPDATE SET
                                sha256      = EXCLUDED.sha256,
                                language    = EXCLUDED.language,
                                size_bytes  = EXCLUDED.size_bytes,
                                node_count  = EXCLUDED.node_count,
                                edge_count  = EXCLUDED.edge_count,
                                parse_error = EXCLUDED.parse_error,
                                parsed_at   = NOW()
                        """),
                        file_rows,
                    )

        prep_res["_ms"] = int((time.monotonic() - t0) * 1000)
        return {
            "files_parsed":    len(changed),
            "files_unchanged": prep_res["kept"],
            "files_deleted":   len(deleted),
            "files_supported": prep_res["supported"],
            "nodes_written":   len(nodes),
            "edges_written":   len(edges),
            "parse_errors":    len(parse_errors),
        }

    async def post(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any],
    ) -> Optional[str]:
        # Attach KG summary to the existing response dict (created by PersistOverview).
        if "response" not in shared:
            shared["response"] = {}
        shared["response"]["kg"] = exec_res

        _append_trace(
            shared, "PersistGraphDelta",
            result_count=exec_res["nodes_written"],
            tokens_in=0, tokens_out=0, cached=False,
            ms=prep_res.get("_ms", 0),
        )
        logger.info(
            "PersistGraphDelta: repo=%s parsed=%d unchanged=%d deleted=%d "
            "nodes=%d edges=%d errors=%d in %dms",
            shared.get("repo"),
            exec_res["files_parsed"],
            exec_res["files_unchanged"],
            exec_res["files_deleted"],
            exec_res["nodes_written"],
            exec_res["edges_written"],
            exec_res["parse_errors"],
            prep_res.get("_ms", 0),
        )
        return None


__all__ = [
    "FilterChangedFiles",
    "ParseFilesAST",
    "ResolveBareCallTargets",
    "PersistGraphDelta",
]
