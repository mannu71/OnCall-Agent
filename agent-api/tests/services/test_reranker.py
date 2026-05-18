"""Tests for the Phase 3 hybrid retrieval reranker.

Covers:
  1. SCIP candidates rank above embedding candidates for definition queries.
  2. Embedding candidates rank above SCIP candidates for semantic queries.
  3. Deduplication by (file_path, line) — higher-scored candidate wins.
  4. classify_query("where is foo") → "definition"
  5. classify_query("who calls bar") → "caller"
  6. classify_query("logging behaviour") → "semantic"
"""
from __future__ import annotations

import pytest

from app.services.retrieval.reranker import (
    Candidate,
    classify_query,
    rerank,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_candidate(
    symbol_id: str = "sym1",
    file_path: str = "src/foo.py",
    line: int = 10,
    source: str = "embedding",
    raw_score: float = 0.8,
    confidence: str = "fuzzy",
    snippet: str = "def foo(): ...",
    signature: str | None = None,
) -> Candidate:
    return Candidate(
        symbol_id=symbol_id,
        file_path=file_path,
        line=line,
        snippet=snippet,
        signature=signature,
        source=source,
        raw_score=raw_score,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Test 1: SCIP > embedding for definition queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_scip_above_embedding_for_definition():
    """SCIP candidates should outrank embedding candidates for 'definition' queries."""
    scip_cand = make_candidate(
        symbol_id="scip1",
        file_path="src/a.py",
        line=5,
        source="scip",
        raw_score=0.5,  # lower raw score but weight=1.0
        confidence="exact",
    )
    emb_cand = make_candidate(
        symbol_id="emb1",
        file_path="src/b.py",
        line=20,
        source="embedding",
        raw_score=0.9,  # higher raw score but weight=0.1
        confidence="fuzzy",
    )

    result = await rerank([emb_cand, scip_cand], query="find_foo", query_type="definition")

    assert len(result) == 2
    assert result[0].symbol_id == "scip1", "SCIP should be ranked first for definition queries"
    assert result[1].symbol_id == "emb1"


# ---------------------------------------------------------------------------
# Test 2: Embedding > SCIP for semantic queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_embedding_above_scip_for_semantic():
    """Embedding candidates should outrank SCIP candidates for 'semantic' queries."""
    scip_cand = make_candidate(
        symbol_id="scip2",
        file_path="src/c.py",
        line=15,
        source="scip",
        raw_score=0.9,  # high raw score but weight=0.3
        confidence="exact",
    )
    emb_cand = make_candidate(
        symbol_id="emb2",
        file_path="src/d.py",
        line=30,
        source="embedding",
        raw_score=0.7,  # lower raw score but weight=1.0
        confidence="fuzzy",
    )

    result = await rerank([scip_cand, emb_cand], query="logging behaviour", query_type="semantic")

    assert len(result) == 2
    assert result[0].symbol_id == "emb2", "Embedding should be ranked first for semantic queries"
    assert result[1].symbol_id == "scip2"


# ---------------------------------------------------------------------------
# Test 3: Deduplication by (file_path, line)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_deduplicates_by_file_line():
    """Two candidates at the same (file_path, line) should collapse to one."""
    cand_low = make_candidate(
        symbol_id="low_score",
        file_path="src/shared.py",
        line=42,
        source="bm25",
        raw_score=0.3,
        confidence="fuzzy",
    )
    cand_high = make_candidate(
        symbol_id="high_score",
        file_path="src/shared.py",
        line=42,
        source="scip",
        raw_score=1.0,
        confidence="exact",
    )

    result = await rerank(
        [cand_low, cand_high],
        query="find_shared",
        query_type="definition",
    )

    assert len(result) == 1, "Duplicate locations should be deduplicated"
    assert result[0].symbol_id == "high_score", "Higher-scored candidate should survive"


# ---------------------------------------------------------------------------
# Test 4: classify_query — definition
# ---------------------------------------------------------------------------


def test_classify_query_definition_where_is():
    assert classify_query("where is foo") == "definition"


def test_classify_query_definition_find_definition():
    assert classify_query("find definition of bar") == "definition"


def test_classify_query_definition_defined():
    assert classify_query("defined in module x") == "definition"


def test_classify_query_definition_where():
    assert classify_query("where MyClass") == "definition"


# ---------------------------------------------------------------------------
# Test 5: classify_query — caller
# ---------------------------------------------------------------------------


def test_classify_query_caller_who_calls():
    assert classify_query("who calls bar") == "caller"


def test_classify_query_caller_callers_of():
    assert classify_query("callers of process_payment") == "caller"


# ---------------------------------------------------------------------------
# Test 6: classify_query — semantic
# ---------------------------------------------------------------------------


def test_classify_query_semantic_logging():
    assert classify_query("logging behaviour") == "semantic"


def test_classify_query_semantic_arbitrary():
    assert classify_query("how does the retry logic work") == "semantic"


def test_classify_query_semantic_empty():
    assert classify_query("") == "semantic"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_empty_input():
    result = await rerank([], query="anything", query_type="semantic")
    assert result == []


@pytest.mark.asyncio
async def test_rerank_single_candidate():
    cand = make_candidate(source="embedding", raw_score=0.7)
    result = await rerank([cand], query="foo", query_type="semantic")
    assert len(result) == 1
    assert result[0].symbol_id == cand.symbol_id


@pytest.mark.asyncio
async def test_rerank_respects_top_n():
    candidates = [
        make_candidate(symbol_id=f"sym{i}", file_path=f"src/f{i}.py", line=i, source="bm25", raw_score=float(i) / 10)
        for i in range(1, 11)
    ]
    result = await rerank(candidates, query="test", query_type="semantic", top_n=5)
    assert len(result) == 5


@pytest.mark.asyncio
async def test_rerank_weighted_score_computation():
    """Verify weighted_score = weight * raw_score after rerank."""
    cand = make_candidate(source="scip", raw_score=0.5)
    result = await rerank([cand], query="foo", query_type="definition")
    assert len(result) == 1
    # definition: scip weight = 1.0 → weighted_score = 1.0 * 0.5 = 0.5
    assert abs(result[0].weighted_score - 0.5) < 1e-6


@pytest.mark.asyncio
async def test_rerank_caller_query_type():
    """Caller queries should use the caller weight table (same as reference/definition for scip)."""
    scip_cand = make_candidate(
        symbol_id="s1", file_path="a.py", line=1, source="scip", raw_score=0.4
    )
    emb_cand = make_candidate(
        symbol_id="e1", file_path="b.py", line=2, source="embedding", raw_score=0.9
    )
    # caller: scip=1.0, embedding=0.1 → scip wins (0.4 > 0.09)
    result = await rerank([emb_cand, scip_cand], query="who calls foo", query_type="caller")
    assert result[0].symbol_id == "s1"
