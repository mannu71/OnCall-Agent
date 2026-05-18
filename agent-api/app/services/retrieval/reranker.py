"""Phase 3 hybrid retrieval reranker.

Merges candidates from multiple sources (SCIP, tree-sitter, BM25, embedding)
using a weighted scoring scheme tuned per query type.  Deduplicates by
(file_path, line), keeping the highest weighted score per location.

Public surface
--------------
  Candidate     — dataclass representing a single retrieval hit
  QueryType     — Literal of supported query types
  rerank()      — async coroutine that scores, deduplicates and sorts
  classify_query() — lightweight regex heuristic to infer QueryType from text
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

QueryType = Literal["definition", "reference", "caller", "callee", "semantic"]

# ---------------------------------------------------------------------------
# Weight table
# ---------------------------------------------------------------------------

SOURCE_WEIGHTS: dict[str, dict[str, float]] = {
    "definition": {"scip": 1.0, "tree-sitter": 0.5, "bm25": 0.3, "embedding": 0.1},
    "reference":  {"scip": 1.0, "tree-sitter": 0.5, "bm25": 0.3, "embedding": 0.1},
    "caller":     {"scip": 1.0, "tree-sitter": 0.5, "bm25": 0.3, "embedding": 0.1},
    "callee":     {"scip": 1.0, "tree-sitter": 0.5, "bm25": 0.3, "embedding": 0.1},
    "semantic":   {"scip": 0.3, "tree-sitter": 0.4, "bm25": 0.6, "embedding": 1.0},
}

_DEFAULT_WEIGHT = 0.2  # fallback for unknown sources
_DEFAULT_TOP_N = 50    # maximum candidates returned when limit not specified

# ---------------------------------------------------------------------------
# Candidate dataclass
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A single retrieval hit from any source."""

    symbol_id: str
    file_path: str
    line: int
    snippet: str
    signature: str | None
    source: str         # "scip" | "tree-sitter" | "bm25" | "embedding"
    raw_score: float    # source-native score in [0, 1]
    confidence: str     # "exact" | "resolved" | "fuzzy" | "guess"

    # Computed after reranking — not set by callers
    weighted_score: float = field(default=0.0, init=False, repr=False)

    def compute_weighted_score(self, query_type: QueryType) -> float:
        """Return source_weight * raw_score for this candidate's source."""
        weight_map = SOURCE_WEIGHTS.get(query_type, {})
        weight = weight_map.get(self.source, _DEFAULT_WEIGHT)
        return weight * self.raw_score


# ---------------------------------------------------------------------------
# Query classifier
# ---------------------------------------------------------------------------

_DEFINITION_RE = re.compile(
    r"^(?:where\s+is\b|where\b|find\s+definition\b|defined\b|def\s+of\b)",
    re.IGNORECASE,
)
_CALLER_RE = re.compile(
    r"^(?:who\s+calls\b|callers?\s+of\b|references?\s+to\b)",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"^(?:references?\s+to\b|usages?\s+of\b)",
    re.IGNORECASE,
)


def classify_query(query: str) -> QueryType:
    """Classify a natural-language query into a QueryType using regex heuristics.

    Rules (checked in order):
      - definition patterns → "definition"
      - caller patterns    → "caller"
      - reference patterns → "reference"
      - everything else    → "semantic"
    """
    q = query.strip()
    if _DEFINITION_RE.match(q):
        return "definition"
    if _CALLER_RE.match(q):
        return "caller"
    if _REFERENCE_RE.match(q):
        return "reference"
    return "semantic"


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------


async def rerank(
    candidates: list[Candidate],
    query: str,
    query_type: QueryType,
    top_n: int = _DEFAULT_TOP_N,
) -> list[Candidate]:
    """Weighted reranker for hybrid retrieval.

    Algorithm:
      1. Compute weighted_score = source_weight[query_type][source] * raw_score
         for every candidate.
      2. Deduplicate by (file_path, line) — keep the candidate with the
         highest weighted_score for each unique location.
      3. Sort descending by weighted_score.
      4. Return the top-*top_n* results.

    Args:
        candidates:  Flat list of Candidate objects from all sources.
        query:       Original query string (used for logging only).
        query_type:  One of the QueryType literals; drives the weight table.
        top_n:       Maximum number of candidates to return.

    Returns:
        A list of Candidate objects sorted by weighted_score descending,
        at most *top_n* long.
    """
    if not candidates:
        return []

    # Step 1: score every candidate
    for c in candidates:
        c.weighted_score = c.compute_weighted_score(query_type)

    # Step 2: deduplicate by (file_path, line), keep highest score
    dedup: dict[tuple[str, int], Candidate] = {}
    for c in candidates:
        key = (c.file_path, c.line)
        existing = dedup.get(key)
        if existing is None or c.weighted_score > existing.weighted_score:
            dedup[key] = c

    unique = list(dedup.values())

    # Step 3: sort descending
    unique.sort(key=lambda c: c.weighted_score, reverse=True)

    # Step 4: cap at top_n
    result = unique[:top_n]

    logger.debug(
        "rerank: query=%r type=%s candidates=%d → deduped=%d → returned=%d",
        query[:80],
        query_type,
        len(candidates),
        len(unique),
        len(result),
    )
    return result
