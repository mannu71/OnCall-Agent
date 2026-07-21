"""Unit tests for semantic memory pure helpers (no DB).

The DB-touching remember/recall/dedup/scoping paths are covered by the live
integration check; here we lock down the pure logic: hashing, bank-filter SQL,
repo normalization, recall-block formatting/truncation, and repo extraction.
"""
from app.services.semantic_memory import (
    _sha256,
    _normalize_repos,
    _bank_filter,
    format_recall_block,
)
from app.harness.context_builder import repos_from_code_analyzer


def test_sha256_stable_and_distinct():
    assert _sha256("abc") == _sha256("abc")
    assert _sha256("abc") != _sha256("abd")
    assert len(_sha256("x")) == 64


def test_normalize_repos():
    assert _normalize_repos(None) is None
    assert _normalize_repos("") is None
    assert _normalize_repos("repo-a") == ["repo-a"]
    assert _normalize_repos(["a", "b"]) == ["a", "b"]
    assert _normalize_repos([]) is None
    assert _normalize_repos(["a", None, ""]) == ["a"]


def test_bank_filter_global_only_when_no_repo():
    f = _bank_filter(None)
    assert "bank = 'global'" in f
    assert "repo_name" not in f


def test_bank_filter_includes_repo_and_global():
    f = _bank_filter(["repo-a"])
    assert "bank = 'global'" in f
    assert "repo_name = ANY(:repos)" in f


def test_format_recall_block_empty():
    assert format_recall_block([]) == ""


def test_format_recall_block_truncates():
    long = "x" * 5000
    block = format_recall_block([{"content": long, "source": "agent"}], max_chars=100)
    assert block.startswith("## Learned memory")
    # truncated to ~100 chars + ellipsis, far below the original 5000.
    assert len(block) < 200
    assert "…" in block


def test_format_recall_block_labels_source():
    block = format_recall_block(
        [{"content": "raise pool size", "source": "manual"}], max_chars=600
    )
    assert "(manual)" in block
    assert "raise pool size" in block


def test_repos_from_code_analyzer():
    assert repos_from_code_analyzer(None) == []
    assert repos_from_code_analyzer({}) == []
    cfg = {"repos": [{"name": "compliance-api"}, {"name": "kyc-protect-api"}]}
    assert repos_from_code_analyzer(cfg) == ["compliance-api", "kyc-protect-api"]
    # tolerate bare-string repo entries
    assert repos_from_code_analyzer({"repos": ["a", "b"]}) == ["a", "b"]


# ── OKF structured indexing (weighted FTS input) ─────────────────────────────

def test_concept_fields_for_index_carries_tags_and_flat():
    from pathlib import Path
    from app.core.knowledge.bundle import KnowledgeBundle

    b = KnowledgeBundle(Path("."))  # no IO — pure parse
    doc = {
        "frontmatter": {
            "title": "DB pool exhausted",
            "description": "connections used up",
            "tags": ["database", "connection pool"],
        },
        "body": "Root cause: pool saturated.",
    }
    fields = b.concept_fields_for_index(doc)
    assert fields["title"] == "DB pool exhausted"
    assert fields["tags"] == ["database", "connection pool"]
    assert fields["description"] == "connections used up"
    # flat = title + description + body (displayable content column); tags are
    # deliberately NOT in flat — they ride the weight-A vector, not the content.
    assert "DB pool exhausted" in fields["flat"]
    assert "connections used up" in fields["flat"]
    assert "Root cause" in fields["flat"]
    assert "connection pool" not in fields["flat"]


def test_concept_fields_for_index_tolerates_scalar_tag_and_missing():
    from pathlib import Path
    from app.core.knowledge.bundle import KnowledgeBundle

    b = KnowledgeBundle(Path("."))
    fields = b.concept_fields_for_index({"frontmatter": {"title": "T", "tags": "solo"}, "body": ""})
    assert fields["tags"] == ["solo"]
    fields2 = b.concept_fields_for_index({"frontmatter": {"title": "T"}, "body": "b"})
    assert fields2["tags"] == []


# ── producer tag derivation (paraphrase synonym anchors) ─────────────────────

def test_derive_tags_extracts_identifiers_codes_and_vocab():
    from app.core.improvement.auto_learn import _derive_tags

    tags = _derive_tags(
        "The AmlScreen processor threw ArgumentOutOfRangeException; the connection "
        "pool was exhausted and it returned HTTP 429",
        ["job hit rate_limit_exceeded"],
        extra=["test-workflow"],
    )
    lower = [t.lower() for t in tags]
    assert "test-workflow" in lower               # extra preserved, ranked first
    assert "argumentoutofrangeexception" in lower  # CamelCase identifier
    assert "rate_limit_exceeded" in lower          # snake_case identifier
    assert "http 429" in lower                     # status code
    assert "exhausted" in lower                    # error vocab
    assert len(tags) <= 8                          # capped
    # dedup is case-insensitive
    assert len(tags) == len({t.lower() for t in tags})
