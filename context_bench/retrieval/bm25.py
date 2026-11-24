"""Reference lexical retriever: Okapi BM25 over code-aware tokens. Also the yardstick for
validating any third-party lexical retriever."""
from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from ..tokenize import code_tokens


class BM25Retriever:
    name = "bm25"

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids: list[str] = []
        self.tf: list[Counter] = []
        self.lengths: list[int] = []
        self.idf: dict[str, float] = {}
        self.avg_len = 0.0

    def index(self, ids: Sequence[str], texts: Sequence[str], split_long: bool = False) -> None:
        self.ids = list(ids)
        self.tf = [Counter(code_tokens(t)) for t in texts]
        self.lengths = [sum(c.values()) for c in self.tf]
        self.avg_len = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        df = Counter(term for c in self.tf for term in c)
        n = len(self.ids)
        self.idf = {term: math.log((n - d + 0.5) / (d + 0.5) + 1) for term, d in df.items()}

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Top-k by BM25; documents sharing no query term are left out; ties keep index order."""
        terms = [t for t in set(code_tokens(query)) if t in self.idf]
        scored = []
        for i, tf in enumerate(self.tf):
            norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg_len or 1))
            s = sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in terms if t in tf)
            if s > 0:
                scored.append((-s, i))
        scored.sort()
        return [(self.ids[i], -neg) for neg, i in scored[:k]]
