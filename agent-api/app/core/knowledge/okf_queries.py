"""OKF-backed knowledge queries for the REST/MCP knowledge surfaces.

Replaces the dropped ``knowledge_entries`` / ``log_patterns`` tables. Known
issues and log patterns are OKF concept docs under the bundle; listing reads the
files, creation writes a reviewable doc + indexes it into the ``kb`` memory bank,
and search is Postgres FTS over that bank (tags act as paraphrase synonyms). All
ids are the concept **slug** (a stable string), replacing the old integer PKs.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.knowledge.bundle import get_default_bundle, index_concept_into_kb


def _section(body: str, name: str) -> str:
    m = re.search(rf"(?ms)^#\s+{re.escape(name)}\b\s*\n(.*?)(?=^#\s|\Z)", body or "")
    return m.group(1).strip() if m else ""


def _bullets(text: str) -> List[str]:
    return [ln.strip()[1:].strip() for ln in text.splitlines() if ln.strip().startswith("-")]


def _entry_dict(path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    fm = doc.get("frontmatter") or {}
    body = doc.get("body") or ""
    tags = list(fm.get("tags") or [])
    return {
        "id": path.stem,           # slug (string id replacing the old integer PK)
        "slug": path.stem,
        "title": fm.get("title") or path.stem,
        "description": fm.get("description") or "",
        "symptoms": _bullets(_section(body, "Symptoms")),
        "solution": _section(body, "Resolution"),
        "category": (tags[0] if tags else None),
        "tags": tags,
        "source": fm.get("source") or "agent",
    }


def _matches_category(entry: Dict[str, Any], category: Optional[str]) -> bool:
    if not category:
        return True
    needle = category.lower()
    if (entry.get("category") or "").lower() == needle:
        return True
    return any(needle == str(t).lower() for t in entry.get("tags") or [])


# ── known issues ─────────────────────────────────────────────────────────────

def list_known_issues(category: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    bundle = get_default_bundle()
    out: List[Dict[str, Any]] = []
    for p in bundle.list_concepts("known-issues"):
        doc = bundle.read_concept(p)
        if not doc:
            continue
        entry = _entry_dict(p, doc)
        if _matches_category(entry, category):
            out.append(entry)
        if len(out) >= limit:
            break
    return out


async def create_known_issue(
    title: str,
    description: str,
    symptoms: Optional[List[str]] = None,
    solution: str = "",
    category: str = "",
    source: str = "manual",
) -> Dict[str, Any]:
    from app.core.improvement.auto_learn import _derive_tags

    symptoms = [s for s in (symptoms or []) if str(s).strip()]
    parts: List[str] = []
    if symptoms:
        parts.append("# Symptoms\n" + "\n".join(f"- {s}" for s in symptoms))
    if (description or "").strip():
        parts.append("# Root cause\n" + description.strip())
    if (solution or "").strip():
        parts.append("# Resolution\n" + solution.strip())
    body = "\n\n".join(parts)
    tags = _derive_tags(f"{description}\n{solution}", symptoms,
                        extra=[category] if category else [])
    bundle = get_default_bundle()
    res = bundle.write_concept(
        section="known-issues", title=title, body=body,
        description=(description or "")[:500], tags=tags, source=source,
    )
    path = Path(res["path"])
    await index_concept_into_kb(bundle, path, bank="kb")
    entry = _entry_dict(path, bundle.read_concept(path) or {})
    entry["created"] = res["created"]
    return entry


# ── log patterns ─────────────────────────────────────────────────────────────

def list_patterns(pattern_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    bundle = get_default_bundle()
    out: List[Dict[str, Any]] = []
    for p in bundle.list_concepts("log-patterns"):
        doc = bundle.read_concept(p)
        if not doc:
            continue
        entry = _entry_dict(p, doc)
        if _matches_category(entry, pattern_type):
            out.append(entry)
        if len(out) >= limit:
            break
    return out


async def add_pattern(
    name: str,
    pattern: str = "",
    pattern_type: Optional[str] = None,
    severity: Optional[Any] = None,
    description: str = "",
) -> Dict[str, Any]:
    from app.core.improvement.auto_learn import _derive_tags

    body_parts: List[str] = []
    if (pattern or "").strip():
        body_parts.append("# Pattern\n" + pattern.strip())
    if (description or "").strip():
        body_parts.append("# Notes\n" + description.strip())
    body = "\n\n".join(body_parts) or (description or pattern or name)
    extra = [t for t in [pattern_type, (f"severity:{severity}" if severity is not None else None)] if t]
    tags = _derive_tags(f"{name}\n{pattern}\n{description}", None, extra=extra)
    bundle = get_default_bundle()
    res = bundle.write_concept(
        section="log-patterns", title=name, body=body,
        description=(description or pattern or "")[:500], tags=tags, source="manual",
    )
    path = Path(res["path"])
    await index_concept_into_kb(bundle, path, bank="kb")
    return {"id": res["slug"], "slug": res["slug"], "name": name, "created": res["created"]}


async def search_patterns(query: str, limit: int = 10, **_ignored: Any) -> List[Dict[str, Any]]:
    """FTS search over the ``kb`` bank (``threshold`` accepted and ignored)."""
    from app.services.semantic_memory import semantic_memory

    hits = await semantic_memory.recall(query, bank="kb", k=limit)
    out: List[Dict[str, Any]] = []
    for h in hits:
        content = (h.get("content") or "").strip()
        out.append({
            "id": h.get("id"),
            "name": content.split("\n", 1)[0][:80] if content else None,
            "snippet": content[:200],
            "score": h.get("score"),
            "source": h.get("source"),
        })
    return out
