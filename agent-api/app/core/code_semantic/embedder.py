"""ONNX code embedder — lazy, thread-safe, cached.

Wraps onnxruntime + the HuggingFace ``tokenizers`` fast tokenizer behind a small
``embed_documents`` / ``embed_query`` surface. The heavy deps and the model
session are loaded lazily on first use (and guarded by a lock), so importing this
module is free and safe in a core-only environment.

Pooling and task prefixes follow the model spec (bge=CLS no-prefix docs; nomic=
mean pooling with ``search_query:`` / ``search_document:``). Every vector is
served through :class:`EmbeddingCache`, so unchanged inputs never hit the model.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence

from app.core.code_semantic.embedding_cache import EmbeddingCache
from app.core.code_semantic.models import EmbeddingModelSpec, get_model_spec
from app.core.code_semantic.provision import ensure_model, verify_model_files

logger = logging.getLogger(__name__)


class CodeEmbedder:
    """CPU ONNX sentence embedder with a SHA256(model+content) cache.

    Args:
        models_root: Base dir holding provisioned model subdirs.
        cache_db: Path to the SQLite embedding cache.
        model_key: Registry key (defaults to registry default).
        allow_download: Fetch the model over unverified TLS if missing.
        batch_size: Max texts per ONNX forward pass.
    """

    def __init__(
        self,
        models_root: str,
        cache_db: str,
        model_key: Optional[str] = None,
        allow_download: bool = False,
        batch_size: int = 32,
    ) -> None:
        self.spec: EmbeddingModelSpec = get_model_spec(model_key)
        self.models_root = models_root
        self.allow_download = allow_download
        self.batch_size = max(1, batch_size)
        self.cache = EmbeddingCache(cache_db)

        self._lock = threading.Lock()
        self._sess = None  # onnxruntime.InferenceSession
        self._tok = None  # tokenizers.Tokenizer
        self._input_names: set[str] = set()
        self._loaded = False

    @property
    def dim(self) -> int:
        return self.spec.dim

    @property
    def model_key(self) -> str:
        return self.spec.key

    # ── lazy model load ──────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            import os

            import numpy as np  # noqa: F401 — surface a clear error early
            import onnxruntime as ort
            from tokenizers import Tokenizer

            mdir = ensure_model(
                self.models_root, self.spec.key, allow_download=self.allow_download
            )
            # Refuse to load a vendored/mounted file that differs from its pin.
            verify_model_files(self.models_root, self.spec)
            tok = Tokenizer.from_file(os.path.join(mdir, "tokenizer.json"))
            tok.enable_truncation(max_length=self.spec.max_length)
            self._tok = tok

            so = ort.SessionOptions()
            # Indexing a repo is embedding-bound. Give ONNX a few cores (2 was far
            # too slow) but cap HARD at 4 AND at most half the box, so a background
            # index build can never peg every core and starve the app's request
            # loop. Measured: at 4 threads on a 14-core host the API stays at
            # ~10ms and container RAM ~0.6GB during a full build. Override via
            # ONNX_INTRA_THREADS.
            default_threads = max(1, min(4, (os.cpu_count() or 4) // 2))
            so.intra_op_num_threads = int(
                os.environ.get("ONNX_INTRA_THREADS", str(default_threads))
            )
            so.inter_op_num_threads = 1
            # Keep memory FLAT over a long index build. With the CPU arena enabled,
            # a full 23k-node cold index climbs to multiple GB of RSS (measured:
            # 3.6GB and rising) and would OOM on a smaller host; disabling it holds
            # RSS ~flat (~1.3GB regardless of corpus size). We trade some indexing
            # speed for a hard memory bound — the right call since the build is a
            # one-time background task and crash-safety is the priority. (Do NOT
            # re-enable without re-testing a large cold index end to end.)
            so.enable_cpu_mem_arena = False
            self._sess = ort.InferenceSession(
                os.path.join(mdir, self.spec.onnx_file),
                sess_options=so,
                providers=["CPUExecutionProvider"],
            )
            self._input_names = {i.name for i in self._sess.get_inputs()}
            self._loaded = True
            logger.info(
                "CodeEmbedder loaded model=%s dim=%d pooling=%s inputs=%s",
                self.spec.key, self.spec.dim, self.spec.pooling,
                sorted(self._input_names),
            )

    # ── forward pass (uncached) ──────────────────────────────────────────────
    def _forward(self, texts: Sequence[str]) -> "list[list[float]]":
        import numpy as np

        self._ensure_loaded()
        encs = [self._tok.encode(t) for t in texts]
        maxlen = max((len(e.ids) for e in encs), default=1)
        ids = np.zeros((len(encs), maxlen), dtype=np.int64)
        mask = np.zeros((len(encs), maxlen), dtype=np.int64)
        for i, e in enumerate(encs):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.attention_mask)] = e.attention_mask

        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        feed = {k: v for k, v in feed.items() if k in self._input_names}

        last_hidden = self._sess.run(None, feed)[0]  # (B, T, H)

        if self.spec.pooling == "mean":
            m = mask[:, :, None].astype(np.float32)
            summed = (last_hidden * m).sum(axis=1)
            counts = np.clip(m.sum(axis=1), 1e-9, None)
            pooled = summed / counts
        else:  # cls
            pooled = last_hidden[:, 0]

        if self.spec.normalize:
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = pooled / np.clip(norms, 1e-9, None)
        return pooled.astype(np.float32).tolist()

    # ── cached batch embed ───────────────────────────────────────────────────
    def _embed_cached(self, texts: Sequence[str], prefix: str) -> List[List[float]]:
        if not texts:
            return []
        # Prefix is applied to the *encoded* text and is part of the cache key,
        # so a query and a document with identical raw text don't collide.
        prepared = [prefix + t for t in texts]
        cached = self.cache.get_batch(self.spec.key, prepared)

        missing = [p for p in prepared if p not in cached]
        # Dedup missing while preserving first-seen order.
        seen: set[str] = set()
        todo = [p for p in missing if not (p in seen or seen.add(p))]

        for i in range(0, len(todo), self.batch_size):
            batch = todo[i : i + self.batch_size]
            vecs = self._forward(batch)
            self.cache.put_batch(self.spec.key, zip(batch, vecs))
            for t, v in zip(batch, vecs):
                cached[t] = v

        return [cached[p] for p in prepared]

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed corpus documents (uses the model's doc prefix, if any)."""
        return self._embed_cached(texts, self.spec.doc_prefix)

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query (uses the model's query prefix, if any)."""
        return self._embed_cached([text], self.spec.query_prefix)[0]
