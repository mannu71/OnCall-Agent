"""Filesystem repo discovery — canonical path for Code Analyzer UI and services."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.core.exceptions import ConfigurationException, NotFoundException, ValidationException

logger = logging.getLogger(__name__)

_LIST_CACHE: dict[str, Tuple[float, Dict[str, Any]]] = {}

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


def repos_root() -> str:
    return settings.repos_base_path


def _list_repo_dirs(base: Path) -> Iterable[Path]:
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
    langs: set[str] = set()
    scanned = 0
    try:
        for root, dirs, files in os.walk(repo):
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
    preference = ["python", "typescript", "csharp", "java",
                  "kotlin", "go", "rust", "ruby", "javascript", "dart", "php"]
    for lang in preference:
        if lang in detected:
            return lang
    return "python"


def _signature_languages(repo: Path) -> set[str]:
    try:
        names = {p.name for p in repo.iterdir() if p.is_file()}
    except (OSError, PermissionError):
        return set()
    out: set[str] = set()
    for lang, files in _LANGUAGE_SIGNALS:
        if any(f in names for f in files):
            out.add(lang)
    return out


def build_repo_info(repo: Path) -> Optional[Dict[str, Any]]:
    """Inspect a single repo dir and return its descriptor, or None if empty."""
    is_git = (repo / ".git").exists()
    sig_langs = _signature_languages(repo)
    ext_langs, scanned = _scan_extensions(repo)
    detected = sig_langs | ext_langs
    if not detected and scanned == 0:
        return None
    return {
        "name": repo.name,
        "path": str(repo).replace("\\", "/"),
        "is_git": is_git,
        "detected_languages": sorted(detected),
        "suggested_language": _suggest_language(detected),
        "file_count_sample": scanned,
    }


async def compute_list_response(base_str: str) -> Dict[str, Any]:
    base = Path(base_str)
    if not base.is_dir():
        logger.info("REPOS_BASE_PATH %s does not exist or is not a directory", base_str)
        return {"base_path": base_str, "base_exists": False, "repos": []}

    repo_paths = list(_list_repo_dirs(base))
    if not repo_paths:
        return {"base_path": base_str, "base_exists": True, "repos": []}

    results = await asyncio.gather(
        *(asyncio.to_thread(build_repo_info, p) for p in repo_paths),
        return_exceptions=True,
    )

    repos: List[Dict[str, Any]] = []
    for path, info in zip(repo_paths, results):
        if isinstance(info, Exception):
            logger.warning("Repo scan failed for %s: %s", path, info)
            continue
        if info is not None:
            repos.append(info)
    return {"base_path": base_str, "base_exists": True, "repos": repos}


async def list_discovered_repos(*, refresh: bool = False) -> Dict[str, Any]:
    """List repositories on disk under ``settings.repos_base_path``."""
    base_str = repos_root()
    now = time.monotonic()
    ttl = settings.code_analyzer_list_cache_ttl_seconds

    if not refresh:
        cached = _LIST_CACHE.get(base_str)
        if cached is not None:
            expires_at, response = cached
            if expires_at > now:
                return response

    response = await compute_list_response(base_str)
    _LIST_CACHE[base_str] = (now + ttl, response)
    return response


async def get_discovered_repo(repo_name: str) -> Dict[str, Any]:
    """Fetch metadata for one repo, jailed under ``repos_base_path``."""
    base = Path(repos_root())
    if not base.is_dir():
        raise ConfigurationException(
            "REPOS_BASE_PATH is not configured or does not exist",
            details={"base_path": str(base)},
        )

    candidate = (base / repo_name).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        raise ValidationException(
            "Invalid repo name",
            details={"repo_name": repo_name},
        )

    if not candidate.is_dir():
        raise NotFoundException(
            f"Repository '{repo_name}' not found",
            details={"repo_name": repo_name},
        )

    info = build_repo_info(candidate)
    if info is None:
        raise NotFoundException(
            f"Repository '{repo_name}' is empty",
            details={"repo_name": repo_name},
        )
    return info
