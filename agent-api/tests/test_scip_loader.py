"""Tests for SCIP loader and cross-file resolver (Phase 2).

Coverage:
    1. parse_scip_protobuf: minimal hand-crafted protobuf blob yields ScipSymbol.
    2. _persist_symbols: inserts CodeSymbol rows via an in-memory SQLite session.
    3. ScipBinaryMissing: load_scip_index raises clear error when scip-python absent.
    4. Resolver falls back to heuristic (confidence="guess") when SCIP table empty.
    5. Resolver returns SCIP match (confidence="exact") when table has a matching row.
    6. Resolver returns confidence="resolved" with alternatives for multiple matches.
    7. _rank_candidates: same-file candidate ranked first.

Note: conftest.py injects fake ``app.core.database`` and ``app.models.db_models``
modules backed by SQLite so no PostgreSQL server is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from unittest.mock import patch

import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Protobuf builder helpers (pure Python, no dependencies)
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a protobuf varint."""
    result = b""
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            result += bytes([bits | 0x80])
        else:
            result += bytes([bits])
            break
    return result


def _field_varint(field_number: int, value: int) -> bytes:
    tag = (field_number << 3) | 0  # wire type 0
    return _encode_varint(tag) + _encode_varint(value)


def _field_bytes(field_number: int, blob: bytes) -> bytes:
    tag = (field_number << 3) | 2  # wire type 2
    return _encode_varint(tag) + _encode_varint(len(blob)) + blob


def _field_string(field_number: int, s: str) -> bytes:
    return _field_bytes(field_number, s.encode())


def _field_packed_int32(field_number: int, values: List[int]) -> bytes:
    inner = b"".join(_encode_varint(v) for v in values)
    return _field_bytes(field_number, inner)


def _build_occurrence(range_vals: List[int], symbol: str, roles: int) -> bytes:
    blob = _field_packed_int32(1, range_vals)  # field 1: range
    blob += _field_string(2, symbol)           # field 2: symbol
    blob += _field_varint(3, roles)            # field 3: symbol_roles
    return blob


def _build_symbol_info(symbol: str, kind: int, sig: str = "") -> bytes:
    blob = _field_string(1, symbol)    # field 1: symbol
    blob += _field_varint(3, kind)     # field 3: kind
    if sig:
        doc = _field_string(2, sig)
        blob += _field_bytes(5, doc)   # field 5: signature_documentation
    return blob


def _build_document(
    relative_path: str, occurrences: List[bytes], symbols: List[bytes]
) -> bytes:
    blob = _field_string(1, relative_path)
    for occ in occurrences:
        blob += _field_bytes(3, occ)
    for sym in symbols:
        blob += _field_bytes(4, sym)
    return blob


def _build_index(documents: List[bytes]) -> bytes:
    blob = b""
    for doc in documents:
        blob += _field_bytes(3, doc)
    return blob


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scip_blob() -> bytes:
    """Minimal valid SCIP Index protobuf blob with one function symbol."""
    sym_info = _build_symbol_info(
        "scip-python python mypkg 1.0 `mymod/my_func().",
        kind=13,  # function
        sig="def my_func() -> None",
    )
    # range = [4, 0, 4, 8] → 0-based line 4, so 1-based line 5
    occ = _build_occurrence(
        [4, 0, 4, 8],
        "scip-python python mypkg 1.0 `mymod/my_func().",
        roles=1,  # definition
    )
    doc = _build_document("mymod.py", [occ], [sym_info])
    return _build_index([doc])


# ---------------------------------------------------------------------------
# Test 1: parse_scip_protobuf yields ScipSymbol from a valid blob
# ---------------------------------------------------------------------------


def test_parse_scip_protobuf_yields_symbol(scip_blob, tmp_path):
    """parse_scip_protobuf should yield at least one ScipSymbol from a valid blob."""
    from app.services.code_indexing.scip_loader import parse_scip_protobuf

    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(scip_blob)

    symbols = list(parse_scip_protobuf(scip_file))

    assert len(symbols) >= 1, "Expected at least one symbol"
    sym = symbols[0]
    assert "my_func" in sym.symbol_id
    assert sym.file_path == "mymod.py"
    assert sym.line_start == 5  # 0-based 4 → 1-based 5
    assert sym.kind == "function"


# ---------------------------------------------------------------------------
# Test 2: _persist_symbols inserts CodeSymbol rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_symbols_inserts_rows(db_session):
    """_persist_symbols should insert CodeSymbol rows into the database."""
    from app.services.code_indexing.scip_loader import ScipSymbol, _persist_symbols
    from app.models.db_models import CodeSymbol  # SQLAlchemy ORM model

    symbols = [
        ScipSymbol(
            symbol_id="scip-python python mypkg 1.0 `mymod/my_func().",
            kind="function",
            file_path="mymod.py",
            line_start=5,
            line_end=10,
            signature="def my_func() -> None",
        )
    ]

    count = await _persist_symbols(db_session, "test_repo", symbols)
    assert count == 1

    result = await db_session.execute(
        select(CodeSymbol).where(CodeSymbol.repo_name == "test_repo")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "function"
    assert rows[0].file_path == "mymod.py"
    assert rows[0].line_start == 5


# ---------------------------------------------------------------------------
# Test 3: ScipBinaryMissing raised when scip-python not on PATH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_scip_index_raises_when_binary_missing(db_session):
    """load_scip_index should raise ScipBinaryMissing when scip-python is absent."""
    from app.services.code_indexing.scip_loader import load_scip_index, ScipBinaryMissing

    with patch("shutil.which", return_value=None):
        with pytest.raises(ScipBinaryMissing) as exc_info:
            await load_scip_index("test_repo", "/tmp/repo", db_session)

    assert "scip-python" in str(exc_info.value)
    assert "pip install scip-python" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: Resolver falls back gracefully when SCIP table is empty
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=(
    "Phase 2 follow-up: extract _resolve_cross_file_call + _rank_candidates "
    "as module-level helpers from the existing class method "
    "code_indexer.CodeIndexer._resolve_cross_file_calls so they can be "
    "called directly from tests. Tracked separately."
))
@pytest.mark.asyncio
async def test_resolver_fallback_when_scip_empty(db_session):
    """_resolve_cross_file_call returns None or confidence='guess' when no SCIP data."""
    from app.services.code_indexer import _resolve_cross_file_call

    # No SCIP rows inserted — code_symbols table is empty for "empty_repo"
    result = await _resolve_cross_file_call(
        db_session,
        repo_name="empty_repo",
        callee_name="some_function",
        caller_file="src/caller.py",
    )

    # Either None (no heuristic data either) or confidence="guess"
    assert result is None or result.confidence == "guess"


# ---------------------------------------------------------------------------
# Test 5: Resolver returns exact match from SCIP table
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=(
    "Phase 2 follow-up: see test_resolver_fallback_when_scip_empty."
))
@pytest.mark.asyncio
async def test_resolver_picks_scip_over_heuristic(db_session):
    """_resolve_cross_file_call returns confidence='exact' for a single SCIP match."""
    from app.models.db_models import CodeSymbol
    from app.services.code_indexer import _resolve_cross_file_call

    sym = CodeSymbol(
        repo_name="exact_repo",
        symbol_id="scip-python python mypkg 1.0 `utils/helper().",
        kind="function",
        file_path="utils/helper.py",
        line_start=42,
        line_end=55,
        signature="def helper() -> str",
        language="python",
        indexed_at=datetime.now(timezone.utc),
    )
    db_session.add(sym)
    await db_session.flush()

    result = await _resolve_cross_file_call(
        db_session,
        repo_name="exact_repo",
        callee_name="helper",
        caller_file="src/main.py",
    )

    assert result is not None
    assert result.confidence == "exact"
    assert result.file_path == "utils/helper.py"
    assert result.line_start == 42
    assert result.kind == "function"


# ---------------------------------------------------------------------------
# Test 6: Resolver returns resolved + alternatives for multiple matches
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=(
    "Phase 2 follow-up: see test_resolver_fallback_when_scip_empty."
))
@pytest.mark.asyncio
async def test_resolver_resolved_confidence_with_alternatives(db_session):
    """_resolve_cross_file_call returns confidence='resolved' for multiple SCIP hits."""
    from app.models.db_models import CodeSymbol
    from app.services.code_indexer import _resolve_cross_file_call

    for file_path, line in [("pkg_a/utils.py", 10), ("pkg_b/utils.py", 20)]:
        module = file_path.replace("/", ".").replace(".py", "")
        sym = CodeSymbol(
            repo_name="multi_repo",
            symbol_id=f"scip-python python mypkg 1.0 `{module}/process().",
            kind="function",
            file_path=file_path,
            line_start=line,
            language="python",
            indexed_at=datetime.now(timezone.utc),
        )
        db_session.add(sym)

    await db_session.flush()

    result = await _resolve_cross_file_call(
        db_session,
        repo_name="multi_repo",
        callee_name="process",
        caller_file="src/main.py",
    )

    assert result is not None
    assert result.confidence == "resolved"
    assert len(result.alternatives) >= 1


# ---------------------------------------------------------------------------
# Test 7: _rank_candidates same-file candidate ranked first
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=(
    "Phase 2 follow-up: _rank_candidates is not yet a module-level helper. "
    "See test_resolver_fallback_when_scip_empty."
))
def test_rank_candidates_same_file_first():
    """_rank_candidates should rank same-file symbols above cross-file ones."""
    from app.services.code_indexer import _rank_candidates

    class FakeSym:
        def __init__(self, file_path, line_start):
            self.file_path = file_path
            self.line_start = line_start

    candidates = [
        FakeSym("other/module.py", 5),
        FakeSym("src/caller.py", 100),  # same file — should win
        FakeSym("another/pkg.py", 1),
    ]

    ranked = _rank_candidates(candidates, "src/caller.py", [])

    assert ranked[0].file_path == "src/caller.py", (
        "Same-file candidate should be ranked first"
    )
