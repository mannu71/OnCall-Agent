"""Generic repo file tools exposed as LangChain StructuredTools for the agent.

These encode NO language, framework, or cloud knowledge — they just expose a
repo's text to the agent (jailed under REPOS_BASE_PATH, honouring the same prune
/ .gitignore rules as indexing), the way a coding agent uses ripgrep + read:

1. ``repo_grep``      — regex/text search across file contents (ripgrep-like)
2. ``repo_read_file`` — read a file by path, numbered lines (optional anchors)
3. ``repo_list_files`` — list file paths, optionally glob-filtered

They complement the codegraph symbol-graph tools (``codegraph__*``): the graph
tools answer "where is symbol X / who calls it", these answer "find this text /
read this file" for config, IaC, and languages the graph doesn't cover.

``repo_read_file(with_anchors=True)`` is the producer of hashline anchors that
``edit_file`` consumes for drift-tolerant start_anchor/end_anchor edits.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field as PydanticField

logger = logging.getLogger(__name__)

# Ceiling for a single tool's serialized JSON output. Grep/list/read are already
# bounded (max_results / limit / max_lines), so this is a backstop against a
# pathological single line; kept generous.
_OUTPUT_MAX_CHARS = 60_000


def _cap(text: str, max_chars: int = _OUTPUT_MAX_CHARS) -> str:
    """Boundary-aware char cap so a tool result can't blow up the context."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)
    cut = text.rfind("\n", 0, max_chars)
    if cut < max_chars // 2:
        cut = max_chars
    return text[:cut] + f"\n…[truncated; showing {cut} of {total} chars. Narrow the query.]"


async def _resolve_repos(
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
) -> List[str]:
    """Resolve a query scope to a deduplicated, ordered list of repo names."""
    out: List[str] = list(repos) if repos else ([repo] if repo else [])
    seen: set = set()
    return [r for r in out if r and not (r in seen or seen.add(r))]


async def _grep(
    pattern: str,
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    glob: Optional[str] = None,
    ignore_case: bool = True,
    max_results: int = 80,
) -> Dict[str, Any]:
    from app.services.repo_files import grep_repo

    scope = await _resolve_repos(repo, repos)
    if not scope:
        return {"error": "no repo/repos resolved", "pattern": pattern}
    per = max(5, max_results // max(1, len(scope)))
    matches: List[Dict[str, Any]] = []
    for r in scope:
        if len(matches) >= max_results:
            break
        try:
            for relpath, line_no, text in await grep_repo(
                r, pattern, glob=glob, ignore_case=ignore_case, max_results=per,
            ):
                matches.append({"repo": r, "file": relpath, "line": line_no, "text": text})
        except Exception as exc:  # noqa: BLE001
            logger.debug("repo_grep: %s failed in %s: %s", pattern, r, exc)
    return {"pattern": pattern, "repos": scope, "count": len(matches),
            "matches": matches[:max_results]}


async def _read_file(
    repo: str,
    path: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    max_lines: int = 400,
    with_anchors: bool = False,
) -> Dict[str, Any]:
    from app.services.repo_files import read_repo_file

    try:
        content = await read_repo_file(repo, path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "repo": repo, "file": path}
    lines = content.splitlines()
    total = len(lines)
    s = max(1, start or 1)
    e = min(total, (end or (s + max_lines - 1)))
    if e - s + 1 > max_lines:
        e = s + max_lines - 1
    if with_anchors:
        from app.harness.hashline import line_hash
        body = "\n".join(
            f"L{i}#{line_hash(lines[i - 1])}: {lines[i - 1]}" for i in range(s, e + 1)
        )
    else:
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(s, e + 1))
    return {"repo": repo, "file": path, "start": s, "end": e,
            "total_lines": total, "content": body}


async def _list_files(
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    glob: Optional[str] = None,
    limit: int = 400,
) -> Dict[str, Any]:
    from app.services.repo_files import list_repo_files

    scope = await _resolve_repos(repo, repos)
    if not scope:
        return {"error": "no repo/repos resolved"}
    per = max(20, limit // max(1, len(scope)))
    files: List[Dict[str, str]] = []
    for r in scope:
        try:
            for relpath in await list_repo_files(r, glob=glob, limit=per):
                files.append({"repo": r, "file": relpath})
        except Exception as exc:  # noqa: BLE001
            logger.debug("repo_list_files: %s failed: %s", r, exc)
    return {"repos": scope, "count": len(files), "files": files[:limit]}


def build_repo_file_tools(
    repos: Optional[List[Dict[str, str]]] = None,
) -> List[StructuredTool]:
    """Build the 3 generic repo file tools (grep / read / list).

    Args:
        repos: List of repo configs [{"name": str, ...}]. Used for the hint
               appended to tool descriptions and as the default multi-repo scope;
               repo boundaries are enforced by REPOS_BASE_PATH at runtime.
    """
    _repo_names = [r.get("name", "") for r in (repos or []) if r.get("name")]
    repo_hint = f" Available repositories: {_repo_names}." if _repo_names else ""
    if len(_repo_names) > 1:
        repo_hint += " Omit repo to search across all connected repos at once."

    class _GrepInput(BaseModel):
        pattern: str = PydanticField(..., description="Regex (or literal) to search file contents for.")
        repo: Optional[str] = PydanticField(None, description="Repo to search. Omit to search all connected repos.")
        glob: Optional[str] = PydanticField(None, description="Optional file glob filter, e.g. '*.cs' or 'appsettings*.json'.")
        max_results: int = PydanticField(80, description="Max matches to return.")

    class _ReadFileInput(BaseModel):
        repo: str = PydanticField(..., description="Repository name under REPOS_BASE_PATH.")
        path: str = PydanticField(..., description="File path relative to the repo root.")
        start: Optional[int] = PydanticField(None, description="First line (1-based) to read.")
        end: Optional[int] = PydanticField(None, description="Last line to read.")
        with_anchors: bool = PydanticField(
            False,
            description="Prefix each line with a hashline anchor 'L<n>#<hash>' (instead of '<n>:') "
                        "so it can be cited to edit_file start_anchor/end_anchor for a robust, "
                        "drift-tolerant edit. Set True only when about to edit this file.")

    class _ListFilesInput(BaseModel):
        repo: Optional[str] = PydanticField(None, description="Repo to list. Omit to list across all connected repos.")
        glob: Optional[str] = PydanticField(None, description="Optional file glob filter, e.g. '**/*.tf' or '*Controller.cs'.")
        limit: int = PydanticField(400, description="Max file paths to return.")

    async def _grep_tool(pattern: str, repo: Optional[str] = None,
                         glob: Optional[str] = None, max_results: int = 80) -> str:
        result = await _grep(pattern, repo=repo,
                             repos=(None if repo else (_repo_names or None)),
                             glob=glob, max_results=max_results)
        return _cap(json.dumps(result, default=str))

    async def _read_file_tool(repo: str, path: str, start: Optional[int] = None,
                              end: Optional[int] = None, with_anchors: bool = False) -> str:
        return _cap(json.dumps(
            await _read_file(repo, path, start=start, end=end, with_anchors=with_anchors),
            default=str))

    async def _list_files_tool(repo: Optional[str] = None, glob: Optional[str] = None,
                               limit: int = 400) -> str:
        result = await _list_files(repo=repo,
                                   repos=(None if repo else (_repo_names or None)),
                                   glob=glob, limit=limit)
        return _cap(json.dumps(result, default=str))

    tools = [
        StructuredTool.from_function(
            coroutine=_grep_tool,
            name="repo_grep",
            description=(
                "Regex/text search across repository file CONTENTS (like ripgrep) — any "
                "language, config, or IaC file. WHEN TO USE: find anything by its text — a "
                "URL/base address, a queue or topic name, an env/config key, a string literal, "
                "a call across services. Omit repo to search ALL connected repos. Then "
                "repo_read_file the hit."
                + repo_hint
            ),
            args_schema=_GrepInput,
        ),
        StructuredTool.from_function(
            coroutine=_read_file_tool,
            name="repo_read_file",
            description=(
                "Read any repository file by path, returning numbered lines (optional "
                "start/end range). WHEN TO USE: inspect a file you found via repo_grep / "
                "repo_list_files, or read code before editing it. Set with_anchors=True to get "
                "hashline anchors for a drift-tolerant edit_file edit."
            ),
            args_schema=_ReadFileInput,
        ),
        StructuredTool.from_function(
            coroutine=_list_files_tool,
            name="repo_list_files",
            description=(
                "List repository file paths (optionally filtered by a glob), across one repo "
                "or ALL connected repos. WHEN TO USE: orient in unfamiliar layout or find "
                "config/infra files (e.g. glob '**/*.tf', '*Controller.cs', 'appsettings*.json')."
                + repo_hint
            ),
            args_schema=_ListFilesInput,
        ),
    ]

    logger.info("build_repo_file_tools: created %d tools (repos=%s)", len(tools), _repo_names)
    return tools
