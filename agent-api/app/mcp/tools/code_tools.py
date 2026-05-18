"""Code analysis tools — Phase 1 slim surface.

Provides a 6-tool interface that returns minimum-useful responses and lets
the LLM drill down on demand via body handles.  The old 5-tool surface
(search_code, get_function, get_callers, get_recent_changes, get_file_context)
is kept as deprecated thin wrappers.

Phase 1 confidence model (pre-SCIP):
  - "exact"    — reserved for SCIP/compiler-verified; not emitted in Phase 1
  - "resolved" — tree-sitter AST match, unique name in the DB
  - "fuzzy"    — embedding/BM25 top-K; always used by search_semantic
  - "guess"    — heuristic tie-break (e.g. most-recently-indexed first)

# TODO(phase2): scip lookup -> exact — SCIP results will supersede "resolved"

Real backing store schema (code_chunks):
  repo_name, file_path, name, chunk_type, signature, body, docstring,
  language, line_start, line_end, embedding, search_vector, entity_id,
  indexed_at

Real call-graph tables:
  code_calls   — caller_repo, caller_name, caller_file, callee_name,
                 callee_file, edge_weight, call_frequency, caller_chunk (FK)
  code_imports — repo_name, file_path, from_module, import_name, is_relative

All file access goes through app.core.security.check_path / PathJailError.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import warnings
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.security import check_path, PathJailError

# Phase 3: hybrid retrieval reranker (lazy import to avoid circular deps at
# module load time; the real import happens inside the tool functions)
_reranker_available: Optional[bool] = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard caps
# ---------------------------------------------------------------------------
_MAX_ROWS: int = 50                 # Maximum rows returned per paginated call
_MAX_FIELD_CHARS: int = 8192        # Maximum chars for any single string field
_MAX_RESPONSE_BYTES: int = 100_000  # 100 KB cap on serialised JSON response
_SNIPPET_CAP: int = 200             # Snippet cap for find_symbol / search_semantic

# Default repos root — override via REPOS_BASE_PATH env var
_DEFAULT_REPOS_ROOT = "/tmp/indexed_repos"


def _repos_root() -> str:
    return os.getenv("REPOS_BASE_PATH", _DEFAULT_REPOS_ROOT)


# ---------------------------------------------------------------------------
# Body-handle cache (module-level, FIFO-eviction dict capped at 1 024 entries)
# ---------------------------------------------------------------------------
_BODY_HANDLE_CACHE: Dict[str, Dict[str, Any]] = {}
_BODY_HANDLE_MAX: int = 1024


def _make_body_handle(repo: str, file: str, line_start: int, line_end: int) -> str:
    """Create an opaque body-fetch handle from repo+file+line range."""
    key = f"{repo}:{file}:{line_start}:{line_end}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    handle = f"fn:{digest}"
    # Store reverse-lookup metadata
    if handle not in _BODY_HANDLE_CACHE:
        if len(_BODY_HANDLE_CACHE) >= _BODY_HANDLE_MAX:
            # FIFO eviction — remove oldest key
            oldest = next(iter(_BODY_HANDLE_CACHE))
            del _BODY_HANDLE_CACHE[oldest]
        _BODY_HANDLE_CACHE[handle] = {
            "repo": repo,
            "file": file,
            "line_start": line_start,
            "line_end": line_end,
        }
    return handle


def _resolve_body_handle(handle: str) -> Optional[Dict[str, Any]]:
    """Resolve a body handle to its stored metadata, or None if expired/unknown."""
    return _BODY_HANDLE_CACHE.get(handle)


# ---------------------------------------------------------------------------
# Field-level and response-level truncation helpers
# ---------------------------------------------------------------------------

def _truncate_field(value: str, cap: int = _MAX_FIELD_CHARS) -> str:
    """Truncate a string field to *cap* characters, appending an indicator."""
    if len(value) <= cap:
        return value
    remaining = len(value) - cap
    return value[:cap] + f"\n…[truncated, {remaining} more chars]"


def _cap_response(data: Dict[str, Any], list_key: Optional[str] = None) -> Dict[str, Any]:
    """Ensure the serialised response fits within _MAX_RESPONSE_BYTES.

    If the response is too large and *list_key* is given, trim rows from the
    tail of that list until it fits, setting ``data["truncated"] = True``.
    """
    serialised = json.dumps(data)
    if len(serialised.encode()) <= _MAX_RESPONSE_BYTES:
        return data

    if list_key and isinstance(data.get(list_key), list):
        items = data[list_key]
        while items and len(json.dumps(data).encode()) > _MAX_RESPONSE_BYTES:
            items.pop()
        data["truncated"] = True
        data.setdefault("next_offset", None)

    return data


# ---------------------------------------------------------------------------
# Path verification helper
# ---------------------------------------------------------------------------

def _get_repos_base() -> Optional[str]:
    """Return REPOS_BASE_PATH env var if set and the path exists, else None."""
    base = os.environ.get("REPOS_BASE_PATH", "")
    if base and os.path.isdir(base):
        return base
    return None


def _verify_on_disk(
    repo: str, file: str, line: int, name: str
) -> Tuple[Optional[bool], Optional[str]]:
    """Check whether *name* still appears around *line* in the on-disk file.

    Returns:
        (True, None)            — symbol found at cited line
        (False, None)           — file present but symbol not found nearby
        (False, error_str)      — OSError / path issue
        (None, None)            — REPOS_BASE_PATH not configured; skip silently
    """
    base = _get_repos_base()
    if base is None:
        return None, None

    # Build absolute path; prevent directory traversal via check_path
    rel = file.lstrip("/\\")
    abs_path = os.path.normpath(os.path.join(base, repo, rel))
    jail = os.path.normpath(base)
    if not abs_path.startswith(jail):
        return False, f"Path traversal detected: {file}"

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except OSError as exc:
        return False, str(exc)

    # Read ±2 lines around the cited line (1-indexed)
    lo = max(0, line - 3)
    hi = min(len(all_lines), line + 2)
    window = "".join(all_lines[lo:hi])
    return (name in window), None


# ---------------------------------------------------------------------------
# Confidence assignment helper
# ---------------------------------------------------------------------------

def _assign_confidence(
    rows: List[Any],
    source: str,
) -> Literal["exact", "resolved", "fuzzy", "guess"]:
    """Assign Phase 1 confidence based on result count and source type.

    # TODO(phase2): scip lookup -> exact
    """
    if source in ("embedding", "bm25", "hybrid"):
        return "fuzzy"
    if len(rows) == 1:
        return "resolved"
    return "guess"


# ---------------------------------------------------------------------------
# DB query stubs — uses real code_chunks / code_calls / code_imports schema.
# When the backing store is unavailable, tools return empty results gracefully.
# ---------------------------------------------------------------------------

async def _db_find_symbol(
    name: str,
    kind: Optional[str],
    repo: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Query code_chunks for symbols matching *name*.

    Maps the reference's (repo, file, kind) field names to the real schema
    columns (repo_name, file_path, chunk_type).

    Returns a list of dicts with normalised keys; empty list on any DB error.
    """
    try:
        filters = ["name = :name"]
        params: Dict[str, Any] = {"name": name, "limit": limit}
        if kind:
            filters.append("chunk_type = :kind")
            params["kind"] = kind
        if repo:
            filters.append("repo_name = :repo")
            params["repo"] = repo

        where = " AND ".join(filters)
        sql = text(
            f"SELECT id::text AS symbol_id, repo_name AS repo, file_path AS file, "
            f"line_start, line_end, chunk_type AS kind, signature, body, name, indexed_at "
            f"FROM code_chunks WHERE {where} "
            f"ORDER BY indexed_at DESC NULLS LAST LIMIT :limit"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            return [dict(row._mapping) for row in result]
    except Exception as exc:  # noqa: BLE001
        logger.debug("_db_find_symbol: DB unavailable — %s", exc)
        return []


async def _db_get_definition(symbol_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single code_chunk row by primary key.

    Returns dict with normalised keys (repo, file, kind) or None.
    """
    try:
        sql = text(
            "SELECT id::text AS symbol_id, repo_name AS repo, file_path AS file, "
            "line_start, line_end, chunk_type AS kind, signature, body, name, indexed_at "
            "FROM code_chunks WHERE id::text = :sid LIMIT 1"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, {"sid": symbol_id})
            row = result.first()
            return dict(row._mapping) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("_db_get_definition: DB unavailable — %s", exc)
        return None


async def _db_get_references(
    symbol_id: str, limit: int, offset: int
) -> List[Dict[str, Any]]:
    """Fetch reference rows for the given symbol.

    References = callers (via code_calls joining by chunk id) + import sites
    (via code_imports joining by name lookup).  Phase 1 uses code_calls only
    since code_imports doesn't store a chunk FK.

    # TODO(phase2): include code_imports rows with role='import'
    """
    try:
        # Callers: find code_calls rows where caller_chunk FK matches symbol_id
        sql = text(
            "SELECT cc.caller_file AS file, 0 AS line, 'caller' AS role "
            "FROM code_calls cc "
            "WHERE cc.caller_chunk::text = :sid "
            "UNION ALL "
            "SELECT cc2.callee_file AS file, 0 AS line, 'callee' AS role "
            "FROM code_calls cc2 "
            "JOIN code_chunks c ON c.name = cc2.callee_name "
            "WHERE c.id::text = :sid "
            "ORDER BY file "
            "LIMIT :limit OFFSET :offset"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                sql, {"sid": symbol_id, "limit": limit, "offset": offset}
            )
            return [dict(row._mapping) for row in result]
    except Exception as exc:  # noqa: BLE001
        logger.debug("_db_get_references: DB unavailable — %s", exc)
        return []


async def _db_get_callers(
    symbol_id: str, depth: int, limit: int
) -> List[Dict[str, Any]]:
    """Fetch caller edges for the given symbol (depth=1 only in Phase 1).

    Uses the real code_calls schema:
      caller_chunk FK -> code_chunks.id  (caller)
      callee_name (string)               (callee)
    Joins code_chunks to resolve callee's id for the 'callee_symbol' field.

    # TODO(phase3): recursive CTE for depth > 1
    """
    try:
        # depth > 1 will be recursive in Phase 3; for now clamp to 1
        sql = text(
            "SELECT caller_c.id::text AS caller_symbol, "
            "       callee_c.id::text AS callee_symbol, "
            "       cc.edge_weight    AS confidence_score "
            "FROM code_calls cc "
            "JOIN code_chunks caller_c ON caller_c.id = cc.caller_chunk "
            "LEFT JOIN code_chunks callee_c ON callee_c.name = cc.callee_name "
            "WHERE callee_c.id::text = :sid "
            "ORDER BY cc.edge_weight DESC NULLS LAST "
            "LIMIT :limit"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                sql, {"sid": symbol_id, "limit": limit}
            )
            return [dict(row._mapping) for row in result]
    except Exception as exc:  # noqa: BLE001
        logger.debug("_db_get_callers: DB unavailable — %s", exc)
        return []


async def _db_search_semantic(
    query: str, repo: Optional[str], limit: int
) -> List[Dict[str, Any]]:
    """Hybrid BM25 + embedding semantic search over code_chunks.

    Phase 1: uses tsvector BM25 (search_vector column) plus ILIKE fallback
    when embeddings are not available (Bedrock not configured).
    Phase 2 will use pgvector cosine distance as the primary ranking signal.

    # TODO(phase2): replace with embedding cosine distance as primary signal
    """
    try:
        import re as _re

        # Try embedding-assisted search first
        embedding_available = False
        emb_str = None
        try:
            from app.services.code_indexer import _embed_text
            embedding = await _embed_text(query)
            if embedding:
                emb_str = f"[{','.join(str(v) for v in embedding)}]"
                embedding_available = True
        except Exception:
            pass

        if embedding_available and emb_str:
            # BM25 + vector hybrid (mirrors old search_code logic)
            ts_terms = _re.sub(r"[^\w\s]", " ", query).split()
            tsquery = " & ".join(ts_terms) if ts_terms else "code"

            filters = ["embedding IS NOT NULL"]
            params: Dict[str, Any] = {
                "embedding": emb_str,
                "tsquery": tsquery,
                "limit": limit,
                "vec_w": 0.7,
                "bm25_w": 0.3,
            }
            if repo:
                filters.append("repo_name = :repo")
                params["repo"] = repo

            where = " AND ".join(filters)
            sql = text(
                f"SELECT id::text AS symbol_id, repo_name AS repo, file_path AS file, "
                f"line_start, line_end, chunk_type AS kind, signature, body, name, indexed_at, "
                f"("
                f"  :vec_w  * (1 - (embedding <=> :embedding::vector)) "
                f"+ :bm25_w * COALESCE(ts_rank_cd(search_vector, to_tsquery('english', :tsquery)), 0) "
                f") AS score "
                f"FROM code_chunks WHERE {where} "
                f"ORDER BY score DESC LIMIT :limit"
            )
        else:
            # Fallback: trigram / ILIKE text search
            filters = ["(name ILIKE :q OR signature ILIKE :q OR body ILIKE :q)"]
            params = {"q": f"%{query}%", "limit": limit}
            if repo:
                filters.append("repo_name = :repo")
                params["repo"] = repo

            where = " AND ".join(filters)
            sql = text(
                f"SELECT id::text AS symbol_id, repo_name AS repo, file_path AS file, "
                f"line_start, line_end, chunk_type AS kind, signature, body, name, indexed_at "
                f"FROM code_chunks WHERE {where} "
                f"ORDER BY indexed_at DESC NULLS LAST LIMIT :limit"
            )

        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            return [dict(row._mapping) for row in result]
    except Exception as exc:  # noqa: BLE001
        logger.debug("_db_search_semantic: DB unavailable — %s", exc)
        return []


# ---------------------------------------------------------------------------
# Phase 3: candidate builder helpers for the reranker
# ---------------------------------------------------------------------------

async def _candidates_from_scip(
    name: str,
    kind: Optional[str],
    repo: Optional[str],
    limit: int,
) -> "List[Any]":
    """Return Candidate objects sourced from the code_symbols (SCIP) table.

    Confidence is always "exact" for SCIP results (compiler-verified).
    """
    try:
        from app.services.retrieval.reranker import Candidate  # local import

        filters = ["symbol_id ILIKE :pattern OR symbol_id = :exact_name"]
        params: Dict[str, Any] = {
            "pattern": f"%{name}%",
            "exact_name": name,
            "limit": limit,
        }
        if kind:
            filters.append("kind = :kind")
            params["kind"] = kind
        if repo:
            filters.append("repo_name = :repo")
            params["repo"] = repo

        where = " AND ".join(filters)
        sql = text(
            f"SELECT symbol_id, file_path, line_start, signature, kind "
            f"FROM code_symbols WHERE {where} "
            f"ORDER BY line_start LIMIT :limit"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            rows = [dict(r._mapping) for r in result]

        candidates = []
        for row in rows:
            candidates.append(
                Candidate(
                    symbol_id=str(row.get("symbol_id", "")),
                    file_path=row.get("file_path", ""),
                    line=row.get("line_start", 0),
                    snippet="",
                    signature=row.get("signature"),
                    source="scip",
                    raw_score=1.0,
                    confidence="exact",
                )
            )
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger.debug("_candidates_from_scip: unavailable — %s", exc)
        return []


async def _candidates_from_embedding_search(
    query: str,
    repo: Optional[str],
    limit: int,
) -> "List[Any]":
    """Return Candidate objects from embedding cosine similarity search."""
    try:
        from app.services.retrieval.reranker import Candidate  # local import

        try:
            from app.services.code_indexing.embedding import embed_text
            vecs = await embed_text(query)
            embedding = vecs[0] if vecs else None
        except Exception:
            embedding = None

        if not embedding:
            return []

        emb_str = f"[{','.join(str(v) for v in embedding)}]"
        filters = ["embedding IS NOT NULL"]
        params: Dict[str, Any] = {"embedding": emb_str, "limit": limit}
        if repo:
            filters.append("repo_name = :repo")
            params["repo"] = repo

        where = " AND ".join(filters)
        sql = text(
            f"SELECT id::text AS symbol_id, file_path, line_start, name, signature, body, "
            f"1 - (embedding <=> :embedding::vector) AS cos_sim "
            f"FROM code_chunks WHERE {where} "
            f"ORDER BY embedding <=> :embedding::vector "
            f"LIMIT :limit"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            rows = [dict(r._mapping) for r in result]

        candidates = []
        for row in rows:
            raw_score = float(row.get("cos_sim") or 0.0)
            # cos_sim can be negative; clamp to [0, 1]
            raw_score = max(0.0, min(1.0, raw_score))
            body = row.get("body") or ""
            candidates.append(
                Candidate(
                    symbol_id=str(row.get("symbol_id", "")),
                    file_path=row.get("file_path", ""),
                    line=row.get("line_start", 0),
                    snippet=body[:_SNIPPET_CAP],
                    signature=row.get("signature"),
                    source="embedding",
                    raw_score=raw_score,
                    confidence="fuzzy",
                )
            )
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger.debug("_candidates_from_embedding_search: unavailable — %s", exc)
        return []


async def _candidates_from_bm25(
    query: str,
    repo: Optional[str],
    limit: int,
) -> "List[Any]":
    """Return Candidate objects from BM25 (tsvector) search over code_chunks."""
    try:
        import re as _re
        from app.services.retrieval.reranker import Candidate  # local import

        ts_terms = _re.sub(r"[^\w\s]", " ", query).split()
        if not ts_terms:
            return []
        tsquery = " & ".join(ts_terms)

        filters = ["search_vector IS NOT NULL"]
        params: Dict[str, Any] = {"tsquery": tsquery, "limit": limit}
        if repo:
            filters.append("repo_name = :repo")
            params["repo"] = repo

        where = " AND ".join(filters)
        sql = text(
            f"SELECT id::text AS symbol_id, file_path, line_start, name, signature, body, "
            f"COALESCE(ts_rank_cd(search_vector, to_tsquery('english', :tsquery)), 0) AS bm25 "
            f"FROM code_chunks WHERE {where} "
            f"ORDER BY bm25 DESC LIMIT :limit"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            rows = [dict(r._mapping) for r in result]

        # Normalise rank to [0, 1] using the first row's score as max
        max_bm25 = float(rows[0]["bm25"]) if rows and rows[0].get("bm25") else 1.0
        if max_bm25 == 0:
            max_bm25 = 1.0

        candidates = []
        for row in rows:
            raw_score = float(row.get("bm25") or 0.0) / max_bm25
            body = row.get("body") or ""
            candidates.append(
                Candidate(
                    symbol_id=str(row.get("symbol_id", "")),
                    file_path=row.get("file_path", ""),
                    line=row.get("line_start", 0),
                    snippet=body[:_SNIPPET_CAP],
                    signature=row.get("signature"),
                    source="bm25",
                    raw_score=raw_score,
                    confidence="fuzzy",
                )
            )
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger.debug("_candidates_from_bm25: unavailable — %s", exc)
        return []


def _is_identifier_like(query: str) -> bool:
    """Return True if the query looks like a code identifier (for SCIP lookup)."""
    import re as _re
    return bool(_re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", query.strip()))


# ---------------------------------------------------------------------------
# Tool 1: find_symbol
# ---------------------------------------------------------------------------

async def find_symbol(
    name: str,
    kind: Optional[str] = None,
    repo: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Find symbols by name.

    Returns up to *limit* (max 50) candidates with location metadata and
    a body_handle for drilling into the implementation.

    Args:
        name:  Symbol name to search for (exact match).
        kind:  Optional kind filter, e.g. "function", "class", "method".
               Maps to chunk_type in code_chunks.
        repo:  Optional repository filter (repo_name in code_chunks).
        limit: Maximum candidates to return (capped at 50).
    """
    limit = min(limit, _MAX_ROWS)

    # ------------------------------------------------------------------
    # Phase 3: build candidates from 3 sources, then rerank
    # ------------------------------------------------------------------
    reranked: List[Any] = []
    try:
        from app.services.retrieval.reranker import rerank, Candidate  # noqa: F401

        # Source 1: SCIP (exact, compiler-verified)
        scip_cands = await _candidates_from_scip(name, kind, repo, limit)

        # Source 2: tree-sitter name match via code_chunks
        rows = await _db_find_symbol(name, kind, repo, limit)
        ts_cands = []
        ts_confidence = _assign_confidence(rows, "tree-sitter")
        for row in rows:
            body = row.get("body") or ""
            ts_cands.append(
                Candidate(
                    symbol_id=str(row.get("symbol_id", "")),
                    file_path=row.get("file", ""),
                    line=row.get("line_start", 0),
                    snippet=body[:_SNIPPET_CAP],
                    signature=row.get("signature"),
                    source="tree-sitter",
                    raw_score=0.8,  # fixed rank for exact name match
                    confidence=ts_confidence,
                )
            )

        # Source 3: embedding top-5
        emb_cands = await _candidates_from_embedding_search(name, repo, limit)

        all_cands = scip_cands + ts_cands + emb_cands
        reranked = await rerank(all_cands, query=name, query_type="definition", top_n=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("find_symbol: reranker unavailable (%s); falling back", exc)
        reranked = []

    # ------------------------------------------------------------------
    # If reranker path produced results, convert to output format
    # ------------------------------------------------------------------
    if reranked:
        candidates_out = []
        for cand in reranked:
            # Try to determine repo from file_path heuristic; fall back to ""
            repo_val = repo or ""
            file_val = cand.file_path
            line_val = cand.line
            handle = _make_body_handle(repo_val, file_val, line_val, line_val)
            verified, verr = _verify_on_disk(repo_val, file_val, line_val, name)
            entry: Dict[str, Any] = {
                "symbol_id": cand.symbol_id,
                "file": file_val,
                "line": line_val,
                "kind": kind or "unknown",
                "confidence": cand.confidence,
                "source": cand.source,
                "verified": verified,
                "body_handle": handle,
            }
            if verr:
                entry["verification_error"] = verr
            candidates_out.append(entry)

        result: Dict[str, Any] = {
            "candidates": candidates_out,
            "truncated": len(reranked) >= limit,
            "next_offset": limit if len(reranked) >= limit else None,
        }
        return _cap_response(result, "candidates")

    # ------------------------------------------------------------------
    # Fallback: original Phase 1 path (reranker import failed)
    # ------------------------------------------------------------------
    rows = await _db_find_symbol(name, kind, repo, limit)
    confidence = _assign_confidence(rows, "tree-sitter")

    candidates = []
    for row in rows:
        repo_val = row.get("repo", "")
        file_val = row.get("file", "")
        line_val = row.get("line_start", 0)
        line_end = row.get("line_end", line_val)
        symbol_id = row.get("symbol_id", "")
        kind_val = row.get("kind", "unknown")

        handle = _make_body_handle(repo_val, file_val, line_val, line_end)
        verified, verr = _verify_on_disk(repo_val, file_val, line_val, name)
        entry = {
            "symbol_id": symbol_id,
            "file": file_val,
            "line": line_val,
            "kind": kind_val,
            "confidence": confidence,
            "source": "tree-sitter",
            "verified": verified,
            "body_handle": handle,
        }
        if verr:
            entry["verification_error"] = verr
        candidates.append(entry)

    result = {
        "candidates": candidates,
        "truncated": len(rows) >= limit,
        "next_offset": limit if len(rows) >= limit else None,
    }
    return _cap_response(result, "candidates")


# ---------------------------------------------------------------------------
# Tool 2: get_definition
# ---------------------------------------------------------------------------

async def get_definition(symbol_id: str) -> Dict[str, Any]:
    """Fetch the definition for a symbol.

    Returns location, a 200-char snippet, a full-body handle, confidence,
    source, and disk-verification status.

    Args:
        symbol_id: Opaque symbol identifier from find_symbol or search_semantic.
    """
    row = await _db_get_definition(symbol_id)

    if row is None:
        return {"error": f"Symbol '{symbol_id}' not found", "symbol_id": symbol_id}

    repo_val = row.get("repo", "")
    file_val = row.get("file", "")
    line_start = row.get("line_start", 0)
    line_end = row.get("line_end", 0)
    name = row.get("name", "")
    signature = _truncate_field(row.get("signature") or "", _MAX_FIELD_CHARS)
    body = row.get("body") or ""
    snippet = _truncate_field(body[:_SNIPPET_CAP], _SNIPPET_CAP)
    handle = _make_body_handle(repo_val, file_val, line_start, line_end)

    verified, verr = _verify_on_disk(repo_val, file_val, line_start, name)
    result: Dict[str, Any] = {
        "symbol_id": symbol_id,
        "file": file_val,
        "line_start": line_start,
        "line_end": line_end,
        "signature": signature,
        "snippet": snippet,
        "body_handle": handle,
        "confidence": "resolved",   # single row by PK
        "source": "tree-sitter",
        "verified": verified,
    }
    if verr:
        result["verification_error"] = verr
    return result


# ---------------------------------------------------------------------------
# Tool 3: get_references
# ---------------------------------------------------------------------------

async def get_references(
    symbol_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return all references to a symbol.

    Phase 1: returns caller sites from code_calls joined via caller_chunk FK.
    Phase 2 will add import sites from code_imports with role='import'.

    # TODO(phase2): scip lookup -> exact — SCIP will add exact reference sites

    Args:
        symbol_id:  Opaque symbol identifier.
        limit:      Max rows per page (capped at 50).
        offset:     Pagination offset.
    """
    limit = min(limit, _MAX_ROWS)
    rows = await _db_get_references(symbol_id, limit + 1, offset)

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    matches = [
        {"file": r.get("file", ""), "line": r.get("line", 0), "role": r.get("role", "ref")}
        for r in rows
    ]
    result: Dict[str, Any] = {
        "symbol_id": symbol_id,
        "matches": matches,
        "truncated": truncated,
        "next_offset": offset + limit if truncated else None,
    }
    return _cap_response(result, "matches")


# ---------------------------------------------------------------------------
# Tool 4: get_callers  (new Phase 1 version; old wrapper below)
# ---------------------------------------------------------------------------

async def get_callers(
    symbol_id: Optional[str] = None,
    depth: int = 1,
    limit: int = 50,
    # Old positional arg kept for backward compat
    function_name: Optional[str] = None,
    repo: Optional[str] = None,
) -> Any:
    """Return call-graph edges for a symbol (new API) or by function name (deprecated).

    Phase 1 supports depth=1.  Deeper traversal is Phase 3 work.

    New API:
        symbol_id: Opaque symbol identifier from find_symbol or search_semantic.
        depth:     Call-graph depth (clamped to 1 in Phase 1).
        limit:     Max edges returned (capped at 50).

    Deprecated (backward-compat):
        function_name: Callee function name — triggers old list-based return.
        repo:          Optional repository filter for old API.
    """
    # Old callers pathway — when called with function_name positional arg
    if function_name is not None and symbol_id is None:
        warnings.warn(
            "get_callers(function_name=...) is deprecated; use get_callers(symbol_id=...) "
            "with a symbol_id from find_symbol. The function_name pathway uses callee_name "
            "string match (less precise).",
            DeprecationWarning,
            stacklevel=2,
        )
        return await _get_callers_by_name(function_name, repo, limit)

    # If symbol_id still None but function_name also None, return error
    if symbol_id is None:
        return {"error": "Provide symbol_id (new API) or function_name (deprecated)"}

    limit = min(limit, _MAX_ROWS)
    # Phase 1: depth > 1 not yet supported
    # TODO(phase3): recursive CTE for depth > 1
    if depth > 1:
        logger.info(
            "get_callers: depth=%d requested; Phase 1 only supports depth=1", depth
        )
        depth = 1

    rows = await _db_get_callers(symbol_id, depth, limit + 1)

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    edges = [
        {
            "caller_symbol": r.get("caller_symbol", ""),
            "callee_symbol": r.get("callee_symbol", symbol_id),
            "confidence": "resolved" if (r.get("confidence_score") or 0) >= 0.9 else "fuzzy",
        }
        for r in rows
    ]
    result: Dict[str, Any] = {
        "symbol_id": symbol_id,
        "depth": depth,
        "edges": edges,
        "truncated": truncated,
        "next_offset": limit if truncated else None,
    }
    return _cap_response(result, "edges")


async def _get_callers_by_name(
    function_name: str,
    repo: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Old get_callers implementation: join code_calls by callee_name string.

    Used by the deprecated function_name=... pathway.
    """
    try:
        params: Dict[str, Any] = {"callee": function_name, "limit": min(limit, _MAX_ROWS)}
        extra = "AND cc.caller_repo = :repo" if repo else ""
        if repo:
            params["repo"] = repo

        sql = text(
            f"SELECT c.repo_name, c.file_path, c.name, c.chunk_type, "
            f"       c.signature, c.line_start, c.line_end "
            f"FROM code_calls cc "
            f"JOIN code_chunks c ON c.id = cc.caller_chunk "
            f"WHERE cc.callee_name = :callee {extra} "
            f"ORDER BY c.repo_name, c.file_path "
            f"LIMIT :limit"
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            rows = result.all()

        return [
            {
                "repo_name":  r.repo_name,
                "file_path":  r.file_path,
                "name":       r.name,
                "type":       r.chunk_type,
                "signature":  r.signature,
                "line_start": r.line_start,
                "line_end":   r.line_end,
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("_get_callers_by_name: DB unavailable — %s", exc)
        return []


# ---------------------------------------------------------------------------
# Tool 5: search_semantic
# ---------------------------------------------------------------------------

async def search_semantic(
    query: str,
    repo: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Semantic search over the indexed code base.

    Returns top-*limit* hits without full bodies — use body_handle with
    get_body to fetch the implementation.  Snippet is capped at 200 chars.

    Args:
        query: Natural-language or identifier query string.
        repo:  Optional repository filter (repo_name).
        limit: Max results (capped at 50).
    """
    limit = min(limit, _MAX_ROWS)

    # ------------------------------------------------------------------
    # Phase 3: multi-source candidates → reranker → top-limit hits
    # ------------------------------------------------------------------
    reranked: List[Any] = []
    try:
        from app.services.retrieval.reranker import rerank, classify_query  # noqa: F401

        q_type = classify_query(query)

        # Source 1: embedding top-20
        emb_cands = await _candidates_from_embedding_search(query, repo, min(limit * 2, 20))

        # Source 2: BM25 top-20
        bm25_cands = await _candidates_from_bm25(query, repo, min(limit * 2, 20))

        # Source 3: SCIP exact identifier match (only if query looks like one)
        scip_cands: List[Any] = []
        if _is_identifier_like(query):
            scip_cands = await _candidates_from_scip(query, None, repo, limit)

        all_cands = emb_cands + bm25_cands + scip_cands
        reranked = await rerank(all_cands, query=query, query_type=q_type, top_n=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("search_semantic: reranker unavailable (%s); falling back", exc)
        reranked = []

    # ------------------------------------------------------------------
    # If reranker path produced results, convert to output format
    # ------------------------------------------------------------------
    if reranked:
        hits_out = []
        repo_val = repo or ""
        for cand in reranked:
            file_val = cand.file_path
            line_val = cand.line
            handle = _make_body_handle(repo_val, file_val, line_val, line_val)
            snippet = _truncate_field(cand.snippet[:_SNIPPET_CAP], _SNIPPET_CAP)
            sig = _truncate_field(cand.signature or "", _MAX_FIELD_CHARS)
            hits_out.append({
                "symbol_id": cand.symbol_id,
                "file": file_val,
                "line": line_val,
                "signature": sig,
                "snippet": snippet,
                "body_handle": handle,
                "confidence": cand.confidence,
                "source": cand.source,
            })

        result: Dict[str, Any] = {
            "query": query,
            "hits": hits_out,
            "truncated": len(reranked) >= limit,
            "next_offset": limit if len(reranked) >= limit else None,
        }
        return _cap_response(result, "hits")

    # ------------------------------------------------------------------
    # Fallback: original Phase 1 hybrid BM25 + embedding path
    # ------------------------------------------------------------------
    rows = await _db_search_semantic(query, repo, limit)

    hits = []
    for row in rows:
        repo_val = row.get("repo", "")
        file_val = row.get("file", "")
        line_start = row.get("line_start", 0)
        line_end = row.get("line_end", line_start)
        body = row.get("body") or ""
        snippet = _truncate_field(body[:_SNIPPET_CAP], _SNIPPET_CAP)
        signature = _truncate_field(row.get("signature") or "", _MAX_FIELD_CHARS)
        handle = _make_body_handle(repo_val, file_val, line_start, line_end)
        symbol_id = row.get("symbol_id", "")

        hits.append({
            "symbol_id": symbol_id,
            "file": file_val,
            "line": line_start,
            "signature": signature,
            "snippet": snippet,
            "body_handle": handle,
            "confidence": "fuzzy",
            "source": "hybrid",   # Phase 1 text fallback; Phase 2 = embedding
        })

    result = {
        "query": query,
        "hits": hits,
        "truncated": len(rows) >= limit,
        "next_offset": limit if len(rows) >= limit else None,
    }
    return _cap_response(result, "hits")


# ---------------------------------------------------------------------------
# Tool 6: get_body
# ---------------------------------------------------------------------------

async def get_body(
    body_handle: str,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
) -> Dict[str, Any]:
    """Fetch the full source body identified by *body_handle*.

    Optionally slice a sub-range with *line_start* / *line_end*.
    Path is validated against REPOS_BASE_PATH jail using check_path.

    Args:
        body_handle: Opaque handle returned by find_symbol, get_definition,
                     or search_semantic.
        line_start:  Optional first line of sub-range to return (1-indexed).
        line_end:    Optional last line of sub-range to return (inclusive).
    """
    meta = _resolve_body_handle(body_handle)
    if meta is None:
        return {"error": "body_handle expired or unknown", "body_handle": body_handle}

    repo = meta["repo"]
    file = meta["file"]
    stored_start = meta["line_start"]
    stored_end = meta["line_end"]

    # Try to read from disk first, using check_path for path jailing
    base = _get_repos_base()
    if base:
        root = Path(base) / repo
        rel = file.lstrip("/\\")
        try:
            safe_path = check_path(root / rel, root)
            with open(str(safe_path), encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()

            lo = (line_start - 1) if line_start else (stored_start - 1)
            hi = line_end if line_end else stored_end
            lo = max(0, lo)
            hi = min(len(all_lines), hi)
            content_lines = all_lines[lo:hi]
            raw = "".join(content_lines)
            truncated = len(raw) > _MAX_FIELD_CHARS
            content = _truncate_field(raw)
            return {
                "file": file,
                "line_start": lo + 1,
                "line_end": hi,
                "content": content,
                "source": "disk",
                "truncated": truncated,
                "next_offset": hi if truncated else None,
            }
        except PathJailError as exc:
            return {"error": f"Path traversal detected: {exc}", "body_handle": body_handle}
        except OSError as exc:
            logger.debug("get_body: disk read failed for %s/%s: %s", repo, file, exc)

    # Fall back to DB body column
    row = await _db_get_definition(meta.get("symbol_id", ""))
    if row and row.get("body"):
        body_text = row["body"]
        truncated = len(body_text) > _MAX_FIELD_CHARS
        return {
            "file": file,
            "line_start": stored_start,
            "line_end": stored_end,
            "content": _truncate_field(body_text),
            "source": "index",
            "truncated": truncated,
            "next_offset": None,
        }

    # No data available
    return {
        "error": "Body not available — disk and index both unreachable",
        "body_handle": body_handle,
        "file": file,
    }


# ===========================================================================
# Deprecated backwards-compat wrappers (Phase 0 / old 5-tool surface)
# ===========================================================================

async def search_code(
    query: str,
    repo: Optional[str] = None,
    limit: int = 5,
    language: Optional[str] = None,
    bm25_weight: float = 0.3,
) -> Dict[str, Any]:
    """[DEPRECATED] Use search_semantic instead.

    search_code previously returned full chunk bodies which wastes tokens.
    Forwards to search_semantic; the language and bm25_weight args are ignored.
    """
    warnings.warn(
        "search_code is deprecated; use search_semantic. "
        "search_code returns full chunk bodies which wastes tokens.",
        DeprecationWarning,
        stacklevel=2,
    )
    return await search_semantic(query=query, repo=repo, limit=limit)


async def get_function(
    name: str,
    repo: Optional[str] = None,
    file: Optional[str] = None,
) -> Dict[str, Any]:
    """[DEPRECATED] Use find_symbol + get_definition instead."""
    warnings.warn(
        "get_function is deprecated; use find_symbol() then get_definition(). "
        "get_function performs a name lookup and returns the first match.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Step 1: find by name
    found = await find_symbol(name=name, kind="function", repo=repo, limit=5)
    candidates = found.get("candidates", [])
    if not candidates:
        return {"error": f"Function '{name}' not found", "name": name}

    # Step 2: prefer file match if specified
    if file:
        for c in candidates:
            if file in c.get("file", ""):
                return await get_definition(c["symbol_id"])
    return await get_definition(candidates[0]["symbol_id"])


async def get_recent_changes(
    file_path: str,
    repo: str,
    days: int = 7,
    max_commits: int = 10,
) -> Dict[str, Any]:
    """Return recent git log entries for a file inside a local repository.

    All file access is validated against the REPOS_BASE_PATH jail via check_path.

    Args:
        file_path:   Path relative to the repository root.
        repo:        Repository name (must exist under REPOS_BASE_PATH).
        days:        How many days of history to fetch.
        max_commits: Maximum number of commits to return (capped at 50).
    """
    max_commits = min(max_commits, _MAX_ROWS)
    root = Path(_repos_root()) / repo
    try:
        safe_path = check_path(root / file_path, root)
    except PathJailError as exc:
        return {"error": str(exc)}

    if not root.exists():
        return {"error": f"Repository '{repo}' not found at {root}"}

    def _run_git() -> List[Dict[str, str]]:
        result = subprocess.run(
            [
                "git", "-C", str(root),
                "log", f"--since={days} days ago",
                f"-n{max_commits}",
                "--pretty=format:%H|%an|%ad|%s",
                "--date=short",
                "--", str(safe_path.relative_to(root)),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash":    parts[0][:12],
                    "author":  parts[1],
                    "date":    parts[2],
                    "message": parts[3],
                })
        return commits

    loop = asyncio.get_event_loop()
    try:
        commits = await loop.run_in_executor(None, _run_git)
    except subprocess.TimeoutExpired:
        return {"error": "git log timed out"}
    except Exception as exc:
        return {"error": str(exc)}

    result: Dict[str, Any] = {
        "repo":     repo,
        "file":     file_path,
        "days":     days,
        "commits":  commits,
        "truncated": len(commits) >= max_commits,
        "next_offset": max_commits if len(commits) >= max_commits else None,
    }
    return _cap_response(result, "commits")


async def get_file_context(
    file_path: str,
    repo: str,
    line_start: int,
    line_end: int,
) -> Dict[str, Any]:
    """Read specific lines from a source file inside a local repository.

    Path is validated against REPOS_BASE_PATH jail via check_path.
    Hard caps: 200 lines max per request; full response capped at 100 KB.

    Args:
        file_path:  Relative path inside the repository.
        repo:       Repository name under REPOS_BASE_PATH.
        line_start: First line to return (1-indexed).
        line_end:   Last line to return (inclusive).
    """
    root = Path(_repos_root()) / repo
    try:
        safe_path = check_path(root / file_path, root)
    except PathJailError as exc:
        return {"error": str(exc)}

    if not safe_path.exists():
        return {"error": f"File not found: {file_path}"}

    line_start = max(1, line_start)
    line_end = min(line_end, line_start + 199)  # cap at 200 lines

    try:
        lines = safe_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        selected = lines[line_start - 1: line_end]
    except OSError as exc:
        return {"error": str(exc)}

    actual_end = line_start + len(selected) - 1
    full_content = "\n".join(selected)
    truncated = len(full_content) > _MAX_FIELD_CHARS
    content = _truncate_field(full_content)

    result: Dict[str, Any] = {
        "repo":       repo,
        "file":       file_path,
        "line_start": line_start,
        "line_end":   actual_end,
        "content":    content,
        "truncated":  truncated,
        "next_offset": actual_end if truncated else None,
    }
    return _cap_response(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Phase 1 slim surface
    "find_symbol",
    "get_definition",
    "get_references",
    "get_callers",
    "search_semantic",
    "get_body",
    # Deprecated wrappers
    "search_code",
    "get_function",
    "get_recent_changes",
    "get_file_context",
    # Helpers (exported for testing)
    "_make_body_handle",
    "_resolve_body_handle",
    "_truncate_field",
    "_cap_response",
    "_verify_on_disk",
    "_assign_confidence",
    "_db_find_symbol",
    "_db_get_definition",
    "_db_get_references",
    "_db_get_callers",
    "_db_search_semantic",
    "_BODY_HANDLE_CACHE",
    "_MAX_ROWS",
    "_MAX_FIELD_CHARS",
    "_MAX_RESPONSE_BYTES",
    "_SNIPPET_CAP",
    "_BODY_HANDLE_MAX",
]
