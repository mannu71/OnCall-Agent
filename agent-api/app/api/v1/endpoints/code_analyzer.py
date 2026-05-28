"""Code analyzer support endpoints.

Discovery of repositories visible to the agent. Used by the
"Configure Code Analyzer Node" UI so users can pick from real
mounted repos instead of typing container paths by hand.

Repos are discovered under ``REPOS_BASE_PATH`` (defaults to
``/app/data/indexed_repos``), which is mounted read-only by
``docker-compose.yml`` from the host's repo root. The path-jail
is enforced at tool-use time via ``app.core.security.check_path``.

Endpoints:
  - ``GET /code-analyzer/repos`` → list discovered repositories.
  - ``GET /code-analyzer/repos/{repo_name}`` → metadata for one repo.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code-analyzer", tags=["code-analyzer"])

_DEFAULT_REPOS_ROOT = "/app/data/indexed_repos"

# In-memory TTL cache for /code-analyzer/repos. The walk over
# REPOS_BASE_PATH (~160 ms for 20 repos) is a noticeable hit when the
# Configure CodeAnalyzer Node panel opens repeatedly. Subsequent opens
# within the TTL get a sub-millisecond response. Callers that need
# fresh data pass ``?refresh=true``.
_LIST_CACHE_TTL_SECONDS = 60.0
_LIST_CACHE: dict[str, Tuple[float, "RepoListResponse"]] = {}

# Quick-detect language by signature file. Cheap; reads only directory
# entry names — never opens file contents.
_LANGUAGE_SIGNALS: list[tuple[str, list[str]]] = [
    ("python",     ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"]),
    ("typescript", ["tsconfig.json"]),
    ("javascript", ["package.json"]),
    ("java",       ["pom.xml", "build.gradle", "build.gradle.kts"]),
    ("kotlin",     ["build.gradle.kts"]),
    ("go",         ["go.mod"]),
    ("rust",       ["Cargo.toml"]),
    ("ruby",       ["Gemfile"]),
    ("dart",       ["pubspec.yaml"]),
    ("php",        ["composer.json"]),
]

_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py":     "python",
    ".ts":     "typescript",
    ".tsx":    "typescript",
    ".js":     "javascript",
    ".jsx":    "javascript",
    ".cs":     "csharp",
    ".csproj": "csharp",
    ".sln":    "csharp",
    ".java":   "java",
    ".kt":     "kotlin",
    ".go":     "go",
    ".rs":     "rust",
    ".rb":     "ruby",
    ".php":    "php",
    ".dart":   "dart",
}


# ─────────────────────────────────────────────────────────────────────────────
# Response models
# ─────────────────────────────────────────────────────────────────────────────

class RepoInfo(BaseModel):
    name: str
    path: str
    is_git: bool
    detected_languages: List[str]
    suggested_language: str
    file_count_sample: int  # capped scan — see _scan_extensions


class RepoListResponse(BaseModel):
    base_path: str
    base_exists: bool
    repos: List[RepoInfo]


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────

def _repos_root() -> str:
    return os.environ.get("REPOS_BASE_PATH", _DEFAULT_REPOS_ROOT)


def _list_repo_dirs(base: Path) -> Iterable[Path]:
    """Yield immediate subdirectories of *base*, skipping hidden/system dirs."""
    try:
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith(".") or name in {"__pycache__", "node_modules"}:
                continue
            yield entry
    except (OSError, PermissionError) as exc:
        logger.warning("Cannot enumerate repos under %s: %s", base, exc)


def _scan_extensions(repo: Path, file_cap: int = 50) -> tuple[set[str], int]:
    """Walk up to ``file_cap`` files and collect detected languages.

    Returns ``(languages, files_scanned)``. Walking stops once the cap
    is reached so very large repos don't slow the endpoint. We only look
    at file extensions — never read file content.

    NOTE on cap: 50 is plenty for language detection — you only need
    to see ONE ``.py`` file to know a repo is Python. The lower cap
    was a deliberate cut after the cold-start latency for a Windows →
    Linux bind mount was measured at 76 s with cap=200; cap=50 brings
    it to a few seconds and detection accuracy is unaffected for any
    non-trivial repo.
    """
    langs: set[str] = set()
    scanned = 0
    try:
        for root, dirs, files in os.walk(repo):
            # Prune common heavy dirs in-place to save a lot of walks.
            dirs[:] = [
                d for d in dirs
                if d not in {".git", "node_modules", "__pycache__",
                             "dist", "build", "target", ".venv", "venv"}
                and not d.startswith(".")
            ]
            for fname in files:
                scanned += 1
                ext = os.path.splitext(fname)[1].lower()
                lang = _EXTENSION_LANGUAGES.get(ext)
                if lang:
                    langs.add(lang)
                if scanned >= file_cap:
                    return langs, scanned
    except (OSError, PermissionError) as exc:
        logger.debug("Scan terminated for %s: %s", repo, exc)
    return langs, scanned


def _suggest_language(detected: set[str]) -> str:
    """Pick the single best default language for the dropdown.

    Priority follows the order of common stack volume; falls back to
    "python" so the form is always pre-fillable.
    """
    preference = ["python", "typescript", "csharp", "java",
                  "kotlin", "go", "rust", "ruby", "javascript", "dart", "php"]
    for lang in preference:
        if lang in detected:
            return lang
    return "python"


def _signature_languages(repo: Path) -> set[str]:
    """Languages indicated by signature files in the repo root."""
    try:
        names = {p.name for p in repo.iterdir() if p.is_file()}
    except (OSError, PermissionError):
        return set()
    out: set[str] = set()
    for lang, files in _LANGUAGE_SIGNALS:
        if any(f in names for f in files):
            out.add(lang)
    return out


def _build_repo_info(repo: Path) -> Optional[RepoInfo]:
    """Inspect a single repo dir and return its descriptor, or None if empty."""
    is_git = (repo / ".git").exists()
    sig_langs = _signature_languages(repo)
    ext_langs, scanned = _scan_extensions(repo)
    detected = sig_langs | ext_langs
    if not detected and scanned == 0:
        # Empty or unreadable directory — skip.
        return None
    return RepoInfo(
        name=repo.name,
        path=str(repo).replace("\\", "/"),
        is_git=is_git,
        detected_languages=sorted(detected),
        suggested_language=_suggest_language(detected),
        file_count_sample=scanned,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

async def _compute_list_response(base_str: str) -> RepoListResponse:
    """Pure-function builder used by both the cached and bypass paths.

    Per-repo scans are run concurrently via ``asyncio.gather`` + a thread
    pool. On a Windows → Linux Docker bind mount the bottleneck is
    per-stat round-trip latency through the VM boundary, not CPU; running
    ~20 walks in parallel drops a cold call from ~76 s to a few seconds.
    """
    base = Path(base_str)
    if not base.is_dir():
        logger.info("REPOS_BASE_PATH %s does not exist or is not a directory", base_str)
        return RepoListResponse(base_path=base_str, base_exists=False, repos=[])

    repo_paths = list(_list_repo_dirs(base))
    if not repo_paths:
        return RepoListResponse(base_path=base_str, base_exists=True, repos=[])

    # Fan out one scan per repo. ``asyncio.to_thread`` releases the GIL
    # for the os.walk syscalls and ``gather`` runs them concurrently.
    results = await asyncio.gather(
        *(asyncio.to_thread(_build_repo_info, p) for p in repo_paths),
        return_exceptions=True,
    )

    repos: List[RepoInfo] = []
    for path, info in zip(repo_paths, results):
        if isinstance(info, Exception):
            logger.warning("Repo scan failed for %s: %s", path, info)
            continue
        if info is not None:
            repos.append(info)
    # Preserve case-insensitive name ordering (the dirs are already
    # sorted by _list_repo_dirs; gather is order-preserving over the
    # input iterable).
    return RepoListResponse(base_path=base_str, base_exists=True, repos=repos)


@router.get("/repos", response_model=RepoListResponse)
async def list_repos(
    refresh: bool = Query(
        False,
        description="Bypass the 60-second in-memory cache and walk the filesystem now.",
    ),
) -> RepoListResponse:
    """List repositories discovered under REPOS_BASE_PATH.

    Returns an empty ``repos`` list (rather than 404) when the base
    directory is missing — the UI can show a configuration hint
    rather than treating it as an error.

    Cached for ~60 seconds per ``REPOS_BASE_PATH`` value. Pass
    ``?refresh=true`` to force a fresh filesystem walk (e.g. after
    creating or removing a repo on the host).
    """
    base_str = _repos_root()
    now = time.monotonic()

    if not refresh:
        cached = _LIST_CACHE.get(base_str)
        if cached is not None:
            expires_at, response = cached
            if expires_at > now:
                return response

    response = await _compute_list_response(base_str)
    _LIST_CACHE[base_str] = (now + _LIST_CACHE_TTL_SECONDS, response)
    return response


@router.get("/repos/{repo_name}", response_model=RepoInfo)
async def get_repo(repo_name: str) -> RepoInfo:
    """Fetch metadata for a single repository (jailed under REPOS_BASE_PATH)."""
    base = Path(_repos_root())
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="REPOS_BASE_PATH not configured")

    # Path-jail: forbid traversal and reject names that resolve outside base.
    candidate = (base / repo_name).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid repo name")

    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")

    info = _build_repo_info(candidate)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' is empty")
    return info
