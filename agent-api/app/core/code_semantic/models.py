"""Registry of supported ONNX embedding models.

Each spec captures everything the embedder + provisioner need: the HuggingFace
repo to fetch from, the exact files (an ONNX graph plus, for models with external
weights, its ``.onnx_data`` sidecar), the pooling strategy, the output dimension,
and the optional task prefixes some models require on query vs. document text.

bge-small-en-v1.5 is the default and the only one validated end-to-end in this
environment (CLS pooling, 384-dim, quantized graph). nomic-embed-text-v1.5 is
registered with its correct mean-pooling + ``search_query:`` / ``search_document:``
prefixes for operators who provision it, but is not exercised here by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class EmbeddingModelSpec:
    """Immutable description of one ONNX embedding model."""

    #: Stable local id (also the cache-key namespace and on-disk dir name).
    key: str
    #: HuggingFace repo, e.g. ``onnx-community/bge-small-en-v1.5-ONNX``.
    hf_repo: str
    #: Files to fetch, repo-relative. The first ``*.onnx`` is the graph loaded by
    #: onnxruntime; a matching ``*.onnx_data`` (external weights) must be listed
    #: alongside it or the session fails to load. Flattened to basenames on disk.
    files: List[str]
    #: Basename of the ONNX graph within the model dir (post-flatten).
    onnx_file: str
    #: Output/embedding dimension.
    dim: int
    #: Pooling over the last hidden state: ``"cls"`` (token 0) or ``"mean"``
    #: (attention-mask-weighted average). bge=cls, nomic/e5=mean.
    pooling: str = "cls"
    #: L2-normalize the pooled vector (cosine-ready). Always true for our models.
    normalize: bool = True
    #: Max tokens per input (truncation length).
    max_length: int = 512
    #: Optional instruction prefix prepended to *query* text before encoding.
    query_prefix: str = ""
    #: Optional instruction prefix prepended to *document* text before encoding.
    doc_prefix: str = ""
    #: Extra notes (provenance / caveats).
    notes: str = ""
    #: HuggingFace revision to download from: a commit SHA or tag. ``main`` is a
    #: moving target, so a spec without a SHA must at least pin ``sha256``.
    revision: str = "main"
    #: Expected sha256 per remote file. A downloaded or vendored file whose hash
    #: differs is rejected (see ``provision.verify_model_files``).
    sha256: Dict[str, str] = field(default_factory=dict)

    def flat_files(self) -> Dict[str, str]:
        """Map each remote file to its flattened local basename."""
        import os

        return {remote: os.path.basename(remote) for remote in self.files}


#: bge-small-en-v1.5 — quantized (int8) graph + external weights sidecar.
#: Validated in-container: 384-dim, relevant > irrelevant on a code probe.
_BGE_SMALL = EmbeddingModelSpec(
    key="bge-small-en-v1.5",
    hf_repo="onnx-community/bge-small-en-v1.5-ONNX",
    files=[
        "onnx/model_quantized.onnx",
        "onnx/model_quantized.onnx_data",
        "tokenizer.json",
        "config.json",
    ],
    onnx_file="model_quantized.onnx",
    dim=384,
    pooling="cls",
    max_length=512,
    # bge query-side instruction (recommended by the model authors for retrieval).
    query_prefix="Represent this sentence for searching relevant passages: ",
    doc_prefix="",
    notes="Default. CLS pooling + L2 norm. Quantized graph (~33MB external weights).",
)

#: nomic-embed-text-v1.5 — mean pooling, task-prefixed, 768-dim (Matryoshka).
#: Registered for operators who provision it; not exercised by default here.
_NOMIC = EmbeddingModelSpec(
    key="nomic-embed-text-v1.5",
    hf_repo="nomic-ai/nomic-embed-text-v1.5",
    files=[
        "onnx/model_quantized.onnx",
        "tokenizer.json",
        "config.json",
    ],
    onnx_file="model_quantized.onnx",
    dim=768,
    pooling="mean",
    max_length=2048,
    query_prefix="search_query: ",
    doc_prefix="search_document: ",
    notes="Optional. Mean pooling + task prefixes. Self-contained quantized "
          "graph (~137MB, no external-weights sidecar). Provision separately.",
)

#: snowflake-arctic-embed-s — 384-dim, CLS pooling, self-contained quantized graph
#: (~34MB, same footprint/speed as bge-small) but a stronger MTEB retriever. The
#: accuracy upgrade that does NOT cost more index time — recommended over bge when
#: you want better results without slower indexing.
_ARCTIC_S = EmbeddingModelSpec(
    key="snowflake-arctic-embed-s",
    hf_repo="Snowflake/snowflake-arctic-embed-s",
    files=[
        "onnx/model_quantized.onnx",
        "tokenizer.json",
        "config.json",
    ],
    onnx_file="model_quantized.onnx",
    dim=384,
    pooling="cls",
    max_length=512,
    # arctic-embed prepends this instruction to queries (documents get none).
    query_prefix="Represent this sentence for searching relevant passages: ",
    doc_prefix="",
    notes="Recommended accuracy upgrade at bge-small speed/size (34MB, 384d, CLS).",
    # Hashes of the files vendored in agent-api/vendor/models (the shipped copy).
    sha256={
        "onnx/model_quantized.onnx":
            "f93ff225320628d2e88baf2a395cae791b0e3b27edf5c70bf7b312a4d3260c14",
        "tokenizer.json":
            "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854",
        "config.json":
            "4e519aa92ec40943356032afe458c8829d70c5766b109e4a57490b82f72dcfb7",
    },
)

#: jina-embeddings-v2-base-code — CODE-SPECIFIC (trained on 30 programming
#: languages + docstrings), 768-dim, mean pooling, ALiBi (8k ctx). Best accuracy
#: for code retrieval here, but ~3-4x slower to index than bge/arctic (~162MB).
_JINA_CODE = EmbeddingModelSpec(
    key="jina-embeddings-v2-base-code",
    hf_repo="jinaai/jina-embeddings-v2-base-code",
    files=[
        "onnx/model_quantized.onnx",
        "tokenizer.json",
        "config.json",
    ],
    onnx_file="model_quantized.onnx",
    dim=768,
    pooling="mean",
    max_length=1024,
    query_prefix="",
    doc_prefix="",
    notes="Code-specific (30 langs). Best code accuracy; ~3-4x slower index than bge.",
)

#: All supported models, keyed by :attr:`EmbeddingModelSpec.key`.
MODEL_REGISTRY: Dict[str, EmbeddingModelSpec] = {
    _BGE_SMALL.key: _BGE_SMALL,
    _ARCTIC_S.key: _ARCTIC_S,
    _JINA_CODE.key: _JINA_CODE,
    _NOMIC.key: _NOMIC,
}

#: Default model key when none is configured — arctic-embed-s (best accuracy in
#: the fast 384-dim/34MB tier; see config.code_semantic_model).
DEFAULT_MODEL_KEY = _ARCTIC_S.key


def get_model_spec(key: str | None) -> EmbeddingModelSpec:
    """Resolve a model key to its spec, defaulting when unknown/empty."""
    if key and key in MODEL_REGISTRY:
        return MODEL_REGISTRY[key]
    return MODEL_REGISTRY[DEFAULT_MODEL_KEY]
