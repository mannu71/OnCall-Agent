"""Code intelligence MCP tools.

Five tools that let agents query the indexed code chunks:
1. search_code       — semantic similarity search across all indexed repos
2. get_function      — exact lookup by function/class name
3. get_callers       — find functions that call a given function
4. get_recent_changes — git log for a file (path-jailed)
5. get_file_context  — read specific lines of a source file (path-jailed)

All file access goes through app.core.security.check_path.
All outbound HTTP calls (none here — git is local) would go through check_ssrf.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.security import check_path, PathJailError

logger = logging.getLogger(__name__)

# Default repos root — override via REPOS_BASE_PATH env var
_DEFAULT_REPOS_ROOT = "/tmp/indexed_repos"


def _repos_root() -> str:
    import os
    return os.getenv("REPOS_BASE_PATH", _DEFAULT_REPOS_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1: search_code
# ─────────────────────────────────────────────────────────────────────────────

async def search_code(
    query: str,
    repo: Optional[str] = None,
    limit: int = 5,
    language: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Semantic search across indexed code chunks.

    Args:
        query: Natural language or code snippet to search for.
        repo: Optional repository name to restrict search.
        limit: Maximum results to return (max 20).
        language: Optional filter ('python' | 'typescript').

    Returns:
        List of matching code chunks with similarity scores.
    """
    limit = min(limit, 20)

    # Embed the query using Bedrock Titan
    from app.services.code_indexer import _embed_text
    embedding = await _embed_text(query)
    if not embedding:
        return [{"error": "Embedding failed — Bedrock may not be configured"}]

    emb_str = f"[{','.join(str(v) for v in embedding)}]"

    async with AsyncSessionLocal() as session:
        conditions = ["1=1"]
        params: Dict[str, Any] = {"embedding": emb_str, "limit": limit}

        if repo:
            conditions.append("repo_name = :repo")
            params["repo"] = repo
        if language:
            conditions.append("language = :language")
            params["language"] = language

        where = " AND ".join(conditions)
        result = await session.execute(
            text(f"""
                SELECT repo_name, file_path, name, chunk_type, signature,
                       docstring, line_start, line_end, language,
                       1 - (embedding <=> :embedding::vector) AS similarity
                  FROM code_chunks
                 WHERE {where}
                 ORDER BY embedding <=> :embedding::vector
                 LIMIT :limit
            """),
            params,
        )
        rows = result.all()

    return [
        {
            "repo_name":  r.repo_name,
            "file_path":  r.file_path,
            "name":       r.name,
            "type":       r.chunk_type,
            "signature":  r.signature,
            "docstring":  r.docstring,
            "line_start": r.line_start,
            "line_end":   r.line_end,
            "language":   r.language,
            "similarity": round(float(r.similarity), 4),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2: get_function
# ─────────────────────────────────────────────────────────────────────────────

async def get_function(
    name: str,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Exact lookup of a function or class by name.

    Args:
        name: Exact function or class name.
        repo: Optional repository filter.

    Returns:
        Chunk dict with full body, or an error dict.
    """
    async with AsyncSessionLocal() as session:
        params: Dict[str, Any] = {"name": name}
        extra = "AND repo_name = :repo" if repo else ""
        if repo:
            params["repo"] = repo

        result = await session.execute(
            text(f"""
                SELECT repo_name, file_path, name, chunk_type, signature,
                       body, docstring, line_start, line_end, language
                  FROM code_chunks
                 WHERE name = :name {extra}
                 ORDER BY indexed_at DESC
                 LIMIT 1
            """),
            params,
        )
        row = result.one_or_none()

    if row is None:
        return {"error": f"Function '{name}' not found in the code index"}

    return {
        "repo_name":  row.repo_name,
        "file_path":  row.file_path,
        "name":       row.name,
        "type":       row.chunk_type,
        "signature":  row.signature,
        "body":       row.body,
        "docstring":  row.docstring,
        "line_start": row.line_start,
        "line_end":   row.line_end,
        "language":   row.language,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3: get_callers
# ─────────────────────────────────────────────────────────────────────────────

async def get_callers(
    function_name: str,
    repo: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Find all functions that call *function_name*.

    Args:
        function_name: The callee function name to search for.
        repo: Optional repository filter.
        limit: Maximum results.

    Returns:
        List of caller chunks.
    """
    async with AsyncSessionLocal() as session:
        params: Dict[str, Any] = {"callee": function_name, "limit": limit}
        extra = "AND cc.repo_name = :repo" if repo else ""
        if repo:
            params["repo"] = repo

        result = await session.execute(
            text(f"""
                SELECT c.repo_name, c.file_path, c.name, c.chunk_type,
                       c.signature, c.line_start, c.line_end
                  FROM code_calls cc
                  JOIN code_chunks c ON c.id = cc.caller_chunk
                 WHERE cc.callee_name = :callee {extra}
                 ORDER BY c.repo_name, c.file_path
                 LIMIT :limit
            """),
            params,
        )
        rows = result.all()

    return [
        {
            "repo_name":  r.repo_name,
            "file_path":  r.file_path,
            "name":       r.name,
            "type":       r.chunk_type,
            "signature":  r.signature,
            "line_start": r.line_start,
            "line_end":   r.line_end,
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4: get_recent_changes
# ─────────────────────────────────────────────────────────────────────────────

async def get_recent_changes(
    file_path: str,
    repo: str,
    days: int = 7,
    max_commits: int = 10,
) -> Dict[str, Any]:
    """Return recent git log entries for a file inside a local repository.

    All file access is validated against the REPOS_BASE_PATH jail.

    Args:
        file_path: Path relative to the repository root.
        repo: Repository name (must exist under REPOS_BASE_PATH).
        days: How many days of history to fetch.
        max_commits: Maximum number of commits to return.
    """
    root = Path(_repos_root()) / repo
    try:
        safe_path = check_path(root / file_path, root)
    except PathJailError as exc:
        return {"error": str(exc)}

    if not root.exists():
        return {"error": f"Repository '{repo}' not found at {root}"}

    def _run_git() -> List[Dict[str, str]]:
        result = subprocess.run(
            [
                "git", "-C", str(root),
                "log", f"--since={days} days ago",
                f"-n{max_commits}",
                "--pretty=format:%H|%an|%ad|%s",
                "--date=short",
                "--", str(safe_path.relative_to(root)),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash":    parts[0][:12],
                    "author":  parts[1],
                    "date":    parts[2],
                    "message": parts[3],
                })
        return commits

    loop = asyncio.get_event_loop()
    try:
        commits = await loop.run_in_executor(None, _run_git)
    except subprocess.TimeoutExpired:
        return {"error": "git log timed out"}
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "repo":     repo,
        "file":     file_path,
        "days":     days,
        "commits":  commits,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5: get_file_context
# ─────────────────────────────────────────────────────────────────────────────

async def get_file_context(
    file_path: str,
    repo: str,
    line_start: int,
    line_end: int,
) -> Dict[str, Any]:
    """Read specific lines from a source file inside a local repository.

    Path is validated against REPOS_BASE_PATH jail.

    Args:
        file_path: Relative path inside the repository.
        repo: Repository name under REPOS_BASE_PATH.
        line_start: First line to return (1-indexed).
        line_end: Last line to return (inclusive).
    """
    root = Path(_repos_root()) / repo
    try:
        safe_path = check_path(root / file_path, root)
    except PathJailError as exc:
        return {"error": str(exc)}

    if not safe_path.exists():
        return {"error": f"File not found: {file_path}"}

    line_start = max(1, line_start)
    line_end   = min(line_end, line_start + 199)  # cap at 200 lines

    try:
        lines = safe_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        selected = lines[line_start - 1 : line_end]
    except OSError as exc:
        return {"error": str(exc)}

    return {
        "repo":       repo,
        "file":       file_path,
        "line_start": line_start,
        "line_end":   line_start + len(selected) - 1,
        "content":    "\n".join(selected),
    }
