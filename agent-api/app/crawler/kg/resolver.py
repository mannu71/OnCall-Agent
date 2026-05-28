"""Cross-file reference resolution for the knowledge graph.

The parser produces ``EdgeRecord``s where ``target_qname`` is often just a
bare identifier — e.g. a call to ``CheckEntity()`` produces
``EdgeRecord(kind='calls', target_qname='CheckEntity', ...)`` because the
parser can only see one file at a time.

After ALL files in the repo are parsed we have a complete picture and can
patch those bare targets to their fully-qualified equivalents.  This is
exactly the ``resolve_bare_call_targets()`` pass from
``tirth8205/code-review-graph``.

Strategy (cheap, deterministic):
1. Build an index: ``{name: [qualified_name, ...]}`` from all parsed nodes.
2. For each edge with a bare target, look up candidates by name.
3. If there is exactly one candidate, promote the edge (confidence='resolved').
4. If there are multiple candidates:
   * If the edge's source file imports a module containing one of them,
     prefer that one.
   * Otherwise keep the bare target — confidence stays 'extracted'.
5. If zero candidates, mark confidence='external' (likely a stdlib/library
   call we don't track in this repo).

The resolver is intentionally side-effect-free: it returns a NEW list of
``EdgeRecord`` so callers can compare before/after if needed.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

from app.crawler.kg.parser import EdgeRecord, NodeRecord

logger = logging.getLogger(__name__)


def resolve_bare_call_targets(
    nodes: List[NodeRecord],
    edges: List[EdgeRecord],
) -> List[EdgeRecord]:
    """Promote bare-name edge targets to qualified names where possible.

    Returns a NEW list of edges with ``confidence`` updated:
      * 'resolved' — bare target matched exactly one qualified node
      * 'external' — bare target matched nothing (treat as out-of-repo)
      * 'extracted' — couldn't disambiguate; left for the agent / LLM
        to handle later if needed
    """
    # Index node names → list of qualified_names
    by_name: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        by_name[n.name].append(n.qualified_name)

    # Index imports per source file: source_file → set of imported targets
    imports_per_file: Dict[str, Set[str]] = defaultdict(set)
    for e in edges:
        if e.kind == "imports_from" and e.file_path:
            imports_per_file[e.file_path].add(e.target_qname)

    resolved: List[EdgeRecord] = []
    stats = {"resolved": 0, "external": 0, "ambiguous": 0, "already_qualified": 0, "passthrough": 0}

    for e in edges:
        # Pass through structural edges and imports
        if e.kind in ("contains", "imports_from"):
            resolved.append(e)
            stats["passthrough"] += 1
            continue
        # Already qualified → trust it
        if "::" in e.target_qname:
            stats["already_qualified"] += 1
            resolved.append(e)
            continue

        candidates = by_name.get(e.target_qname, [])
        if not candidates:
            resolved.append(_with_confidence(e, "external"))
            stats["external"] += 1
        elif len(candidates) == 1:
            resolved.append(_replace_target(e, candidates[0], "resolved"))
            stats["resolved"] += 1
        else:
            picked = _pick_by_imports(candidates, imports_per_file.get(e.file_path, set()))
            if picked is not None:
                resolved.append(_replace_target(e, picked, "resolved"))
                stats["resolved"] += 1
            else:
                resolved.append(e)  # ambiguous, leave as-is
                stats["ambiguous"] += 1

    logger.info(
        "kg.resolver: edges=%d resolved=%d external=%d ambiguous=%d "
        "already_qualified=%d passthrough=%d",
        len(edges),
        stats["resolved"], stats["external"], stats["ambiguous"],
        stats["already_qualified"], stats["passthrough"],
    )
    return resolved


def _replace_target(e: EdgeRecord, new_target: str, confidence: str) -> EdgeRecord:
    return EdgeRecord(
        kind=e.kind,
        source_qname=e.source_qname,
        target_qname=new_target,
        file_path=e.file_path,
        line=e.line,
        col=e.col,
        confidence=confidence,
    )


def _with_confidence(e: EdgeRecord, confidence: str) -> EdgeRecord:
    return EdgeRecord(
        kind=e.kind,
        source_qname=e.source_qname,
        target_qname=e.target_qname,
        file_path=e.file_path,
        line=e.line,
        col=e.col,
        confidence=confidence,
    )


def _pick_by_imports(candidates: List[str], imports: Set[str]) -> Optional[str]:
    """Pick the candidate whose path appears in the source file's imports.

    Returns None if no candidate clearly wins (zero or multiple matches).
    """
    if not imports:
        return None
    hits: List[str] = []
    for c in candidates:
        c_file = c.split("::")[0]  # qualified_name starts with file_path
        for imp in imports:
            if not imp:
                continue
            # Match on either dotted name fragment or path tail.
            imp_path = imp.replace(".", "/").strip(";").strip()
            if imp in c_file or (imp_path and imp_path in c_file):
                hits.append(c)
                break
    return hits[0] if len(hits) == 1 else None
