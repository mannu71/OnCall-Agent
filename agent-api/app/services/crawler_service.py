"""Crawler service — HTTP and MCP share this layer."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.crawler.handles import make_body_handle

logger = logging.getLogger(__name__)


class CrawlerService:
    """Repository queries and crawler flow orchestration."""

    async def filter_unindexed_repos(self, repo_names: List[str]) -> List[str]:
        """Return repo names from *repo_names* not yet present in repo_abstractions."""
        if not repo_names:
            return []
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(
                    "SELECT repo_name FROM repo_abstractions "
                    "WHERE repo_name = ANY(:names)"
                ),
                {"names": repo_names},
            )
            already_indexed = {r[0] for r in rows.fetchall()}
        return [name for name in repo_names if name not in already_indexed]

    async def list_indexed_repos(self) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(
                    "SELECT repo_name, files_indexed, model_id, generated_at "
                    "FROM repo_abstractions ORDER BY repo_name ASC"
                )
            )
            results = rows.fetchall()

        repos = [
            {
                "repo_name": r[0],
                "files_indexed": r[1],
                "model_id": r[2],
                "generated_at": r[3].isoformat() if r[3] else None,
            }
            for r in results
        ]
        return {"repos": repos, "count": len(repos)}

    async def get_index_overview(self, repo: str) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text(
                    "SELECT overview, files_indexed, files_sha256, model_id, "
                    "tokens_in, tokens_out, generated_at "
                    "FROM repo_abstractions WHERE repo_name = :repo"
                ),
                {"repo": repo},
            )
            r = row.fetchone()

        if r is None:
            return None

        overview, files_indexed, sha256, model_id, tokens_in, tokens_out, generated_at = r
        return {
            "repo_name": repo,
            "files_indexed": files_indexed,
            "files_sha256": sha256,
            "model_id": model_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "generated_at": generated_at.isoformat() if generated_at else None,
            "abstractions": overview.get("abstractions", []),
            "relationships": overview.get("relationships", []),
            "mermaid": overview.get("mermaid", ""),
        }

    async def index_repo(
        self,
        repo: str,
        *,
        force: bool = False,
        model_id: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_index_repo

        return await crawler_index_repo(
            repo=repo,
            force=force,
            model_id=model_id,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )

    # Repo-scoped crawler tables, child rows first so FK-free deletes stay safe.
    _REPO_SCOPED_TABLES = (
        "kg_edges",
        "kg_unresolved_refs",
        "kg_nodes",
        "kg_files",
        "repo_abstractions",
    )

    async def delete_index(self, repo: str) -> Dict[str, Any]:
        """Remove the entire index for *repo* from every repo-scoped table.

        Idempotent: deleting a repo that was never indexed simply reports zero
        rows removed rather than erroring, so the UI can call it freely.
        """
        rows: Dict[str, int] = {}
        async with AsyncSessionLocal() as session:
            async with session.begin():
                for table in self._REPO_SCOPED_TABLES:
                    result = await session.execute(
                        text(f"DELETE FROM {table} WHERE repo_name = :repo"),
                        {"repo": repo},
                    )
                    rows[table] = result.rowcount or 0
        total = sum(rows.values())
        logger.info("Deleted index for repo=%s (%d rows: %s)", repo, total, rows)
        return {"repo_name": repo, "deleted": True, "rows": rows}

    async def find_symbol(
        self,
        *,
        name: str,
        repo: str,
        kind: Optional[str] = None,
        limit: int = 5,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_find_symbol

        return await crawler_find_symbol(
            symbol=name,
            repo=repo,
            kind=kind,
            limit=limit,
            model_id=model_id,
        )

    async def get_body(self, *, handle: str, page: int = 1) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_get_body

        return await crawler_get_body(handle=handle, page=page)

    async def list_runs(
        self,
        *,
        flow_name: Optional[str] = None,
        repo: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        filters = []
        params: Dict[str, Any] = {"limit": limit, "offset": offset}

        if flow_name:
            filters.append("flow_name = :flow_name")
            params["flow_name"] = flow_name
        if repo:
            filters.append("repo_name = :repo")
            params["repo"] = repo
        if session_id:
            filters.append("session_id = :session_id")
            params["session_id"] = session_id

        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(
                    f"SELECT id, flow_name, session_id, repo_name, success, error, "
                    f"started_at, duration_ms "
                    f"FROM flow_runs {where} "
                    f"ORDER BY started_at DESC "
                    f"LIMIT :limit OFFSET :offset"
                ),
                params,
            )
            total_row = await session.execute(
                text(f"SELECT COUNT(*) FROM flow_runs {where}"),
                {k: v for k, v in params.items() if k not in ("limit", "offset")},
            )

        runs = [
            {
                "id": r[0],
                "flow_name": r[1],
                "session_id": r[2],
                "repo_name": r[3],
                "success": r[4],
                "error": r[5],
                "started_at": r[6].isoformat() if r[6] else None,
                "duration_ms": r[7],
            }
            for r in rows.fetchall()
        ]
        return {
            "total": total_row.scalar() or 0,
            "limit": limit,
            "offset": offset,
            "runs": runs,
        }

    async def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text(
                    "SELECT id, flow_name, session_id, repo_name, inputs, response, "
                    "trace, success, error, started_at, duration_ms "
                    "FROM flow_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
            r = row.fetchone()

        if r is None:
            return None

        return {
            "id": r[0],
            "flow_name": r[1],
            "session_id": r[2],
            "repo_name": r[3],
            "inputs": r[4],
            "response": r[5],
            "trace": r[6],
            "success": r[7],
            "error": r[8],
            "started_at": r[9].isoformat() if r[9] else None,
            "duration_ms": r[10],
        }

    async def cache_stats(self) -> Dict[str, Any]:
        from app.crawler.cache import cache_stats as _stats

        return await _stats()

    async def clear_cache(self) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("DELETE FROM llm_cache"))
            await session.commit()
            deleted = result.rowcount

        logger.warning("llm_cache truncated: %d rows deleted", deleted)
        return {"deleted": deleted, "message": "LLM cache cleared."}

    async def trace_path(
        self,
        *,
        symbol: str,
        repo: str,
        direction: str = "callers",
        depth: int = 2,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_trace_path

        return await crawler_trace_path(
            symbol=symbol,
            repo=repo,
            direction=direction,
            depth=depth,
            model_id=model_id,
        )

    async def search_semantic(
        self,
        *,
        query: str,
        repo: str,
        limit: int = 10,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_search_semantic

        return await crawler_search_semantic(
            query=query,
            repo=repo,
            limit=limit,
            model_id=model_id,
        )

    async def investigate_alert(
        self,
        *,
        alert: str,
        repo: str,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_investigate_alert

        return await crawler_investigate_alert(
            alert=alert,
            repo=repo,
            model_id=model_id,
        )

    async def get_repo_files(
        self,
        repo: str,
        *,
        language: Optional[str] = None,
        with_errors_only: bool = False,
        limit: int = 200,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_files

        return await crawler_files(
            repo=repo,
            language=language,
            with_errors_only=with_errors_only,
            limit=limit,
        )

    async def get_file_nodes(self, repo: str, file_path: str) -> Dict[str, Any]:
        nodes_sql = """
            SELECT kind, name, qualified_name, file_path, line_start, line_end, language, parent_name,
                   signature, docstring, exported, is_async, is_static, is_abstract, is_test, decorators, type_parameters, domain
            FROM kg_nodes
            WHERE repo_name = :repo AND file_path = :file_path
            ORDER BY line_start ASC
        """
        edges_sql = """
            SELECT source_qname, target_qname, kind, confidence, line
            FROM kg_edges
            WHERE repo_name = :repo AND file_path = :file_path
        """

        async with AsyncSessionLocal() as session:
            nodes_rows = await session.execute(
                text(nodes_sql),
                {"repo": repo, "file_path": file_path},
            )
            nodes_res = nodes_rows.fetchall()
            edges_rows = await session.execute(
                text(edges_sql),
                {"repo": repo, "file_path": file_path},
            )
            edges_res = edges_rows.fetchall()

        nodes = [
            {
                "kind": r[0],
                "name": r[1],
                "qualified_name": r[2],
                "file": r[3],
                "line_start": r[4],
                "line_end": r[5],
                "language": r[6],
                "parent_name": r[7],
                "signature": r[8],
                "docstring": r[9],
                "exported": bool(r[10]),
                "is_async": bool(r[11]),
                "is_static": bool(r[12]),
                "is_abstract": bool(r[13]),
                "is_test": bool(r[14]),
                "decorators": r[15],
                "type_parameters": r[16],
                "domain": r[17],
            }
            for r in nodes_res
        ]
        edges = [
            {
                "source_qname": r[0],
                "target_qname": r[1],
                "kind": r[2],
                "confidence": r[3],
                "line": r[4],
            }
            for r in edges_res
        ]
        return {"repo": repo, "file_path": file_path, "nodes": nodes, "edges": edges}

    async def get_node(self, repo: str, qualified_name: str) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_node

        return await crawler_node(qualified_name=qualified_name, repo=repo)

    async def get_repo_map(
        self, repo: str, *, name_like: Optional[str] = None, limit: int = 60,
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_repo_map

        return await crawler_repo_map(repo=repo, name_like=name_like, limit=limit)

    async def get_callers(self, repo: str, symbol: str, *, depth: int = 2) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_callers

        return await crawler_callers(symbol=symbol, repo=repo, depth=depth)

    async def get_callees(self, repo: str, symbol: str, *, depth: int = 2) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_callees

        return await crawler_callees(symbol=symbol, repo=repo, depth=depth)

    async def get_impact(self, repo: str, symbol: str) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_impact

        return await crawler_impact(symbol=symbol, repo=repo)

    async def get_references(
        self, repo: str, symbol: str, *, limit: int = 20
    ) -> Dict[str, Any]:
        from app.services.crawler_flows import crawler_find_references

        return await crawler_find_references(symbol=symbol, repo=repo, limit=limit)

    async def diff_impact(
        self, repo: str, *, files: List[str], symbols: List[str]
    ) -> Dict[str, Any]:
        sql = """
            WITH RECURSIVE impact_walk AS (
                SELECT
                    qualified_name,
                    name,
                    kind,
                    file_path,
                    line_start,
                    line_end,
                    is_test,
                    exported,
                    0 AS hop
                FROM kg_nodes
                WHERE repo_name = :r
                  AND (file_path = ANY(:files) OR name = ANY(:symbols) OR qualified_name = ANY(:symbols))

                UNION

                SELECT
                    n.qualified_name,
                    n.name,
                    n.kind,
                    n.file_path,
                    n.line_start,
                    n.line_end,
                    n.is_test,
                    n.exported,
                    w.hop + 1 AS hop
                FROM kg_edges e
                JOIN impact_walk w
                  ON  e.repo_name = :r
                  AND (e.target_qname = w.qualified_name OR e.target_qname = w.name)
                JOIN kg_nodes n
                  ON  n.repo_name = :r
                  AND n.qualified_name = e.source_qname
                WHERE w.hop < 5
            )
            SELECT DISTINCT ON (qualified_name)
                qualified_name, name, kind, file_path, line_start, line_end, is_test, exported, hop
            FROM impact_walk
            ORDER BY qualified_name, hop ASC;
        """

        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(sql),
                {"r": repo, "files": files, "symbols": symbols},
            )
            results = rows.fetchall()

        affected_nodes = []
        affected_tests = []
        affected_entry_points = []

        for row in results:
            qname, name, kind, file_path, line_start, line_end, is_test, exported, hop = row
            handle = None
            if file_path and line_start:
                handle = make_body_handle(
                    repo,
                    file_path,
                    max(1, int(line_start) - 2),
                    int(line_end or line_start) + 10,
                )

            entry = {
                "name": name,
                "qualified_name": qname,
                "kind": kind,
                "file": file_path,
                "line_start": line_start,
                "line_end": line_end,
                "hop": hop,
                "body_handle": handle,
            }
            affected_nodes.append(entry)
            if is_test:
                affected_tests.append(entry)
            elif exported or kind in ("route", "method") or "Controller" in qname:
                affected_entry_points.append(entry)

        return {
            "repo": repo,
            "input_files": files,
            "input_symbols": symbols,
            "total_impacted_nodes": len(affected_nodes),
            "impacted_nodes": affected_nodes,
            "affected_tests": affected_tests,
            "affected_entry_points": affected_entry_points,
        }

    async def discover_filesystem_repos(self, *, refresh: bool = False) -> Dict[str, Any]:
        """List repos visible on disk under REPOS_BASE_PATH."""
        from app.services.repo_discovery import list_discovered_repos

        return await list_discovered_repos(refresh=refresh)

    async def get_filesystem_repo(self, repo_name: str) -> Dict[str, Any]:
        """Metadata for one on-disk repo (path-jailed)."""
        from app.services.repo_discovery import get_discovered_repo

        return await get_discovered_repo(repo_name)

    async def list_repos_unified(self, *, refresh: bool = False) -> Dict[str, Any]:
        """Filesystem repos merged with ``repo_abstractions`` index metadata."""
        discovery = await self.discover_filesystem_repos(refresh=refresh)
        indexed = await self.list_indexed_repos()
        by_name = {r["repo_name"]: r for r in indexed.get("repos", [])}

        merged: List[Dict[str, Any]] = []
        for repo in discovery.get("repos", []):
            entry = dict(repo)
            meta = by_name.get(repo["name"])
            entry["indexed"] = meta is not None
            if meta:
                entry["files_indexed"] = meta.get("files_indexed")
                entry["model_id"] = meta.get("model_id")
                entry["generated_at"] = meta.get("generated_at")
            merged.append(entry)

        for name, meta in by_name.items():
            if not any(r["name"] == name for r in merged):
                merged.append({
                    "name": name,
                    "path": "",
                    "is_git": False,
                    "detected_languages": [],
                    "suggested_language": "python",
                    "file_count_sample": 0,
                    "indexed": True,
                    "files_indexed": meta.get("files_indexed"),
                    "model_id": meta.get("model_id"),
                    "generated_at": meta.get("generated_at"),
                    "db_only": True,
                })

        merged.sort(key=lambda r: r["name"].lower())
        return {
            **discovery,
            "repos": merged,
            "indexed_count": len(by_name),
        }


crawler_service = CrawlerService()
