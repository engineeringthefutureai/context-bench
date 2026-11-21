"""Reference dense retriever: brute-force cosine similarity (a few thousand chunks need no
vector database). Also the yardstick for validating any third-party semantic retriever."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from ..embedders import Embedder


class EmbeddingRetriever:
    name = "embed"

    def __init__(self, embedder: Embedder, query_prefix: str = ""):
        """query_prefix goes in front of every query, not of documents (e.g. the instruction
        format some embedding models are trained with)."""
        self.embedder, self.query_prefix = embedder, query_prefix
        self.ids: list[str] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)

    def index(self, ids: Sequence[str], texts: Sequence[str]) -> None:
        self.ids = list(ids)
        self.vectors = self.embedder.embed(list(texts)) if texts else np.zeros((0, 0), dtype=np.float32)

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Top-k by cosine (vectors are normalised); ties keep index order."""
        if not self.ids:
            return []
        scores = self.vectors @ self.embedder.embed([self.query_prefix + query])[0]
        order = sorted(range(len(self.ids)), key=lambda i: (-scores[i], i))[:k]
        return [(self.ids[i], float(scores[i])) for i in order]
