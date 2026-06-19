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
