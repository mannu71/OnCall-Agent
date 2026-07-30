"""Dispatch-time context injection (3.3) — shift governance left.

Rather than a static system-prompt paragraph every run pays for regardless
of relevance, operator-authored rules live in ``AGENT_POLICY.md`` (repo
root) and are SLICED per run to only the rules whose ``[tools: ...]``
selector matches a tool actually bound this run. An agent told the
applicable constraint up front makes fewer mistakes than one that discovers
it by getting corrected after the fact.

Rule format, one entry per ``##`` line::

    ## R-014 [tools: cw_logs_insights*] Always bound queries with a time window.
    ## R-021 [tools: edit_file,create_file] Never edit without a prior fs_read.
    ## R-099 General rule with no tools selector — applies to every run.

A rule with no ``[tools: ...]`` selector applies unconditionally. Malformed
lines are silently skipped (best-effort; a broken policy doc must never
break a run). The result is capped at ``max_chars`` (~400 tokens default)
so it stays a cheap addition to the system prompt, not a second document.
"""
from __future__ import annotations

import fnmatch
import functools
import os
import re
from typing import List, Optional, Sequence, Tuple

_RULE_RE = re.compile(r"^##\s*(R-\d+)\s*(?:\[tools:\s*([^\]]*)\])?\s*(.*)$")
_DEFAULT_MAX_CHARS = 1600  # ~400 tokens at ~4 chars/token


def _default_policy_path() -> str:
    # app/core/governance/brief_slicer.py -> app/core/governance -> app/core
    # -> app -> agent-api (repo root, alongside AGENT_POLICY.md).
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(repo_root, "AGENT_POLICY.md")


def _policy_path() -> str:
    return os.environ.get("AGENT_POLICY_PATH", _default_policy_path())


def _parse_rules(text: str) -> Tuple[Tuple[str, Tuple[str, ...], str], ...]:
    """Parse ``## R-<n> [tools: ...] <text>`` lines into (id, patterns, text)."""
    rules = []
    for line in (text or "").splitlines():
        m = _RULE_RE.match(line.strip())
        if not m:
            continue
        rule_id, tools_csv, rule_text = m.groups()
        rule_text = (rule_text or "").strip()
        if not rule_text:
            continue
        patterns = tuple(p.strip() for p in (tools_csv or "").split(",") if p.strip())
        rules.append((rule_id, patterns, rule_text))
    return tuple(rules)


@functools.lru_cache(maxsize=1)
def _cached_rules(
    path: str, mtime_ns: int, size: int,
) -> Tuple[Tuple[str, Tuple[str, ...], str], ...]:
    """Stat-keyed cache — re-parses only when AGENT_POLICY.md actually changes,
    so a normal run pays no repeated file-read/regex cost.

    Keyed on ``st_mtime_ns`` rather than the float ``st_mtime``, and on size as
    well: two writes inside one filesystem mtime tick produce an identical float
    timestamp, which silently served a stale parse (and made the self-test flake
    non-deterministically). Nanoseconds plus size closes that window, and both
    come from the single stat below at no extra cost.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_rules(f.read())
    except Exception:  # noqa: BLE001 — a missing/unreadable doc is a no-op
        return ()


def _load_rules() -> Tuple[Tuple[str, Tuple[str, ...], str], ...]:
    path = _policy_path()
    try:
        st = os.stat(path)
    except OSError:
        return ()
    return _cached_rules(path, st.st_mtime_ns, st.st_size)


def slice_rules(
    bound_tool_names: Optional[Sequence[str]], *, max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Return this run's applicable AGENT_POLICY.md rules as prompt text.

    A rule applies when it has no ``[tools: ...]`` selector, or when any of
    its fnmatch patterns matches any bound tool name. Result capped at
    ``max_chars``. Returns '' on no match, no policy doc, or any error —
    governance must never block a run.
    """
    try:
        rules = _load_rules()
        if not rules:
            return ""
        names = list(bound_tool_names or [])
        applicable: List[str] = []
        for rule_id, patterns, text in rules:
            if not patterns or any(
                fnmatch.fnmatch(n, p) for n in names for p in patterns
            ):
                applicable.append(f"{rule_id}. {text}")
        if not applicable:
            return ""
        out = "\n".join(applicable)
        if len(out) > max_chars:
            truncated = out[:max_chars].rsplit("\n", 1)[0]
            out = (truncated or out[:max_chars]) + "\n…[truncated]"
        return out
    except Exception:  # noqa: BLE001 — governance must never break a run
        return ""


__all__ = ["slice_rules"]
