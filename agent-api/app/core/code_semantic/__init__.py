"""ONNX code-embedding semantic search.

A self-contained, Bedrock-independent embedding path for concept-level code
search (the gap left by codegraph's empty ``search_semantic`` random-indexing
leg). Runs a small quantized sentence-transformer (bge-small-en-v1.5 by default,
nomic-embed-text-v1.5 optional) on CPU via onnxruntime, with a SHA256(model +
content) SQLite cache so re-embedding unchanged code is free.

Public surface:

* :class:`~app.core.code_semantic.embedder.CodeEmbedder` — lazy, thread-safe
  encoder (``embed_documents`` / ``embed_query``), transparently cached.
* :class:`~app.core.code_semantic.embedding_cache.EmbeddingCache` — the
  SHA256-keyed float32 blob cache.
* :data:`~app.core.code_semantic.models.MODEL_REGISTRY` — supported models.
* :func:`~app.core.code_semantic.provision.ensure_model` — offline-first model
  provisioning (urllib fetch past the corp TLS wall, into the data volume).

Everything is import-safe without ``onnxruntime``/``tokenizers`` installed: the
heavy deps are imported lazily inside :class:`CodeEmbedder`, so this package can
be imported (and the cache used) in a core-only environment.
"""
from __future__ import annotations

from app.core.code_semantic.models import MODEL_REGISTRY, EmbeddingModelSpec

__all__ = ["MODEL_REGISTRY", "EmbeddingModelSpec"]
