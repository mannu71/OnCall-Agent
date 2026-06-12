"""Deterministic knowledge-graph search over ``kg_nodes``.

A single helper, :func:`kg_search`, that finds code symbols for a free-text
query WITHOUT an LLM and WITHOUT a separate on-disk verification pass — every
row is a real indexed definition, so it is inherently disk-valid.

Why both ILIKE and full-text:
  * ``kg_nodes.search_vector`` is ``to_tsvector('english', …)`` built from
    name / qualified_name / signature / docstring. English FTS does NOT split
    camelCase (``ProfilesController`` → one stemmed token), so identifier
    queries are matched by a ``name ILIKE %term%`` substring scan.
  * Full-text rank (``ts_rank``) still adds signal from natural-language prose
    in signatures and docstrings.

Ranking tiers (best first): exact name match → name substring match →
full-text rank → exported symbols → earliest line.

Used by the semantic-search flow (replacing the LLM-guess pipeline) and by the
RCA flow (grounding ``suspected_symbols`` in real files). Mirrors the result
shape of ``nodes/find.py::QueryKGForSymbol`` so downstream nodes need no change.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Identifier/word tokens worth searching. Drop 1-2 char noise ("is", "of").
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{3,}")

_SQL = """
    SELECT name, kind, qualified_name, file_path, line_start, line_end, signature,
           ts_rank(search_vector, websearch_to_tsquery('english', :q)) AS rank,
           (lower(name) = ANY(:exact)) AS exact_hit,
           (name ILIKE ANY(:patterns)) AS name_hit
    FROM kg_nodes
    WHERE repo_name = :r
      AND (
            name ILIKE ANY(:patterns)
            OR search_vector @@ websearch_to_tsquery('english', :q)
          )
      {kind_clause}
    ORDER BY exact_hit DESC, name_hit DESC, rank DESC, exported DESC, line_start ASC
    LIMIT :lim
"""


async def kg_search(
    repo: str,
    query: str,
    limit: int = 10,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return ranked ``kg_nodes`` matches for *query* in *repo*.

    Args:
        repo:  Repository name (``kg_nodes.repo_name``).
        query: Free text — an identifier, a phrase, or a natural-language query.
        limit: Max rows to return.
        kind:  Optional exact ``kind`` filter (e.g. ``"class"``, ``"method"``).

    Returns:
        List of dicts shaped like the find-flow's verified matches::

            {name, kind, qualified_name, file, line, line_end, signature,
             snippet, _line_count}

        Empty list when nothing matches (caller may fall back to the LLM path).
    """
    q = (query or "").strip()
    if not q:
        return []

    tokens = [t.lower() for t in _TOKEN_RE.findall(q)]
    # Keep the whole query (spaces stripped) as an exact-match candidate too, so
    # "ProfilesController" matches name='ProfilesController' on the top tier.
    exact_terms = sorted(set(tokens) | {q.lower(), q.replace(" ", "").lower()})
    patterns = [f"%{t}%" for t in tokens] or [f"%{q.lower()}%"]

    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    kind_clause = "AND kind = :k" if kind else ""
    params: Dict[str, Any] = {
        "r": repo,
        "q": q,
        "exact": exact_terms,
        "patterns": patterns,
        "lim": max(1, int(limit)),
    }
    if kind:
        params["k"] = kind

    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(_SQL.format(kind_clause=kind_clause)), params)
            results = rows.fetchall()
    except Exception as exc:  # noqa: BLE001 — retrieval must degrade, not crash
        logger.warning("kg_search failed (repo=%s, q=%r): %s", repo, q[:60], exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in results:
        name, kind_, qname, file_path, line_start, line_end, signature, *_ = row
        ls = int(line_start or 1)
        le = int(line_end or ls)
        out.append({
            "name":          name,
            "kind":          kind_,
            "qualified_name": qname,
            "file":          file_path,
            "line":          ls,
            "line_end":      le,
            "signature":     signature or "",
            "snippet":       (signature or name or "")[:300],
            # Lower bound on the file's length so body-handle windows stay valid.
            "_line_count":   max(le, ls + 50),
        })

    logger.info("kg_search: repo=%s q=%r → %d rows", repo, q[:60], len(out))
    return out
