"""Concept-level semantic code search over codegraph's symbol nodes.

Fills the gap left by codegraph's empty ``search_semantic`` (its zero-dependency
random-indexing leg returns nothing useful). Corpus = the code entities codegraph
already extracted per project (functions/methods/classes with file+line+signature);
we embed a compact text per entity with :class:`CodeEmbedder`, hold the vectors in
a per-process index, and answer queries by cosine KNN.

The embedding cache (SHA256(model+content)) makes re-indexing an unchanged repo
nearly free — only new/changed entity texts hit the model. The in-memory index is
rebuilt lazily when the project DB's change-token moves.

This is a plain service (no tool/agent wiring) so it can be exercised directly and
reused by whichever surface consumes it (a codegraph search tool, an API, an eval).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.code_semantic.embedder import CodeEmbedder

logger = logging.getLogger(__name__)


@dataclass
class _ProjectIndex:
    signature: str
    matrix: Any  # np.ndarray (N, dim), row-normalized
    meta: List[Dict[str, Any]]


#: Substrings that mark a file as a test / build-output artifact. Mirrors the
#: codegraph C engine's ``cg_search_path_is_test`` so both search paths agree.
_TEST_PATH_MARKERS = (
    "/tests/", "/test/", "/__tests__/", "/obj/", "/coverlet/", "node_modules/",
    ".tests.", "tests.cs", "_test.", ".spec.", ".test.",
)
_TEST_PATH_PREFIXES = ("tests/", "test/", "obj/")

#: Query tokens that signal the user actually WANTS tests (write/find test cases,
#: specs, mocks, fixtures). When present we don't down-rank tests.
_TEST_INTENT_TOKENS = (
    "test", "tests", "unit test", "spec", "mock", "fixture", "assert",
    "test case", "test cases", "should ", "arrange", "xunit", "nunit", "pytest",
)


def is_test_path(rel: str) -> bool:
    """True if a file path looks like a test / build-output file."""
    if not rel:
        return False
    low = rel.lower().replace("\\", "/")
    if any(m in low for m in _TEST_PATH_MARKERS):
        return True
    return any(low.startswith(p) for p in _TEST_PATH_PREFIXES)


def query_wants_tests(query: str) -> bool:
    """Heuristic: does the query express test-writing / test-finding intent?"""
    low = f" {query.lower()} "
    return any(tok in low for tok in _TEST_INTENT_TOKENS)


def _embed_text(node: Dict[str, Any], body: str = "") -> str:
    """Compose the text embedded for one code entity.

    A small identity header (kind/name/qualified-name/signature/summary) followed
    by the entity's **actual source body** when available. Embedding the real code
    — not just the name — is the single biggest accuracy lever (mirrors
    code-context-engine, which embeds the AST-chunk content): a concept query then
    matches on what the code *does*, so config/test entities whose names merely
    echo the query no longer dominate. The header stays first so identity signal
    survives truncation of a long body.
    """
    parts = [f"{node.get('kind','')} {node.get('name','')}".strip()]
    qn = node.get("qualified_name") or ""
    if qn and qn != node.get("name"):
        parts.append(qn)
    sig = node.get("signature") or ""
    if sig:
        parts.append(sig)
    summary = node.get("summary") or ""
    if summary:
        parts.append(summary[:400])
    if body:
        parts.append(body)
    return "\n".join(p for p in parts if p)


class CodeSemanticSearch:
    """Per-process semantic search over codegraph projects, backed by ONNX embeds."""

    def __init__(self, embedder: Optional[CodeEmbedder] = None) -> None:
        self._embedder = embedder
        self._indexes: Dict[str, _ProjectIndex] = {}
        self._lock = threading.Lock()
        # Projects whose index is being built off the request thread → so a query
        # never blocks minutes on a first full-corpus embed. Maps project → the
        # DB signature being built (to detect a stale in-flight build).
        self._building: Dict[str, str] = {}

    @staticmethod
    def _read_body(root: Optional[str], node: Dict[str, Any],
                   file_cache: Dict[str, List[str]], max_chars: int) -> str:
        """Read a node's source slice ``[start_line:end_line]``, bounded.

        Memory-safe: nodes are processed in file order, so we keep only the
        *current* file's lines resident (single-entry cache) — reading a new file
        evicts the previous one. This bounds peak RAM to one source file instead
        of holding the whole repo's source in memory during a big index build.
        Missing files / bad ranges degrade to an empty body (header still embeds).
        Never raises.
        """
        if not root or max_chars <= 0:
            return ""
        rel = node.get("file") or ""
        s, e = node.get("line_start"), node.get("line_end")
        if not rel or not isinstance(s, int) or not isinstance(e, int) or e < s:
            return ""
        import os

        lines = file_cache.get(rel)
        if lines is None:
            file_cache.clear()  # evict the previous file — keep only one resident
            path = os.path.join(root, rel)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = []
            file_cache[rel] = lines
        if not lines:
            return ""
        # codegraph lines are 1-based, inclusive.
        body = "\n".join(lines[max(0, s - 1): e])
        return body[:max_chars]

    # ── embedder (lazy, from settings) ───────────────────────────────────────
    @property
    def embedder(self) -> CodeEmbedder:
        if self._embedder is None:
            self._embedder = CodeEmbedder(
                models_root=settings.code_semantic_models_root,
                cache_db=settings.code_semantic_cache_db,
                model_key=settings.code_semantic_model,
                allow_download=settings.code_semantic_allow_download,
                batch_size=settings.code_semantic_batch_size,
            )
        return self._embedder

    # ── index build ──────────────────────────────────────────────────────────
    def _build_index(self, project: str) -> Optional[_ProjectIndex]:
        import numpy as np

        from app.services import codegraph_admin

        sig = codegraph_admin.db_signature(project)
        if sig is None:
            logger.warning("code_semantic: project '%s' not indexed by codegraph", project)
            return None
        nodes = codegraph_admin.code_nodes_for_embedding(project)
        if not nodes:
            logger.warning("code_semantic: project '%s' has no code nodes", project)
            return None

        # Embed the real source body (biggest accuracy lever), read from disk once
        # per file. Cache-backed embeds keep the read cost one-time.
        root = codegraph_admin.project_root_path(project)
        max_chars = settings.code_semantic_body_max_chars
        file_cache: Dict[str, List[str]] = {}
        texts = [
            _embed_text(n, self._read_body(root, n, file_cache, max_chars))
            for n in nodes
        ]
        vecs = self.embedder.embed_documents(texts)  # cache-backed
        matrix = np.asarray(vecs, dtype=np.float32)
        # Vectors are already L2-normalized by the embedder; cosine == dot.
        meta = [
            {
                "name": n["name"],
                "qualified_name": n["qualified_name"],
                "kind": n["kind"],
                "file": n["file"],
                "line_start": n["line_start"],
                "line_end": n["line_end"],
                "signature": n["signature"],
                "is_test": is_test_path(n["file"]),
            }
            for n in nodes
        ]
        logger.info(
            "code_semantic: built index project=%s entities=%d dim=%d",
            project, len(meta), matrix.shape[1] if matrix.size else 0,
        )
        return _ProjectIndex(signature=sig, matrix=matrix, meta=meta)

    def _get_index(self, project: str) -> Optional[_ProjectIndex]:
        """Build (or rebuild) the index synchronously and cache it. Blocking."""
        from app.services import codegraph_admin

        cur = self._indexes.get(project)
        sig = codegraph_admin.db_signature(project)
        if cur is not None and sig == cur.signature:
            return cur
        with self._lock:
            cur = self._indexes.get(project)
            if cur is not None and sig == cur.signature:
                return cur
            idx = self._build_index(project)
            if idx is not None:
                self._indexes[project] = idx
            return idx

    def _bg_build(self, project: str, sig: str) -> None:
        """Build the index off the request thread; publish it when done."""
        try:
            idx = self._build_index(project)
            if idx is not None:
                with self._lock:
                    self._indexes[project] = idx
        except Exception:  # noqa: BLE001 — a failed build must not crash the thread
            logger.exception("code_semantic: background index build failed for %s", project)
        finally:
            with self._lock:
                self._building.pop(project, None)

    def _acquire_index_async(self, project: str) -> Optional[_ProjectIndex]:
        """Return a fresh cached index, or trigger a background build and None.

        Never blocks: if the index is missing or stale, ensure exactly one build
        thread is running for it and return None (caller reports "indexing").
        """
        from app.services import codegraph_admin

        sig = codegraph_admin.db_signature(project)
        if sig is None:
            return None  # not indexed by codegraph at all
        with self._lock:
            cur = self._indexes.get(project)
            if cur is not None and cur.signature == sig:
                return cur
            # Stale or missing → make sure a build for THIS signature is running.
            if self._building.get(project) != sig:
                self._building[project] = sig
                threading.Thread(
                    target=self._bg_build, args=(project, sig),
                    name=f"codesem-index-{project}", daemon=True,
                ).start()
                logger.info("code_semantic: started background index build for %s", project)
            return None

    def is_indexing(self, project: str) -> bool:
        """True if a background index build is currently in flight for *project*."""
        with self._lock:
            return project in self._building

    # ── query ────────────────────────────────────────────────────────────────
    def search(
        self,
        project: str,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        include_tests: Optional[bool] = None,
        blocking: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return the top-``k`` code entities most similar to ``query``.

        Each hit: ``{score, name, qualified_name, kind, is_test, file,
        line_start, line_end, signature}``.

        Indexing is **non-blocking by default**: if the project's index isn't
        ready, a build is kicked off in the background and ``[]`` is returned
        immediately (check :meth:`is_indexing` to tell "still building" from "no
        matches"). Pass ``blocking=True`` to build synchronously (tests/evals).

        Test handling (never destructive — tests stay searchable):
          * ``include_tests=True`` — no penalty; rank tests on pure similarity.
            Right when the user is writing/finding tests.
          * ``include_tests=False`` — apply the configured test penalty.
          * ``include_tests=None`` (default) — auto: penalize tests UNLESS the
            query itself expresses test intent (``query_wants_tests``).
        """
        import numpy as np

        idx = self._get_index(project) if blocking else self._acquire_index_async(project)
        if idx is None or idx.matrix.size == 0:
            return []
        q = np.asarray(self.embedder.embed_query(query), dtype=np.float32)
        scores = idx.matrix @ q  # cosine (both normalized)

        # Decide whether tests get penalized this query.
        if include_tests is True:
            penalize_tests = False
        elif include_tests is False:
            penalize_tests = True
        else:
            penalize_tests = not query_wants_tests(query)
        penalty = settings.code_semantic_test_penalty
        if penalize_tests and penalty < 1.0:
            is_test = np.array([m.get("is_test", False) for m in idx.meta])
            scores = np.where(is_test, scores * penalty, scores)

        k = min(max(1, top_k), len(idx.meta))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        out: List[Dict[str, Any]] = []
        for i in top:
            s = float(scores[i])
            if s < min_score:
                continue
            hit = dict(idx.meta[i])
            hit["score"] = round(s, 4)
            out.append(hit)
        return out

    def invalidate(self, project: Optional[str] = None) -> None:
        """Drop the cached in-memory index (per project or all)."""
        with self._lock:
            if project:
                self._indexes.pop(project, None)
            else:
                self._indexes.clear()


#: Process-wide singleton (index cache is per-process; safe to share).
_SINGLETON: Optional[CodeSemanticSearch] = None
_SINGLETON_LOCK = threading.Lock()


def get_code_semantic_search() -> CodeSemanticSearch:
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = CodeSemanticSearch()
    return _SINGLETON
