"""SHA256(model + content) SQLite cache for embedding vectors.

Embedding a chunk of source is deterministic given (model, text), so we key each
vector by ``sha256(f"{model}:{text}")`` and store it as a packed ``float32`` blob.
Re-indexing a repo where most files are unchanged then costs one cheap SQLite
lookup per chunk instead of a model forward pass.

Design mirrors the well-trodden reference implementation: a single table, batch
get/put, WAL mode for concurrent readers, and a short-lived connection per call
(SQLite connections are not shareable across threads/loops, and opening one is
microseconds). A module-level lock serializes writers so concurrent indexers on
the same process don't trip ``database is locked``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import struct
import threading
from contextlib import closing, contextmanager
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    vector       BLOB NOT NULL,
    created_at   REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_embcache_model ON embedding_cache(model);
"""


def content_hash(model: str, text: str) -> str:
    """Stable cache key for ``(model, text)`` — ``sha256(f"{model}:{text}")``."""
    return hashlib.sha256(f"{model}:{text}".encode("utf-8")).hexdigest()


def _pack(vec: Sequence[float]) -> bytes:
    """Pack a float sequence into a little-endian float32 blob."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> List[float]:
    """Unpack a float32 blob back into a Python float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


class EmbeddingCache:
    """SQLite-backed vector cache keyed by SHA256(model + content)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> "Iterator[sqlite3.Connection]":
        """Open a short-lived connection and always close it.

        ``with sqlite3.connect(...) as conn`` only commits/rolls back — it does
        NOT close the connection, which leaks handles and keeps the DB file
        locked on Windows. We wrap it in ``closing`` and commit on clean exit.
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with closing(conn):
            yield conn
            conn.commit()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def get_batch(self, model: str, texts: Sequence[str]) -> Dict[str, List[float]]:
        """Return ``{text: vector}`` for the texts already cached under ``model``.

        Missing texts are simply absent from the result. Deduplicates keys so a
        repeated chunk is looked up once.
        """
        if not texts:
            return {}
        # hash → first text that produced it (for mapping results back).
        by_hash: Dict[str, str] = {}
        for t in texts:
            by_hash.setdefault(content_hash(model, t), t)
        hashes = list(by_hash.keys())

        out: Dict[str, List[float]] = {}
        with self._connect() as conn:
            # Chunk the IN() to stay under SQLite's variable limit (999).
            for i in range(0, len(hashes), 900):
                window = hashes[i : i + 900]
                placeholders = ",".join("?" * len(window))
                rows = conn.execute(
                    f"SELECT content_hash, vector FROM embedding_cache "
                    f"WHERE content_hash IN ({placeholders})",
                    window,
                ).fetchall()
                for h, blob in rows:
                    out[by_hash[h]] = _unpack(blob)
        return out

    def put_batch(
        self, model: str, items: Iterable[tuple[str, Sequence[float]]]
    ) -> int:
        """Insert/replace ``(text, vector)`` pairs for ``model``. Returns count."""
        rows = []
        for text, vec in items:
            rows.append((content_hash(model, text), model, len(vec), _pack(vec)))
        if not rows:
            return 0
        with _WRITE_LOCK, self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embedding_cache "
                "(content_hash, model, dim, vector) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def get(self, model: str, text: str) -> Optional[List[float]]:
        """Single-text convenience lookup."""
        res = self.get_batch(model, [text])
        return res.get(text)

    def put(self, model: str, text: str, vec: Sequence[float]) -> None:
        """Single-text convenience insert."""
        self.put_batch(model, [(text, vec)])

    def stats(self) -> Dict[str, int]:
        """Row counts per model — for diagnostics / the cache footer."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model, COUNT(*) FROM embedding_cache GROUP BY model"
            ).fetchall()
        return {model: n for model, n in rows}

    def clear(self, model: Optional[str] = None) -> int:
        """Delete all rows (or just one model's). Returns rows removed."""
        with _WRITE_LOCK, self._connect() as conn:
            if model:
                cur = conn.execute(
                    "DELETE FROM embedding_cache WHERE model=?", (model,)
                )
            else:
                cur = conn.execute("DELETE FROM embedding_cache")
            return cur.rowcount
