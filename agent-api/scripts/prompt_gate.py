#!/usr/bin/env python3
"""Prompt-edit gate — run the trajectory accuracy suite when the system-prompt
assembly changes.

A single well-intentioned edit to the prompt-assembly code can silently break a
behavioural contract that only the trajectory suite exercises (e.g. the
2026-07-19 regression where a domain-routing bullet made the agent skip
mandatory skill loading — invisible to unit tests, caught only by a full run).
This gate makes that class of change run the suite before it lands.

What it does:
  1. Diff the working tree (or a given base ref) for changes to the
     prompt-critical files below.
  2. If none changed, exit 0 (nothing to gate).
  3. Otherwise sync the working-tree copies of those files + the evals tree into
     the running agent-api container and run the trajectory suite, then require
     a perfect objective score.

Usage:
  python scripts/prompt_gate.py                 # gate uncommitted working changes
  python scripts/prompt_gate.py --base origin/main   # gate everything since a ref
  python scripts/prompt_gate.py --list-only     # just report critical changes
  CONTAINER=my-api python scripts/prompt_gate.py     # override container name

Exit codes: 0 = pass or nothing to gate; 1 = suite failed; 2 = setup error.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Repo-relative paths whose edits change how the system prompt is assembled.
# Keep in sync with the "# Skills" / routing / capability sections.
PROMPT_CRITICAL = [
    "agent-api/app/harness/agent_builder.py",
    "agent-api/app/harness/capabilities.py",
    "agent-api/app/harness/skill_tools.py",
    "agent-api/app/harness/context_builder.py",
    "agent-api/app/core/skills/manager.py",
]

# App files worth syncing into the container so the suite tests the working tree.
CONTAINER = os.environ.get("CONTAINER", "kyc-agent-api")
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_API = REPO_ROOT / "agent-api"
PASS_MARKER = "trajectory mean OBJECTIVE score: 1.0000"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def changed_files(base: str | None) -> list[str]:
    """Repo-relative paths changed vs *base* (or the working tree if None)."""
    if base:
        cp = _run(["git", "-C", str(REPO_ROOT), "diff", "--name-only", base])
    else:
        # Uncommitted working-tree changes (staged + unstaged) vs HEAD.
        cp = _run(["git", "-C", str(REPO_ROOT), "diff", "--name-only", "HEAD"])
    if cp.returncode != 0:
        print(f"[prompt-gate] git diff failed: {cp.stderr.strip()}", file=sys.stderr)
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def container_running() -> bool:
    cp = _run(["docker", "ps", "--format", "{{.Names}}"])
    return CONTAINER in cp.stdout.split()


def sync_into_container(critical_hits: list[str]) -> bool:
    """Refresh evals + the changed prompt files inside the container. Docker cp
    into an existing dir nests, so evals is replaced wholesale, files go 1:1."""
    rm = _run(["docker", "exec", CONTAINER, "rm", "-rf", "/app/evals"])
    if rm.returncode != 0:
        print(f"[prompt-gate] could not clear /app/evals: {rm.stderr.strip()}", file=sys.stderr)
        return False
    cp = _run(["docker", "cp", str(AGENT_API / "evals"), f"{CONTAINER}:/app/evals"])
    if cp.returncode != 0:
        print(f"[prompt-gate] could not copy evals: {cp.stderr.strip()}", file=sys.stderr)
        return False
    for rel in critical_hits:
        host = REPO_ROOT / rel
        dest = "/app/" + rel[len("agent-api/"):]  # mirror under /app
        cp = _run(["docker", "cp", str(host), f"{CONTAINER}:{dest}"])
        if cp.returncode != 0:
            print(f"[prompt-gate] could not copy {rel}: {cp.stderr.strip()}", file=sys.stderr)
            return False
    return True


def run_suite() -> tuple[bool, str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    cp = _run(["docker", "exec", "-e", "PYTHONUTF8=1", CONTAINER,
               "python", "-m", "evals.accuracy.run_trajectory"], env=env)
    out = cp.stdout + cp.stderr
    return (PASS_MARKER in out), out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None,
                    help="Gate everything changed since this git ref (default: uncommitted changes).")
    ap.add_argument("--list-only", action="store_true",
                    help="Only report prompt-critical changes; do not run the suite.")
    args = ap.parse_args()

    changed = set(changed_files(args.base))
    hits = [p for p in PROMPT_CRITICAL if p in changed]

    if not hits:
        print("[prompt-gate] no prompt-critical files changed — gate skipped.")
        return 0

    print("[prompt-gate] prompt-critical changes detected:")
    for h in hits:
        print(f"    - {h}")

    if args.list_only:
        return 0

    if not container_running():
        print(f"[prompt-gate] container '{CONTAINER}' is not running — start it, or set "
              "CONTAINER=<name>. (Cannot gate without the agent runtime.)", file=sys.stderr)
        return 2

    print(f"[prompt-gate] syncing working tree into '{CONTAINER}' and running the "
          "trajectory suite (~4 min)…")
    if not sync_into_container(hits):
        return 2

    ok, out = run_suite()
    print(out)
    if ok:
        print("[prompt-gate] PASS — trajectory suite at 100%. Prompt change is safe to land.")
        return 0
    print("[prompt-gate] FAIL — trajectory suite regressed. Do NOT land this prompt change "
          "until it is back to 100%. See the per-case output above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
