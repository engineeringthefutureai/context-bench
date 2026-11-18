"""Reference dense retriever: brute-force cosine similarity (a few thousand chunks need no
vector database). Also the yardstick for validating any third-party semantic retriever."""
from __future__ import annotations

from typing import Sequence

from ..embedders import Embedder


class EmbeddingRetriever:
    name = "embed"

    def __init__(self, embedder: Embedder):
        raise NotImplementedError

    def index(self, ids: Sequence[str], texts: Sequence[str]) -> None:
        raise NotImplementedError

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        raise NotImplementedError
