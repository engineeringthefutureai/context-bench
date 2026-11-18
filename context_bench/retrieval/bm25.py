"""Reference lexical retriever: Okapi BM25 over code-aware tokens. Also the yardstick for
validating any third-party lexical retriever."""
from __future__ import annotations

from typing import Sequence


class BM25Retriever:
    name = "bm25"

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        raise NotImplementedError

    def index(self, ids: Sequence[str], texts: Sequence[str]) -> None:
        raise NotImplementedError

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        raise NotImplementedError
