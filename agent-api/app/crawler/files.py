"""Local file crawler for code repositories.

Walks a directory under REPOS_BASE_PATH, applies include/exclude glob
patterns, honours .gitignore via pathspec, and returns a flat list of
(relative_path, content) tuples.

Directory pruning strategy (three layers, applied in order):
  1. Universal base set  — VCS dirs, IDE metadata, always noise.
  2. Tech-stack detection — scans root-level indicator files (package.json,
     *.csproj, go.mod, Cargo.toml, …) and adds the matching prune set so
     the list is dynamically correct for the actual repo language/framework.
  3. .gitignore           — repo's own gitignore is applied at both dir and
     file level via pathspec; this catches project-specific generated dirs
     that no static list can anticipate.

Every path is validated through app.core.security.check_path before the
file is opened — traversal outside the repos root raises PathJailError.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Include / exclude glob patterns (applied to file relpaths)
# ---------------------------------------------------------------------------

_DEFAULT_INCLUDE: Set[str] = {
    # General
    "*.py", "*.js", "*.jsx", "*.ts", "*.tsx",
    "*.go", "*.java", "*.kt", "*.rs",
    "*.c", "*.cpp", "*.h",
    "*.md", "*.yml", "*.yaml",
    # .NET / C#
    "*.cs", "*.razor", "*.cshtml",
    "*.csproj", "*.sln",
}

_DEFAULT_EXCLUDE: Set[str] = {
    "*.pyc", "*.log", "*.min.js", "*.min.css",
}

# ---------------------------------------------------------------------------
# Universal base prune set — always noisy regardless of tech stack
# ---------------------------------------------------------------------------

_BASE_PRUNE_DIRS: Set[str] = {
    # VCS internals — always noise
    ".git", ".svn", ".hg",
    # IDE metadata — always noise
    ".idea", ".vs", ".vscode",
    # Python bytecode/env — any project can accidentally have these
    # even if it's not a Python project (e.g. a dev ran `python -m venv`)
    "__pycache__", ".pytest_cache",
    ".venv", "venv", "env",
    # Node — same reasoning; a .NET project with a React frontend will have this
    "node_modules",
}

# ---------------------------------------------------------------------------
# Per-stack prune sets — added dynamically when indicator files are found
# ---------------------------------------------------------------------------

# indicator file (or extension) → directories to prune for that stack
_STACK_INDICATORS: List[Tuple[str, Set[str]]] = [
    # Python
    (
        "requirements.txt|setup.py|pyproject.toml|Pipfile|setup.cfg",
        {".venv", "venv", "env", ".env",
         ".pytest_cache", ".mypy_cache", ".ruff_cache",
         "dist", "build", "__pycache__"},
    ),
    # Node / JS / TS
    (
        "package.json",
        {"node_modules", "dist", "build", "out", ".next", ".nuxt",
         ".cache", ".parcel-cache", ".nyc_output", "__mocks__",
         "storybook-static", "coverage"},
    ),
    # .NET / C#
    (
        "*.csproj|*.sln|*.fsproj|*.vbproj|global.json",
        {"bin", "obj", "publish", "TestResults",
         ".sonarqube", ".store", "coverlet",
         "packages",              # old-style NuGet restore
         ".vs",                   # already in base, kept for clarity
         },
    ),
    # Java / Kotlin (Maven or Gradle)
    (
        "pom.xml|build.gradle|build.gradle.kts|settings.gradle|settings.gradle.kts",
        {"target", ".gradle", "out", "build"},
    ),
    # Rust
    (
        "Cargo.toml",
        {"target"},
    ),
    # Go
    (
        "go.mod",
        {"vendor"},
    ),
    # iOS / Swift
    (
        "Podfile|Package.swift|*.xcodeproj|*.xcworkspace",
        {"Pods", ".build", "DerivedData"},
    ),
    # PHP / Composer
    (
        "composer.json",
        {"vendor"},
    ),
    # Ruby
    (
        "Gemfile",
        {"vendor", ".bundle"},
    ),
    # Terraform / Infra
    (
        "*.tf|*.tfvars",
        {".terraform"},
    ),
    # Generic local-dev tooling (present in any stack)
    (
        "docker-compose.yml|docker-compose.yaml",
        {"localstack"},
    ),
]

_DEFAULT_MAX_FILE_SIZE = 100_000  # bytes
_DEFAULT_MAX_FILES    = 5_000     # hard cap — prevents hanging on huge repos


# ---------------------------------------------------------------------------
# Stack detection
# ---------------------------------------------------------------------------

def _detect_prune_dirs(directory: str) -> Set[str]:
    """Scan the root of *directory* and return a dynamically-built prune set.

    For each stack in _STACK_INDICATORS we check whether any of the pipe-
    separated indicator patterns match a file at the repo root.  When a match
    is found the corresponding directory set is added to the result.

    This means:
    - A .NET project gets bin/obj/TestResults/... pruned automatically.
    - A Node project gets node_modules/dist/... pruned automatically.
    - A Python project gets .venv/venv/... pruned automatically.
    - A mixed-stack project (e.g. .NET + React frontend) gets both sets.
    """
    try:
        root_entries = set(os.listdir(directory))
    except OSError:
        return set()

    extra: Set[str] = set()
    detected: List[str] = []

    for indicators_str, prune_set in _STACK_INDICATORS:
        patterns = indicators_str.split("|")
        matched = False
        for pattern in patterns:
            # Exact filename match
            if pattern in root_entries:
                matched = True
                break
            # Glob match (e.g. "*.csproj")
            if any(fnmatch.fnmatch(entry, pattern) for entry in root_entries):
                matched = True
                break
        if matched:
            extra.update(prune_set)
            detected.append(indicators_str.split("|")[0])

    if detected:
        logger.info(
            "crawl_local_files: detected stacks %s → pruning %d extra dir names",
            detected, len(extra),
        )
    else:
        logger.info(
            "crawl_local_files: no stack detected at '%s', using base prune set only",
            directory,
        )

    return extra


# ---------------------------------------------------------------------------
# .gitignore support
# ---------------------------------------------------------------------------

def _build_gitignore_spec(directory: str):
    """Load .gitignore from *directory* and return a pathspec matcher, or None."""
    try:
        import pathspec  # type: ignore[import]
    except ImportError:
        logger.warning("pathspec not installed — .gitignore patterns ignored")
        return None

    gitignore = os.path.join(directory, ".gitignore")
    if not os.path.exists(gitignore):
        return None
    try:
        with open(gitignore, encoding="utf-8-sig") as f:
            lines = f.readlines()
        spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        logger.info("crawl_local_files: loaded .gitignore from '%s'", directory)
        return spec
    except Exception as exc:
        logger.warning("Could not parse .gitignore at %s: %s", gitignore, exc)
        return None


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

def _is_excluded(relpath: str, patterns: Set[str], gitignore_spec) -> bool:
    if gitignore_spec and gitignore_spec.match_file(relpath):
        return True
    return any(fnmatch.fnmatch(relpath, p) for p in patterns)


def _is_included(relpath: str, patterns: Set[str]) -> bool:
    if not patterns:
        return True
    return any(fnmatch.fnmatch(relpath, p) for p in patterns)


# ---------------------------------------------------------------------------
# Synchronous walk (runs in a thread)
# ---------------------------------------------------------------------------

def _crawl_sync(
    directory: str,
    jail: str,
    include_patterns: Set[str],
    exclude_patterns: Set[str],
    max_file_size: int,
    max_files: int,
    prune_dirs: Set[str],
) -> List[Tuple[str, str]]:
    """Synchronous crawl — run via asyncio.to_thread()."""
    from app.core.security import check_path, PathJailError

    gitignore_spec = _build_gitignore_spec(directory)
    results: List[Tuple[str, str]] = []

    for root, dirs, files in os.walk(directory):
        # ── Prune directories in-place (three layers) ─────────────────────
        # Layer 1+2: combined static+dynamic name set (O(1) lookup per dir)
        # Layer 3:   gitignore + glob patterns on the relative path
        dirs[:] = [
            d for d in dirs
            if d not in prune_dirs
            and not _is_excluded(
                os.path.relpath(os.path.join(root, d), directory),
                exclude_patterns,
                gitignore_spec,
            )
        ]

        for filename in files:
            if len(results) >= max_files:
                logger.warning(
                    "crawl_local_files: reached max_files=%d for '%s' — "
                    "truncating. Narrow include_patterns to reduce scope.",
                    max_files, os.path.basename(directory),
                )
                return results

            abs_path = os.path.join(root, filename)
            relpath  = os.path.relpath(abs_path, directory)

            if _is_excluded(relpath, exclude_patterns, gitignore_spec):
                continue
            if not _is_included(relpath, include_patterns):
                continue
            try:
                if os.path.getsize(abs_path) > max_file_size:
                    logger.debug("Skipping large file: %s", relpath)
                    continue
            except OSError:
                continue

            try:
                check_path(abs_path, jail)
            except PathJailError:
                logger.warning("Path jail violation, skipping: %s", abs_path)
                continue

            try:
                with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                    content = f.read()
                results.append((relpath, content))
            except Exception as exc:
                logger.debug("Could not read %s: %s", abs_path, exc)

    return results


# ---------------------------------------------------------------------------
# Public async entry point
# ---------------------------------------------------------------------------

async def crawl_local_files(
    repo_name: str,
    include_patterns: Optional[Set[str]] = None,
    exclude_patterns: Optional[Set[str]] = None,
    max_file_size: int = _DEFAULT_MAX_FILE_SIZE,
    max_files: int = _DEFAULT_MAX_FILES,
) -> List[Tuple[str, str]]:
    """Crawl *repo_name* under REPOS_BASE_PATH and return (path, content) tuples.

    Directory pruning is applied in three layers:
      1. Universal base (_BASE_PRUNE_DIRS) — .git, IDE dirs, always noisy.
      2. Stack-specific (_detect_prune_dirs) — detects tech stack from root
         indicator files (package.json, *.csproj, go.mod, Cargo.toml, …)
         and adds the relevant prune dirs automatically.
      3. .gitignore — repo's own ignore rules applied to both dirs and files.

    Args:
        repo_name:        Repository directory name under REPOS_BASE_PATH.
        include_patterns: Glob patterns for files to include.
                          Defaults to common source file extensions.
        exclude_patterns: Additional glob patterns to exclude.
        max_file_size:    Skip files larger than this (bytes). Default 100 KB.
        max_files:        Hard cap on returned files. Default 5,000.

    Returns:
        Sorted list of (relative_path, content) tuples.

    Raises:
        ValueError: If the repository directory does not exist.
    """
    repos_root = os.getenv("REPOS_BASE_PATH", "/tmp/indexed_repos")
    directory  = os.path.join(repos_root, repo_name)

    if not os.path.isdir(directory):
        raise ValueError(
            f"Repository '{repo_name}' not found under REPOS_BASE_PATH ({repos_root})"
        )

    inc = include_patterns if include_patterns is not None else _DEFAULT_INCLUDE
    exc = exclude_patterns if exclude_patterns is not None else _DEFAULT_EXCLUDE

    # Build the dynamic prune set in the calling thread (fast — just os.listdir)
    prune_dirs = _BASE_PRUNE_DIRS | _detect_prune_dirs(directory)
    logger.info(
        "Crawling repo '%s' at %s (prune_dirs=%d entries)",
        repo_name, directory, len(prune_dirs),
    )

    files = await asyncio.to_thread(
        _crawl_sync, directory, repos_root, inc, exc,
        max_file_size, max_files, prune_dirs,
    )

    logger.info("Crawled %d files from '%s'", len(files), repo_name)
    return sorted(files, key=lambda t: t[0])
