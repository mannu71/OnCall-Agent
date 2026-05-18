"""Phase 5 correctness tests for code_indexer.

Covers:
  Fix 1 — incremental embedding via sha256 content hash:
    * body_sha256 is populated on insert
    * re-index of unchanged body issues 0 Bedrock calls
    * body-changed chunk triggers a fresh embedding call
    * of N chunks only new ones trigger embedding

  Fix 2 — search_vector refresh on UPDATE:
    * The DO UPDATE SQL clause rebuilds search_vector via to_tsvector
    * The tsvector expression references signature, name, docstring

  Fix 3 — structural test linkage:
    * Parametrized test links via body-mention rule (Rule C)
    * Confidence is higher when multiple rules match
    * TODO(phase2-scip) comment is present
    * confidence column exists in CREATE TABLE SQL

  ORM model checks:
    * body_sha256 column declared on CodeChunk ORM model
    * confidence column declared on CodeTestLink ORM model
    * TODO(post-phase2) comment on edge-weight cosine block

All tests are fully unit-level (no live Postgres, no live Bedrock).
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_chunk(
    name: str = "my_func",
    body: str = "def my_func(): pass",
    file_path: str = "src/module.py",
    repo_name: str = "test-repo",
    chunk_type: str = "function",
    signature: str = "my_func()",
    docstring: str = "",
    embedding: Optional[List[float]] = None,
):
    from app.services.code_indexer import CodeChunk
    return CodeChunk(
        repo_name=repo_name,
        file_path=file_path,
        name=name,
        chunk_type=chunk_type,
        body=body,
        language="python",
        signature=signature,
        docstring=docstring,
        embedding=embedding,
    )


def _mock_session_with_fetchall(rows: list):
    """Return (mock_session_cls, mock_session) where session.execute always returns rows."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_cls = MagicMock()
    mock_cls.return_value = mock_session
    return mock_cls, mock_session


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1a — sha256 population
# ─────────────────────────────────────────────────────────────────────────────

class TestBodySha256Population:
    """body_sha256 must be set on every chunk passed through _embed_chunks."""

    @pytest.mark.asyncio
    async def test_sha256_populated_after_embed_chunks(self):
        """body_sha256 is set to sha256(body) when _embed_chunks runs."""
        body = "def foo(): return 1"
        expected_hash = hashlib.sha256(body.encode()).hexdigest()

        mock_cls, _ = _mock_session_with_fetchall([])  # empty DB

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            with patch("app.services.code_indexer._embed_text", new=AsyncMock(return_value=[0.1])):
                from app.services.code_indexer import CodeIndexer
                indexer = CodeIndexer(embed=True)
                chunk = _make_chunk(body=body)
                await indexer._embed_chunks([chunk])

        assert chunk.body_sha256 == expected_hash

    @pytest.mark.asyncio
    async def test_sha256_is_64_hex_chars(self):
        """sha256 hexdigest is always exactly 64 lowercase hex characters."""
        mock_cls, _ = _mock_session_with_fetchall([])

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            with patch("app.services.code_indexer._embed_text", new=AsyncMock(return_value=[0.2])):
                from app.services.code_indexer import CodeIndexer
                indexer = CodeIndexer(embed=True)
                chunk = _make_chunk(body="x" * 500)
                await indexer._embed_chunks([chunk])

        assert len(chunk.body_sha256) == 64
        assert all(c in "0123456789abcdef" for c in chunk.body_sha256)


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1b — zero Bedrock calls on unchanged body
# ─────────────────────────────────────────────────────────────────────────────

class TestIncrementalEmbeddingSkip:
    """Re-index of an unchanged body must produce 0 _embed_text calls."""

    @pytest.mark.asyncio
    async def test_unchanged_body_skips_bedrock(self):
        """When stored hash == new hash and embedding exists, _embed_text is NOT called."""
        body = "def foo(): return 42"
        stored_hash = hashlib.sha256(body.encode()).hexdigest()

        # Simulate a DB row with matching hash and an existing embedding
        fake_row = MagicMock()
        fake_row.repo_name   = "test-repo"
        fake_row.file_path   = "src/module.py"
        fake_row.name        = "my_func"
        fake_row.chunk_type  = "function"
        fake_row.body_sha256 = stored_hash
        fake_row.embedding   = "[0.1,0.2,0.3]"

        mock_cls, _ = _mock_session_with_fetchall([fake_row])
        embed_mock = AsyncMock(return_value=[9.9, 9.9])

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            with patch("app.services.code_indexer._embed_text", embed_mock):
                from app.services.code_indexer import CodeIndexer
                indexer = CodeIndexer(embed=True)
                chunk = _make_chunk(body=body)
                await indexer._embed_chunks([chunk])

        assert embed_mock.call_count == 0, (
            "Bedrock must NOT be called when body hash is unchanged"
        )
        assert chunk.embedding == [0.1, 0.2, 0.3], (
            "Cached embedding must be reused"
        )

    @pytest.mark.asyncio
    async def test_changed_body_calls_bedrock(self):
        """When stored hash != new hash, _embed_text IS called exactly once."""
        old_body = "def foo(): return 1"
        new_body = "def foo(): return 999"  # body changed
        stored_hash = hashlib.sha256(old_body.encode()).hexdigest()

        fake_row = MagicMock()
        fake_row.repo_name   = "test-repo"
        fake_row.file_path   = "src/module.py"
        fake_row.name        = "my_func"
        fake_row.chunk_type  = "function"
        fake_row.body_sha256 = stored_hash   # OLD hash
        fake_row.embedding   = "[0.5,0.5]"

        mock_cls, _ = _mock_session_with_fetchall([fake_row])
        new_embedding = [1.1, 2.2, 3.3]
        embed_mock = AsyncMock(return_value=new_embedding)

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            with patch("app.services.code_indexer._embed_text", embed_mock):
                from app.services.code_indexer import CodeIndexer
                indexer = CodeIndexer(embed=True)
                chunk = _make_chunk(body=new_body)
                await indexer._embed_chunks([chunk])

        assert embed_mock.call_count == 1, (
            "Bedrock MUST be called once when body hash changes"
        )
        assert chunk.embedding == new_embedding

    @pytest.mark.asyncio
    async def test_multiple_chunks_only_new_ones_embed(self):
        """Of N chunks, only those with absent/changed hashes call _embed_text."""
        unchanged_body = "def bar(): pass"
        new_body       = "def baz_v2(): pass"
        stored_hash    = hashlib.sha256(unchanged_body.encode()).hexdigest()

        # DB returns only the unchanged row
        fake_row = MagicMock()
        fake_row.repo_name   = "test-repo"
        fake_row.file_path   = "src/mod.py"
        fake_row.name        = "bar"
        fake_row.chunk_type  = "function"
        fake_row.body_sha256 = stored_hash
        fake_row.embedding   = "[1.0,2.0]"

        mock_cls, _ = _mock_session_with_fetchall([fake_row])
        embed_mock = AsyncMock(return_value=[9.0, 9.0])

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            with patch("app.services.code_indexer._embed_text", embed_mock):
                from app.services.code_indexer import CodeIndexer
                indexer = CodeIndexer(embed=True)
                chunk_unchanged = _make_chunk(name="bar", body=unchanged_body, file_path="src/mod.py")
                chunk_new       = _make_chunk(name="baz", body=new_body,       file_path="src/mod.py")
                await indexer._embed_chunks([chunk_unchanged, chunk_new])

        assert embed_mock.call_count == 1, (
            f"Expected 1 Bedrock call (only for new chunk), got {embed_mock.call_count}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — search_vector refresh on UPDATE (source inspection)
# ─────────────────────────────────────────────────────────────────────────────

class TestSearchVectorRefresh:
    """The ON CONFLICT...DO UPDATE path must rebuild search_vector."""

    def test_upsert_sql_rebuilds_search_vector_on_update(self):
        """_upsert_chunks SQL must rebuild search_vector in the DO UPDATE clause."""
        from app.services.code_indexer import CodeIndexer
        source = inspect.getsource(CodeIndexer._upsert_chunks)

        # Both DO UPDATE and to_tsvector must appear together
        assert "DO UPDATE" in source, "_upsert_chunks must have an ON CONFLICT DO UPDATE"
        assert "to_tsvector" in source, (
            "search_vector must be rebuilt via to_tsvector — not just copied from EXCLUDED"
        )
        # Verify the search_vector assignment appears in the UPDATE clause
        update_block = source[source.index("DO UPDATE"):]
        assert "search_vector" in update_block, (
            "search_vector must be set in the DO UPDATE clause (Fix 2)"
        )

    def test_search_vector_update_uses_signature_name_docstring(self):
        """tsvector in the UPDATE clause references EXCLUDED.signature/name/docstring."""
        from app.services.code_indexer import CodeIndexer
        source = inspect.getsource(CodeIndexer._upsert_chunks)
        update_block = source[source.index("DO UPDATE"):]

        for field_ref in ("EXCLUDED.signature", "EXCLUDED.name", "EXCLUDED.docstring"):
            assert field_ref in update_block, (
                f"search_vector UPDATE must reference {field_ref!r} (Fix 2)"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3 — structural test linkage (source inspection + logic tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestStructuralTestLinkage:
    """_link_test_files must use multi-rule linkage with confidence scoring."""

    def test_link_test_files_has_scip_todo_comment(self):
        """_link_test_files must contain the SCIP replacement TODO comment."""
        from app.services.code_indexer import CodeIndexer
        source = inspect.getsource(CodeIndexer._link_test_files)
        assert "TODO(phase2-scip)" in source, (
            "Must have # TODO(phase2-scip): replace with SCIP reference edges comment"
        )

    def test_confidence_column_declared_in_sql(self):
        """CREATE TABLE for code_test_links must include a confidence column."""
        from app.services.code_indexer import CodeIndexer
        source = inspect.getsource(CodeIndexer._link_test_files)
        assert "confidence" in source, (
            "code_test_links table must declare and use a confidence column (Fix 3)"
        )

    def test_three_rule_labels_present(self):
        """Rule A, Rule B, and Rule C linkage labels must exist in source."""
        from app.services.code_indexer import CodeIndexer
        source = inspect.getsource(CodeIndexer._link_test_files)
        assert "Rule A" in source, "Name-heuristic (Rule A) must be labelled"
        assert "Rule B" in source, "Import-based (Rule B) must be labelled"
        assert "Rule C" in source, "Body-mention (Rule C) must be labelled"

    @pytest.mark.asyncio
    async def test_parametrized_test_links_via_body_mention(self):
        """A parametrized test that calls source_func in its body is linked.

        The old name-equality heuristic would miss this because the test function
        name ('test_with_various_inputs') doesn't strip-match 'source_func'.
        Rule C (body mention) should fire and create the link.
        """
        # Test chunk: lives in tests/, name does NOT match source_func
        test_chunk = MagicMock()
        test_chunk.file_path = "tests/test_processor.py"
        test_chunk.name      = "test_with_various_inputs"
        test_chunk.body      = (
            "@pytest.mark.parametrize('x,y', [(1,2),(3,4)])\n"
            "def test_with_various_inputs(x, y):\n"
            "    result = source_func(x, y)\n"
            "    assert result > 0\n"
        )

        source_chunk = MagicMock()
        source_chunk.file_path = "src/processor.py"
        source_chunk.name      = "source_func"
        source_chunk.body      = "def source_func(x, y): return x + y"

        # Track which INSERT calls are made (with conf parameter)
        inserted_links: list = []
        call_counter = [0]

        tc_result = MagicMock(); tc_result.fetchall.return_value = [test_chunk]
        sc_result = MagicMock(); sc_result.fetchall.return_value = [source_chunk]
        imp_result = MagicMock(); imp_result.fetchall.return_value = []
        default_result = MagicMock(); default_result.fetchall.return_value = []

        async def execute_side(stmt, params=None, **kw):
            call_counter[0] += 1
            n = call_counter[0]
            if n == 3:
                return tc_result
            if n == 4:
                return sc_result
            if n == 5:
                return imp_result
            if params and "conf" in (params or {}):
                inserted_links.append(dict(params))
            return default_result

        mock_session = AsyncMock()
        mock_session.execute = execute_side
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_cls = MagicMock()
        mock_cls.return_value = mock_session

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            from app.services.code_indexer import CodeIndexer
            indexer = CodeIndexer(embed=False)
            await indexer._link_test_files("test-repo")

        linked_source_names = {lnk.get("sn") for lnk in inserted_links}
        assert "source_func" in linked_source_names, (
            "Parametrized test body mentions 'source_func'; Rule C must create a link. "
            f"Got links: {inserted_links}"
        )

    @pytest.mark.asyncio
    async def test_confidence_higher_when_multiple_rules_match(self):
        """A link that satisfies both Rule A (name heuristic) AND Rule C (body mention)
        should have confidence > 0.4 (which a single-rule match would produce)."""
        # test_source_func -> strips "test_" -> source_func  (Rule A fires)
        # body also calls source_func directly              (Rule C fires)
        test_chunk = MagicMock()
        test_chunk.file_path = "tests/test_mod.py"
        test_chunk.name      = "test_source_func"
        test_chunk.body      = (
            "def test_source_func():\n"
            "    r = source_func()\n"
            "    assert r is not None\n"
        )

        source_chunk = MagicMock()
        source_chunk.file_path = "src/mod.py"
        source_chunk.name      = "source_func"
        source_chunk.body      = "def source_func(): return 1"

        inserted_links: list = []
        call_counter = [0]

        tc_result = MagicMock(); tc_result.fetchall.return_value = [test_chunk]
        sc_result = MagicMock(); sc_result.fetchall.return_value = [source_chunk]
        imp_result = MagicMock(); imp_result.fetchall.return_value = []
        default_result = MagicMock(); default_result.fetchall.return_value = []

        async def execute_side(stmt, params=None, **kw):
            call_counter[0] += 1
            n = call_counter[0]
            if n == 3:
                return tc_result
            if n == 4:
                return sc_result
            if n == 5:
                return imp_result
            if params and "conf" in (params or {}):
                inserted_links.append(dict(params))
            return default_result

        mock_session = AsyncMock()
        mock_session.execute = execute_side
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_cls = MagicMock()
        mock_cls.return_value = mock_session

        with patch("app.services.code_indexer.AsyncSessionLocal", mock_cls):
            from app.services.code_indexer import CodeIndexer
            indexer = CodeIndexer(embed=False)
            await indexer._link_test_files("test-repo")

        source_func_links = [lnk for lnk in inserted_links if lnk.get("sn") == "source_func"]
        assert source_func_links, "Expected at least one link for source_func"
        max_conf = max(lnk["conf"] for lnk in source_func_links)
        assert max_conf > 0.4, (
            f"Multi-rule match (Rule A + Rule C) must score > 0.4, got {max_conf}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ORM model column checks
# ─────────────────────────────────────────────────────────────────────────────

class TestOrmModelColumns:
    """Verify new Phase 5 schema additions are present.

    Note on shape: this repo creates ``code_chunks`` and ``code_test_links``
    via raw SQL DDL inside ``code_indexer.py`` (idempotent ALTER TABLE
    pattern), NOT via SQLAlchemy ORM models. ``CodeChunk`` is a
    @dataclass for in-memory chunks. So we verify:
      1. The dataclass has the ``body_sha256`` field.
      2. The DDL block contains the ALTER TABLE statement (proves the
         column is auto-created on first index run).
    """

    def test_code_chunk_dataclass_has_body_sha256(self):
        """CodeChunk @dataclass declares body_sha256 field."""
        from app.services.code_indexer import CodeChunk
        fields = {f.name for f in CodeChunk.__dataclass_fields__.values()}
        assert "body_sha256" in fields, (
            f"CodeChunk dataclass must have body_sha256 field; got {sorted(fields)}"
        )

    def test_code_chunks_ddl_adds_body_sha256(self):
        """The raw SQL DDL must include the ALTER TABLE for body_sha256."""
        import app.services.code_indexer as _mod
        src = inspect.getsource(_mod)
        assert (
            "ADD COLUMN IF NOT EXISTS body_sha256" in src
        ), "Missing ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS body_sha256 ..."

    def test_code_test_links_ddl_adds_confidence(self):
        """The raw SQL DDL for code_test_links must add the confidence column."""
        import app.services.code_indexer as _mod
        src = inspect.getsource(_mod)
        assert (
            "ADD COLUMN IF NOT EXISTS confidence" in src
        ), "Missing ALTER TABLE code_test_links ADD COLUMN IF NOT EXISTS confidence ..."

    def test_edge_weight_cosine_block_has_post_phase2_todo(self):
        """The O(N²) cosine block must have a post-phase2 TODO comment (spec requirement)."""
        import app.services.code_indexer as _mod
        full_src = inspect.getsource(_mod)
        assert "TODO(post-phase2)" in full_src, (
            "Must have # TODO(post-phase2): delete entire edge-weight cosine block comment"
        )
