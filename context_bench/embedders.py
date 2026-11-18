"""Embedding clients. The embedder is swappable and recorded per run (RunResult.embedder)."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class OpenAICompatEmbedder:
    """Any /v1/embeddings endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None):
        raise NotImplementedError

    def embed(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError


class CachedEmbedder:
    """Wraps an embedder with an on-disk cache keyed by content hash."""

    def __init__(self, inner: Embedder, cache_dir):
        raise NotImplementedError

    def embed(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError
