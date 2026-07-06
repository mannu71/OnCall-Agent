"""codegraph store admin — list / inspect / reindex / delete indexed projects.

v0.10.0+ of the codegraph engine uses one SQLite DB per project stored under
``~/.cache/codegraph/`` (``CODEGRAPH_CACHE_DIR``). Discovery is done by
scanning that directory — there is no central registry. Each file is named
``<project-name>.db`` where project-name is derived from the repo path.

The user-facing repo name (used in API calls and the UI) is the basename of the
``root_path`` stored in each project's ``projects`` table (e.g. ``compliance-api``
from ``/app/data/indexed_repos/compliance-api``).

Mutations (reindex) go through the MCP engine. Delete removes the DB file.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# Labels that represent structural/meta graph nodes, not source-code entities.
_META_LABELS = {"File", "Folder", "Project", "Branch", "Module", "Section", "Resource"}


def _cache_dir() -> str:
    return settings.codegraph_cache_dir


def _connect(db_path: str) -> Optional[sqlite3.Connection]:
    """Open a codegraph project DB read-only. Returns None if missing."""
    if not db_path or not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _project_dbs() -> List[Tuple[str, str, str]]:
    """Scan the cache dir and return [(db_path, internal_name, repo_name)]."""
    d = _cache_dir()
    if not os.path.isdir(d):
        return []
    result: List[Tuple[str, str, str]] = []
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".db") or fname.startswith("_"):
            continue
        db_path = os.path.join(d, fname)
        try:
            conn = _connect(db_path)
            if conn is None:
                continue
            try:
                row = conn.execute(
                    "SELECT name, root_path FROM projects LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            if row:
                internal = row["name"]
                root = (row["root_path"] or "").rstrip("/")
                repo_name = os.path.basename(root) or internal
                result.append((db_path, internal, repo_name))
        except Exception:  # noqa: BLE001
            logger.debug("codegraph_admin: could not read %s", db_path, exc_info=True)
    return result


def _find_project(repo_name: str) -> Optional[Tuple[str, str]]:
    """Return (db_path, internal_name) for a user-facing repo_name, or None."""
    for db_path, internal, name in _project_dbs():
        if name == repo_name:
            return (db_path, internal)
    return None


def _props(row_props: Any) -> Dict[str, Any]:
    """Safely parse the JSON properties column."""
    if not row_props:
        return {}
    if isinstance(row_props, dict):
        return row_props
    try:
        return json.loads(row_props)
    except Exception:  # noqa: BLE001
        return {}


def list_projects() -> List[Dict[str, Any]]:
    """Return indexed codegraph projects normalised to the Explorer's repo shape.

    Shape: ``{repo_name, files_indexed, node_count, edge_count, indexed_at, root}``.
    """
    out: List[Dict[str, Any]] = []
    for db_path, internal, repo_name in _project_dbs():
        try:
            conn = _connect(db_path)
            if conn is None:
                continue
            try:
                pr = conn.execute(
                    "SELECT indexed_at, root_path FROM projects LIMIT 1"
                ).fetchone()
                # Count code nodes (exclude meta labels like File/Folder/Project)
                node_count = conn.execute(
                    "SELECT COUNT(*) FROM nodes WHERE label NOT IN "
                    "('File','Folder','Project','Branch','Module','Section','Resource')"
                ).fetchone()[0]
                edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                files_indexed = conn.execute(
                    "SELECT COUNT(DISTINCT file_path) FROM nodes "
                    "WHERE file_path != '' AND label NOT IN "
                    "('File','Folder','Project','Branch','Module','Section','Resource')"
                ).fetchone()[0]
            finally:
                conn.close()
            out.append({
                "repo_name": repo_name,
                "files_indexed": files_indexed,
                "node_count": node_count,
                "edge_count": edge_count,
                "indexed_at": pr["indexed_at"] if pr else None,
                "root": pr["root_path"] if pr else None,
            })
        except Exception:  # noqa: BLE001
            logger.debug("codegraph_admin: list failed for %s", db_path, exc_info=True)
    return out


def project_exists(project: str) -> bool:
    return _find_project(project) is not None


def list_files(project: str) -> List[Dict[str, Any]]:
    """Return per-file node counts for a project (Explorer file-tree shape)."""
    found = _find_project(project)
    if not found:
        return []
    db_path, internal = found
    conn = _connect(db_path)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT file_path, COUNT(*) node_count "
            "FROM nodes "
            "WHERE file_path != '' "
            "  AND label NOT IN ('File','Folder','Project','Branch','Module','Section','Resource') "
            "GROUP BY file_path ORDER BY file_path",
        ).fetchall()
        return [
            {
                "file": r["file_path"],
                "language": "",
                "node_count": r["node_count"],
                "edge_count": 0,
                "parse_error": None,
            }
            for r in rows
        ]
    finally:
        conn.close()


def file_nodes(project: str, file_path: str) -> Dict[str, Any]:
    """Return nodes + intra-file edges for one file (Explorer graph shape).

    Maps the new schema (source_id/target_id) to the crawler shape
    (source_qname/target_qname/kind) so the UI graph renderer works unchanged.
    """
    found = _find_project(project)
    if not found:
        return {"nodes": [], "edges": []}
    db_path, internal = found
    conn = _connect(db_path)
    if conn is None:
        return {"nodes": [], "edges": []}
    try:
        node_rows = conn.execute(
            "SELECT id, label, name, qualified_name, file_path, "
            "start_line, end_line, properties "
            "FROM nodes WHERE file_path=? "
            "AND label NOT IN ('File','Folder','Project','Branch','Module','Section','Resource') "
            "ORDER BY start_line",
            (file_path,),
        ).fetchall()

        nodes: List[Dict[str, Any]] = []
        id_to_qname: Dict[int, str] = {}
        for r in node_rows:
            qn = r["qualified_name"]
            id_to_qname[r["id"]] = qn
            props = _props(r["properties"])
            # parent_name: extract from parent_class qualified name if present
            parent_class_qn = props.get("parent_class", "")
            parent_name = None
            if parent_class_qn:
                parent_name = parent_class_qn.split(".")[-1] if "." in parent_class_qn else parent_class_qn
            nodes.append({
                "kind": r["label"].lower(),
                "name": r["name"],
                "qualified_name": qn,
                "parent_name": parent_name,
                "file": r["file_path"],
                "line_start": r["start_line"],
                "line_end": r["end_line"],
                "signature": props.get("signature"),
                "language": "",
                "complexity": props.get("complexity"),
            })

        # Intra-file edges: both endpoints must be in this file
        edges: List[Dict[str, Any]] = []
        if id_to_qname:
            ids_str = ",".join(str(i) for i in id_to_qname)
            for r in conn.execute(
                f"SELECT source_id, target_id, type FROM edges "
                f"WHERE source_id IN ({ids_str}) AND target_id IN ({ids_str})"
            ):
                src_qn = id_to_qname.get(r["source_id"])
                dst_qn = id_to_qname.get(r["target_id"])
                if src_qn and dst_qn:
                    edges.append({
                        "source_qname": src_qn,
                        "target_qname": dst_qn,
                        "kind": r["type"],
                    })
        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()


def get_node(project: str, qualified_name: str) -> Optional[Dict[str, Any]]:
    """Return a single node's detail by qualified name."""
    found = _find_project(project)
    if not found:
        return None
    db_path, internal = found
    conn = _connect(db_path)
    if conn is None:
        return None
    try:
        r = conn.execute(
            "SELECT label, name, qualified_name, file_path, "
            "start_line, end_line, properties "
            "FROM nodes WHERE qualified_name=? LIMIT 1",
            (qualified_name,),
        ).fetchone()
        if not r:
            return None
        props = _props(r["properties"])
        return {
            "kind": r["label"].lower(),
            "name": r["name"],
            "qualified_name": r["qualified_name"],
            "file": r["file_path"],
            "line_start": r["start_line"],
            "line_end": r["end_line"],
            "signature": props.get("signature"),
            "language": "",
            "complexity": props.get("complexity"),
            "docstring": props.get("summary"),
        }
    finally:
        conn.close()


def delete_project(project: str) -> Dict[str, Any]:
    """Delete the project DB file for *project*.

    In v0.10.0+ each project is a standalone DB file — deletion is just
    removing the file. The engine keeps no central registry to update.
    """
    found = _find_project(project)
    if not found:
        return {"error": f"project '{project}' not indexed", "deleted": False}
    db_path, _ = found
    try:
        os.remove(db_path)
        # Also remove WAL/SHM sidecar files if present
        for suffix in ("-wal", "-shm"):
            side = db_path + suffix
            if os.path.exists(side):
                try:
                    os.remove(side)
                except OSError:
                    pass
        logger.info("codegraph_admin: deleted project=%s db=%s", project, db_path)
        return {"deleted": True, "project": project}
    except OSError as exc:
        logger.exception("codegraph_admin: delete failed for project=%s", project)
        return {"error": str(exc), "deleted": False}


async def get_layout(
    project: str,
    level: str = "overview",
    center_node: Optional[str] = None,
    radius: int = 2,
    max_nodes: int = 2000,
) -> Dict[str, Any]:
    """Compute a 3D force-directed graph layout for *project* via the engine.

    Unlike the read paths above, layout requires the engine's compiled
    Barnes-Hut layout algorithm (``layout3d.c``), so this goes through the
    same ephemeral MCP subprocess connect/execute/disconnect as reindex.
    """
    from app.services.mcp_client_manager import MCPClientManager
    from app.workflow.tools.codegraph_tools import (
        CODEGRAPH_SERVER_ID,
        codegraph_inline_config,
    )

    # The engine's own project identifier (derived from the indexed repo_path,
    # e.g. "app-data-indexed_repos-compliance-acuris-api") is not the same as
    # the friendly repo_name (root_path basename) used everywhere else in this
    # module — get_layout is the one read path that goes through the live
    # engine, so it needs the internal name, not the display name.
    found = _find_project(project)
    if not found:
        return {"error": f"project '{project}' not indexed"}
    _db_path, internal_name = found

    manager = MCPClientManager()
    connected = False
    try:
        connected = await manager.connect_server(
            CODEGRAPH_SERVER_ID, codegraph_inline_config()
        )
        if not connected:
            return {"error": "could not start codegraph engine"}

        args: Dict[str, Any] = {
            "project": internal_name,
            "level": level,
            "radius": radius,
            "max_nodes": max_nodes,
        }
        if center_node:
            args["center_node"] = center_node

        result = await manager.execute_tool(
            server_id=CODEGRAPH_SERVER_ID,
            tool_name="get_layout",
            arguments=args,
            tool_timeout=60,
        )
        if result.get("isError"):
            from app.crawler.background_indexer import _content_text
            return {"error": _content_text(result)}

        # NOTE: _content_text truncates to 500 chars (fine for short error
        # messages, not for a full layout payload) — extract the raw text here.
        content = result.get("content")
        text = None
        if isinstance(content, list) and content:
            item = content[0]
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
        if not text:
            return {"error": "empty layout response"}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": f"malformed layout response: {text[:200]}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("codegraph_admin: get_layout failed for project=%s", project)
        return {"error": str(exc)}
    finally:
        if connected:
            try:
                await manager.disconnect_all()
            except Exception:  # noqa: BLE001
                pass


async def reindex_project(project: str) -> Dict[str, Any]:
    """Re-run codegraph indexing for *project* (fast mode) via the engine."""
    from app.services.mcp_client_manager import MCPClientManager
    from app.workflow.tools.codegraph_tools import (
        CODEGRAPH_SERVER_ID,
        codegraph_inline_config,
    )

    repo_path = os.path.join(settings.repos_base_path, project)
    if not os.path.isdir(repo_path):
        return {"error": f"repo path not found: {repo_path}", "reindexed": False}

    manager = MCPClientManager()
    connected = False
    try:
        connected = await manager.connect_server(
            CODEGRAPH_SERVER_ID, codegraph_inline_config()
        )
        if not connected:
            return {"error": "could not start codegraph engine", "reindexed": False}
        result = await manager.execute_tool(
            server_id=CODEGRAPH_SERVER_ID,
            tool_name="index_repository",
            arguments={"repo_path": repo_path, "mode": "fast"},
            tool_timeout=0,
        )
        if result.get("isError"):
            from app.crawler.background_indexer import _content_text
            return {"error": _content_text(result), "reindexed": False}
        return {"reindexed": True, "project": project}
    except Exception as exc:  # noqa: BLE001
        logger.exception("codegraph_admin: reindex failed for project=%s", project)
        return {"error": str(exc), "reindexed": False}
    finally:
        if connected:
            try:
                await manager.disconnect_all()
            except Exception:  # noqa: BLE001
                pass
