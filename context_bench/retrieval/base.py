"""The retrieval interface. Any library can be plugged in by implementing it."""
from __future__ import annotations

from typing import Protocol, Sequence

from ..types import Chunk


class Retriever(Protocol):
    name: str

    def index(self, ids: Sequence[str], texts: Sequence[str]) -> None:
        """Replace the index with these documents."""

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Top-k (doc_id, score), best first."""


def chunk_text(c: Chunk) -> str:
    """What a function/class is indexed by (L5, validate-retrieval): path, qualname, then source."""
    return f"{c.path}\n{c.qualname}\n{c.source}"
