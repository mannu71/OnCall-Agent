"""Git branch operations for on-disk repos under ``REPOS_BASE_PATH``.

The Codebase Explorer indexes whatever branch a repo has checked out on disk.
This module lets the UI inspect and switch that branch, then reindex, without
anyone shelling into the container. It is a thin, jailed wrapper over the ``git``
CLI (already present in the runtime image) — no GitPython dependency.

Design notes / guardrails:
  * Every path is resolved through ``check_path`` jailed to ``repos_base_path``,
    so a crafted ``repo_name`` cannot escape the repos root.
  * All subprocess calls use an explicit arg list (never ``shell=True``), a
    timeout, and ``GIT_TERMINAL_PROMPT=0`` so a repo whose remote needs
    credentials fails fast instead of hanging on an interactive auth prompt.
  * Branch switching **refuses** on a dirty working tree — we never discard a
    user's uncommitted edits (no ``checkout -f``).
  * Checkout does not reindex here; the caller (frontend) chains the existing
    ``/codegraph/index/{repo}`` reindex after a successful switch.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.security import PathJailError, check_path

logger = logging.getLogger(__name__)

# Bound every git call so a wedged process can't stall a request thread. Ref
# reads (branch/rev-parse) are fast even on a slow bind mount; fetch reaches the
# network and checkout rewrites the working tree, so both get longer leashes.
#
# NOTE on performance: these repos are typically Docker-bind-mounted from a
# Windows host, where a full ``git status`` tree-walk is pathologically slow and
# wildly variable (seconds to minutes). So we deliberately keep ``git status``
# OFF the hot path: branch listing reads only refs/HEAD, and dirtiness is left to
# ``git checkout`` itself — without ``-f`` it refuses (and names the exact files)
# when a switch would overwrite local edits, and otherwise carries clean changes
# across. That is both cheaper and more precise than a blanket pre-walk.
_GIT_TIMEOUT = 20
_FETCH_TIMEOUT = 90
_CHECKOUT_TIMEOUT = 180

# Cap the conflicting-file list surfaced to the UI so a huge diff can't bloat the
# response / error message.
_MAX_DIRTY_FILES = 50


class RepoGitError(Exception):
    """Raised for repo-name resolution / non-git-repo problems (maps to 4xx)."""


def _resolve_repo(repo_name: str) -> str:
    """Return the jailed absolute path for *repo_name*, or raise RepoGitError.

    Rejects empty names, path separators, and anything that resolves outside
    ``repos_base_path``.
    """
    if not repo_name or repo_name in (".", "..") or "/" in repo_name or "\\" in repo_name:
        raise RepoGitError(f"invalid repo name: {repo_name!r}")
    base = settings.repos_base_path
    candidate = os.path.join(base, repo_name)
    try:
        resolved = check_path(candidate, jail=base)
    except PathJailError as exc:
        raise RepoGitError(str(exc)) from exc
    if not os.path.isdir(resolved):
        raise RepoGitError(f"repo not found: {repo_name}")
    return str(resolved)


def _git_env() -> Dict[str, str]:
    env = dict(os.environ)
    # Never block on an interactive credential/host-key prompt.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    return env


def _run_git(
    repo_path: str, args: List[str], timeout: int = _GIT_TIMEOUT
) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in *repo_path*. Never raises on non-zero exit."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        env=_git_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _is_git_repo(repo_path: str) -> bool:
    return os.path.isdir(os.path.join(repo_path, ".git"))


def _current_branch(repo_path: str) -> tuple[Optional[str], bool]:
    """Return (branch_name, detached). ``branch_name`` is None when detached."""
    proc = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = (proc.stdout or "").strip()
    if proc.returncode != 0 or not name:
        return (None, True)
    if name == "HEAD":  # detached HEAD
        return (None, True)
    return (name, False)


def _parse_checkout_conflict(stderr: str) -> List[str]:
    """Extract the tab-indented file paths git lists when a checkout is refused.

    git prints, e.g.::

        error: Your local changes to the following files would be overwritten
        by checkout:
        \tpath/one
        \tpath/two
        Please commit your changes or stash them before you switch branches.

    (also the "untracked working tree files would be overwritten" variant). The
    conflicting paths are exactly the tab-indented lines.
    """
    files: List[str] = []
    for line in (stderr or "").splitlines():
        if line.startswith("\t"):
            path = line.strip()
            if path:
                files.append(path)
            if len(files) >= _MAX_DIRTY_FILES:
                break
    return files


def git_status(repo_name: str) -> Dict[str, Any]:
    """Return ``{is_git, current_branch, detached}`` for *repo_name*.

    Intentionally cheap: reads only HEAD, never walks the working tree (see the
    performance note at the top of this module).
    """
    repo_path = _resolve_repo(repo_name)
    if not _is_git_repo(repo_path):
        return {"is_git": False, "current_branch": None, "detached": False}
    branch, detached = _current_branch(repo_path)
    return {"is_git": True, "current_branch": branch, "detached": detached}


def _local_branches(repo_path: str) -> List[str]:
    proc = _run_git(repo_path, ["branch", "--format=%(refname:short)"])
    if proc.returncode != 0:
        return []
    return [b.strip() for b in (proc.stdout or "").splitlines() if b.strip()]


def _remote_branches(repo_path: str) -> List[str]:
    """Remote-tracking branch short names, excluding the ``origin/HEAD`` alias."""
    proc = _run_git(repo_path, ["branch", "-r", "--format=%(refname:short)"])
    if proc.returncode != 0:
        return []
    out: List[str] = []
    for line in (proc.stdout or "").splitlines():
        name = line.strip()
        # Skip the symbolic "origin/HEAD -> origin/main" alias in either form.
        if not name or name.endswith("/HEAD") or "->" in name:
            continue
        out.append(name)
    return out


def list_branches(repo_name: str, include_remote: bool = True) -> Dict[str, Any]:
    """List branches and current-branch state for *repo_name*.

    Local branches + HEAD are cheap (ref reads only). Enumerating *remote*
    branches is not: a busy repo can carry hundreds of them and reading each ref
    off a slow bind mount costs several seconds. So the hot ``GET /branches`` path
    passes ``include_remote=False`` (instant), and the explicit Fetch action
    (which the user already expects to be slower) is what surfaces/refreshes the
    remote list. When skipped, ``remote`` is ``[]`` and ``remote_loaded`` is
    ``False`` so the UI can prompt "Fetch to load remote branches".

    For a non-git dir returns ``{is_git: False, ...}`` so the UI can hide the
    picker rather than error.
    """
    repo_path = _resolve_repo(repo_name)
    if not _is_git_repo(repo_path):
        return {
            "is_git": False,
            "current": None,
            "detached": False,
            "local": [],
            "remote": [],
            "remote_loaded": False,
        }
    branch, detached = _current_branch(repo_path)
    return {
        "is_git": True,
        "current": branch,
        "detached": detached,
        "local": _local_branches(repo_path),
        "remote": _remote_branches(repo_path) if include_remote else [],
        "remote_loaded": include_remote,
    }


def fetch(repo_name: str) -> Dict[str, Any]:
    """``git fetch --prune`` then return the refreshed branch listing.

    Never raises: a missing-credentials / offline failure comes back as
    ``{ok: False, error, ...current listing...}`` so the UI degrades to the
    local branches it already had.
    """
    repo_path = _resolve_repo(repo_name)
    if not _is_git_repo(repo_path):
        raise RepoGitError(f"'{repo_name}' is not a git checkout")

    ok = True
    error: Optional[str] = None
    try:
        proc = _run_git(repo_path, ["fetch", "--prune"], timeout=_FETCH_TIMEOUT)
        if proc.returncode != 0:
            ok = False
            error = (proc.stderr or proc.stdout or "git fetch failed").strip()[:500]
            logger.info("repo_git.fetch: repo=%s fetch failed: %s", repo_name, error)
    except subprocess.TimeoutExpired:
        ok = False
        error = f"git fetch timed out after {_FETCH_TIMEOUT}s"
        logger.info("repo_git.fetch: repo=%s timed out", repo_name)

    listing = list_branches(repo_name)
    listing["ok"] = ok
    if error:
        listing["error"] = error
    return listing


def checkout_branch(repo_name: str, branch: str) -> Dict[str, Any]:
    """Switch *repo_name* to *branch*, letting git guard against data loss.

    We do NOT pre-walk the tree for dirtiness (too slow on a bind mount). Instead
    ``git checkout`` without ``-f`` is the safety boundary: it carries clean local
    changes across and refuses — naming the exact files — when a switch would
    overwrite uncommitted edits.

    Returns ``{ok: True, ...status...}`` on success. When git refuses because of
    conflicting local changes, returns
    ``{ok: False, reason: 'dirty', dirty_files: [...], error}``. Raises
    ``RepoGitError`` for a non-git repo or an unknown branch (caller maps to 4xx).
    """
    repo_path = _resolve_repo(repo_name)
    if not _is_git_repo(repo_path):
        raise RepoGitError(f"'{repo_name}' is not a git checkout")

    branch = (branch or "").strip()
    if not branch:
        raise RepoGitError("branch is required")

    # Only allow switching to a branch git already knows about — never pass an
    # arbitrary user string as a ref to checkout.
    local = _local_branches(repo_path)
    remote = _remote_branches(repo_path)
    if branch not in local and branch not in remote:
        raise RepoGitError(f"unknown branch: {branch}")

    # For a remote-only branch (e.g. "origin/feature/x"), check out the local
    # tracking name ("feature/x"); git auto-creates it from the matching remote
    # ref and sets up tracking.
    target = branch
    if branch not in local and "/" in branch:
        target = branch.split("/", 1)[1]

    try:
        proc = _run_git(repo_path, ["checkout", target], timeout=_CHECKOUT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RepoGitError(
            f"git checkout timed out after {_CHECKOUT_TIMEOUT}s (very large/slow checkout)"
        )

    if proc.returncode != 0:
        stderr = proc.stderr or proc.stdout or ""
        lowered = stderr.lower()
        if "would be overwritten" in lowered or "local changes" in lowered:
            return {
                "ok": False,
                "reason": "dirty",
                "dirty_files": _parse_checkout_conflict(stderr),
                "current": _current_branch(repo_path)[0],
                "error": stderr.strip()[:500],
            }
        err = stderr.strip()[:500] or "git checkout failed"
        logger.info("repo_git.checkout: repo=%s branch=%s failed: %s", repo_name, target, err)
        return {"ok": False, "reason": "checkout_failed", "error": err}

    status = git_status(repo_name)
    status["ok"] = True
    return status
