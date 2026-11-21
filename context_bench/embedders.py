"""Embedding clients. The embedder is swappable and recorded per run (RunResult.embedder)."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

BATCH = 32


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class OpenAICompatEmbedder:
    """Any /v1/embeddings endpoint. Returns L2-normalised float32 rows, so a dot product is cosine."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 300.0):
        self.name = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            resp = self._http.post("/v1/embeddings", json={"model": self.name, "input": texts[i:i + BATCH]})
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            rows += [d["embedding"] for d in data]
        v = np.asarray(rows, dtype=np.float32).reshape(len(texts), -1)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


class CachedEmbedder:
    """Wraps an embedder with an on-disk cache keyed by content hash: one .npy per (embedder, text),
    under cache_dir/<embedder name>/. Repo chunks are embedded once per content, whatever the run."""

    def __init__(self, inner: Embedder, cache_dir):
        self.inner, self.name = inner, inner.name
        self.dir = Path(cache_dir) / inner.name.replace("/", "_").replace(":", "_")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()      # sweep workers share one instance

    def _path(self, text: str) -> Path:
        return self.dir / (hashlib.sha256(text.encode()).hexdigest()[:32] + ".npy")

    def _save(self, path: Path, v: np.ndarray) -> None:
        """Write to a temp file and rename, so a reader never sees a half-written vector."""
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            np.save(f, v)
        os.replace(tmp, path)

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:     # one embedding pass at a time: no duplicate work on a cold cache
            cached = {t: np.load(self._path(t)) for t in set(texts) if self._path(t).is_file()}
            missing = [t for t in dict.fromkeys(texts) if t not in cached]
            if missing:
                for t, v in zip(missing, self.inner.embed(missing)):
                    self._save(self._path(t), v)
                    cached[t] = v
        return np.stack([cached[t] for t in texts]) if texts else np.zeros((0, 0), dtype=np.float32)
