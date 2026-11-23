"""Reference dense retriever: brute-force cosine similarity (a few thousand chunks need no
vector database). Also the yardstick for validating any third-party semantic retriever."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from ..embedders import Embedder


class EmbeddingRetriever:
    name = "embed"

    def __init__(self, embedder: Embedder, query_prefix: str = "", document_prefix: str = ""):
        """query_prefix goes in front of every query and document_prefix in front of every indexed text
        (the task formats embedding models are trained with: Qwen3-Embedding instructs queries only,
        EmbeddingGemma prefixes both sides)."""
        self.embedder, self.query_prefix, self.document_prefix = embedder, query_prefix, document_prefix
        self.ids: list[str] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)

    def index(self, ids: Sequence[str], texts: Sequence[str]) -> None:
        self.ids = list(ids)
        self.vectors = (self.embedder.embed([self.document_prefix + t for t in texts]) if texts
                        else np.zeros((0, 0), dtype=np.float32))

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Top-k by cosine (vectors are normalised); ties keep index order."""
        if not self.ids:
            return []
        # Queries skip the on-disk cache (a cached query would hide the embedder's real cost); the
        # documents' vectors stand for an index built ahead of time, which is how RAG is deployed.
        embedder = getattr(self.embedder, "inner", self.embedder)
        scores = self.vectors @ embedder.embed([self.query_prefix + query])[0]
        order = sorted(range(len(self.ids)), key=lambda i: (-scores[i], i))[:k]
        return [(self.ids[i], float(scores[i])) for i in order]
