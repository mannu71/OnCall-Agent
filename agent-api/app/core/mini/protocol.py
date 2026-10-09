"""The contract every local model implementation serves.

One spec implements one task. Callers never touch a model directly: they go
through :func:`app.core.mini.pool.MiniPool.run` with a fallback, so a missing,
slow or failing model degrades to today's behaviour instead of failing a run.
Text generation is deliberately absent: aux calls and RLM sub-calls go to
Bedrock through the LLM gateway.
"""
from __future__ import annotations

from typing import Dict, List, Protocol, Sequence, runtime_checkable

from app.core.privacy.classifier import Span


@runtime_checkable
class MiniModel(Protocol):
    name: str

    def classify(self, text: str, labels: Sequence[str]) -> Dict[str, float]:
        """Probability per label (sums to ~1)."""

    def extract(self, text: str, entity_types: Sequence[str]) -> List[Span]:
        """Entity spans with character offsets into *text*."""

    def embed(self, texts: Sequence[str], *, query: bool = False) -> List[List[float]]:
        """L2-normalised vectors, one per input."""

    def rerank(self, query: str, docs: Sequence[str]) -> List[float]:
        """Relevance score per doc (higher is better)."""
